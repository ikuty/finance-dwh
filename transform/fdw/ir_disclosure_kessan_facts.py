"""ir-disclosure-dlの決算短信PDFのテキスト(ir_disclosure_kessan_pdf.extract_textの
出力)から、サマリー情報の各数値を構造化する。

Stage1(ir_disclosure_kessan_pdf.py)の後段。ビジネスロジック(サブタイプ判定・
会計基準ごとの列の意味づけ・数値の正規化等)はすべてここに閉じ込める。

実機検証(2026-09-30、323件)で判明した重要な事実:

1. disclosure_kind=kessan_tanshinには、本体(サマリー情報を持つ決算短信そのもの)
   以外に最低4種類のサブタイプが混在する(323件中62件=19%)。
     - 訂正/再訂正(39件): 自由記述、財務数値テーブル無し。
     - 補足説明資料(1件): スライド形式、対象外(既存のスコープ決定通り)。
     - 公認会計士等による期中レビューの完了notice(4件): 1ページ目が別内容の
       カバーページで、本体は後続ページにずれる。しかも内容は「数値変更なし」
       （元のtanshinと同一データ）のため、別イベントとして重複取得するより
       対象外にする方が単純。
     - 決算短信の開示予定日・遅延に関するお知らせ(6件): 財務数値無し。
   いずれもタイトル文字列で機械的に判別できる(classify_titleを参照)。
2. 上記62件を除外した「本体」候補は、pdfplumber.extract_tables()換算で
   4〜6テーブルに98%収束する(残りはスキャンPDF等の真の例外)。
3. ただしextract_tables()自体は、提出企業のPDF生成ソフトウェアによって罫線検出が
   不安定で、同じ論理的な表(EPS実績等)が検出されないことがある(実機確認)。
   そのため本モジュールはextract_tables()を使わず、extract_text()のテキストを
   正規表現で解析する(テキストは全サンプルで一貫して整形されていることを確認済み)。

数値の正規化(jpx_stq_factsとの違い): jpx_stqはカンマ除去等の正規化をcleansed層に
委ねているが、この文書種別は「△」による負数表記・「－」による未開示表記という
日本の決算短信特有の記法を含み、これをtry_castできる形にするには解釈が必要なため、
本モジュール側で正規化する(カンマ除去・△→マイナス符号・－→NULL)。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, fields

# --- タイトル文字列からのサブタイプ・属性判定 -------------------------------------

_CORRECTION_KEYWORDS = ("訂正",)
_SUPPLEMENTARY_KEYWORDS = ("補足説明資料",)
_REVIEW_NOTICE_KEYWORDS = ("レビューの完了",)
_ACCOUNTING_STANDARD_BRACKETS = ("〔", "［", "[", "【")


def classify_title(title: str) -> str:
    """タイトル文字列からPDFのサブタイプを判定する。

    'genuine'(構造化対象の本体) / 'correction' / 'supplementary' /
    'review_notice' / 'schedule_notice' のいずれかを返す。本体のタイトルは
    必ず「〔日本基準〕」等の会計基準括弧を含む(実機確認、2026-09-30)。これが
    無いものは「決算短信の開示予定日に関するお知らせ」等の事務連絡。
    """
    if any(kw in title for kw in _CORRECTION_KEYWORDS):
        return "correction"
    if any(kw in title for kw in _SUPPLEMENTARY_KEYWORDS):
        return "supplementary"
    if any(kw in title for kw in _REVIEW_NOTICE_KEYWORDS):
        return "review_notice"
    if not any(b in title for b in _ACCOUNTING_STANDARD_BRACKETS):
        return "schedule_notice"
    return "genuine"


def parse_period_type(title: str) -> str | None:
    if "中間期" in title:
        return "q2_half"
    if "第１四半期" in title or "第1四半期" in title:
        return "q1"
    if "第２四半期" in title or "第2四半期" in title:
        return "q2_half"
    if "第３四半期" in title or "第3四半期" in title:
        return "q3"
    if "四半期" not in title:
        return "annual"
    return None


# 決算期末の年・月(「2026年５月期」全角/半角混在可、「令和８年10月期」元号)を
# タイトルから抽出する。「2026年5月8日期」のような月末以外が期末日の変則決算
# (実機確認、トーシンHD)は対象外(Noneを返す、既知の制限)。
_FYE_ERA_RE = re.compile(r"令和(?P<era_year>\d{1,2})年(?P<month>\d{1,2})月期")
_FYE_RE = re.compile(r"(?P<year>\d{4})年(?P<month>\d{1,2})月期")

# 決算期末からperiod_typeに応じて何ヶ月遡るか(四半期は決算期末からの逆算で求める。
# 実機確認2026-09-30の実データ3件で検証済み: 5月期Q1「2026年６月１日～2026年８月
# 31日」、1月期中間期「2026年２月１日～2026年７月31日」、8月期本決算
# 「2025年9月1日～2026年8月31日」。ニイタカ・coly・クラウディアHDそれぞれ参照)。
_PERIOD_TYPE_OFFSET_MONTHS = {"q1": 9, "q2_half": 6, "q3": 3, "annual": 0}


def parse_fiscal_year_end(title: str) -> tuple[int, int] | None:
    """タイトルから決算期末の西暦年・月を返す((year, month))。元号年は
    本文データ行と同じ変換規則(令和1年=2019年)。見つからなければNone。"""
    m = _FYE_ERA_RE.search(title)
    if m:
        return (2018 + int(m.group("era_year")), int(m.group("month")))
    m = _FYE_RE.search(title)
    if m:
        return (int(m.group("year")), int(m.group("month")))
    return None


def compute_period_end(fye_year: int, fye_month: int, period_type: str | None) -> str | None:
    """決算期末(fye_year年fye_month月の月末)とperiod_typeから、実際の期間末日
    (ISO形式文字列 YYYY-MM-DD)を計算する。"""
    if period_type not in _PERIOD_TYPE_OFFSET_MONTHS:
        return None
    offset = _PERIOD_TYPE_OFFSET_MONTHS[period_type]
    total_months = fye_year * 12 + (fye_month - 1) - offset
    year, month = divmod(total_months, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


# タイトルに「2024年度」とだけあり決算月が分からない会社(日本電信電話等、53件、
# 実機確認2026-11-12)の期間末日は、本文の「１．…の連結業績（2024年4月1日～2025年3月31日）」
# 見出しの終了日から取る。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_RANGE_END_RE = re.compile(r"[～~〜]\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def parse_period_end_from_text(lines: list[str]) -> str | None:
    """本文冒頭の「１．…（YYYY年M月D日～YYYY年M月D日）」見出しの終了日(ISO形式)を返す。"""
    for line in lines[:80]:
        s = line.strip().translate(_FULLWIDTH_DIGITS)
        if not re.match(r"^1[．.]", s):
            continue
        m = _RANGE_END_RE.search(s)
        if not m:
            continue
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def parse_consolidation(title: str) -> str | None:
    if "非連結" in title:
        return "non_consolidated"
    if "連結" in title:
        return "consolidated"
    return None


def parse_accounting_standard(title: str) -> str | None:
    if "IFRS" in title or "ＩＦＲＳ" in title:
        return "ifrs"
    if "米国基準" in title:
        return "us_gaap"
    if "日本基準" in title:
        return "jgaap"
    return None


# --- 数値トークンの正規化 ----------------------------------------------------------

_DASH_RE = re.compile(r"^[－―ー\-]{1,3}$")
_NUMERIC_CORE_RE = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^\d+(?:\.\d+)?$")


def _normalize_number(token: str) -> str | None:
    """「△1,234」→"-1234"、「－」→None、「1,234.5」→"1234.5"。
    数値として解釈できないトークンはNoneを返す。"""
    token = token.strip()
    if _DASH_RE.match(token):
        return None
    neg = token[:1] in "△▲▼"
    core = token.lstrip("△▲▼").replace(",", "")
    if not _NUMERIC_CORE_RE.match(core.replace(",", "")):
        return None
    return ("-" + core) if neg else core


# --- データ行の抽出 ------------------------------------------------------------

# 「2024年度」「2025年度（予想）」のように「期」でなく「度」で終わるラベルの会社もある
# (日本電信電話、実機確認2026-11-12)。
# 「2027年５月期第１四半期 6,334 5.5 585 0.5 ...」「2027年8月期（予想） － 5.00 ...」
# 「令和８年７月期 ...」のような、期ラベル+数値トークン列からなる行。元号年のデータ行は
# タイトルで「令和」を示した後、本文では「令和」を省略し「８年10月期...」のように
# 1〜2桁の年数字だけで始まることを実機確認済み(2026-09-30、株式会社キタック)。
_PERIOD_ROW_RE = re.compile(
    r"^(?P<label>\d{1,4}年\S*?[期度](?:\s*[（(]予想[）)])?)\s+"
    r"(?P<rest>[△▲▼0-9,.\s％\-－―ー]+)$"
)

# 業績予想セクションの「通期 27,500 11.7 ...」行。「通 期」のように字間が空く
# レイアウトも実機確認済み(2026-09-30、株式会社カラダノート)。
_FORECAST_ROW_RE = re.compile(r"^通\s*期\s+(?P<rest>[△▲▼0-9,.\s％\-－―ー]+)$")


def _tokenize_rest(rest: str) -> list[str | None]:
    return [_normalize_number(tok) for tok in rest.split()]


def _find_period_rows(lines: list[str], start: int, end: int) -> list[tuple[str, list[str | None]]]:
    """lines[start:end]の範囲で期ラベル行をすべて見つけ、(ラベル, 数値トークン列)の
    リストを返す(出現順=通常「当期, 前期」の順)。"""
    rows = []
    for line in lines[start:end]:
        m = _PERIOD_ROW_RE.match(line.strip())
        if m:
            rows.append((m.group("label"), _tokenize_rest(m.group("rest"))))
    return rows


def _find_section_bounds(lines: list[str]) -> dict[str, int]:
    """主要セクションの開始行インデックスを返す(見つからなければキー無し)。"""
    # 節番号は全角(「１．」「（２）」)と半角(「1.」「(2)」)の両方が実在する
    # (実機確認、2026-09-30、株式会社ハイレックスコーポレーションは半角表記)。
    # 全角数字[１-３]と半角数字[1-3]の両方を許容する。
    bounds: dict[str, int] = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if "bounds_1" not in bounds and re.match(r"^[１1][．.]", s):
            bounds["bounds_1"] = i
        elif "bounds_eps" not in bounds and (
            s.startswith("１株当たり") or s.startswith("1株当たり")
            or s.startswith("基本的１株当たり") or s.startswith("基本的1株当たり")
            or s.startswith("潜在株式調整後")
        ):
            # EPS見出し行は「潜在株式調整後\n１株当たり\n...」のように単独1語の場合と、
            # 「１株当たり 潜在株式調整後 自己資本 総資産 売上高」のようにROE等を含む
            # 結合見出し1行の場合がある(実機確認、2026-09-30、内田洋行)。前方一致で
            # 両方を拾う。
            bounds["bounds_eps"] = i
        elif "bounds_2" not in bounds and re.match(r"^[（(][２2][）)]", s):
            bounds["bounds_2"] = i
        elif "bounds_cf" not in bounds and "キャッシュ・フローの状況" in s:
            bounds["bounds_cf"] = i
        elif "bounds_dividend" not in bounds and "…" not in s and _DIVIDEND_HEADING_RE.match(s):
            bounds["bounds_dividend"] = i
        elif "bounds_3" not in bounds and re.match(r"^[３3][．.]", s):
            bounds["bounds_3"] = i
    return bounds


# --- 配当表(「配当の状況」) -------------------------------------------------------

# 見出しは「２．配当の状況」の他に「２ 配当の状況」「２．配当」、IFRSでNon-GAAPの節が
# 挟まる会社の「３．配当の状況」「４．配当の状況」がある(実機確認、2026-11-12、
# 取りこぼし26件)。節番号に依存せず、短い節見出し行の「配当(の状況)」で探す。
# 目次の行(「……… 3」)や本文(「配当金の支払額…」)は長さと「…」で除く。
_DIVIDEND_HEADING_RE = re.compile(r"^[0-9０-９]{1,2}[．.\s]\s*配当(?:の状況)?$")
_NEXT_SECTION_RE = re.compile(r"^[0-9０-９]{1,2}[．.\s]")
_DIVIDEND_SLOTS = ("q1", "q2", "q3", "q4", "total")
# 決算短信の種類ごとに、当期実績の表に現れる経過済みスロット数。
_ELAPSED_SLOTS = {"q1": 1, "q2_half": 2, "q3": 3, "annual": 5}


def _find_dividend_heading_candidates(lines: list[str]) -> list[int]:
    return [
        i for i, line in enumerate(lines)
        if "…" not in line and _DIVIDEND_HEADING_RE.match(line.strip())
    ]


def _tokenize_dividend(rest: str) -> list[str | None]:
    """配当表の数値トークン列。「－」は無配(0)として"0"にする。"""
    out: list[str | None] = []
    for tok in rest.split():
        out.append("0" if _DASH_RE.match(tok) else _normalize_number(tok))
    return out


def parse_dividend_table(lines: list[str], period_type: str | None) -> dict[str, str | None]:
    """「配当の状況」の表を、前期・当期・予想の各5スロット(15列)へ展開する。

    実機確認(2026-11-12、決算短信31,892件)で判明したレイアウトの規則:
      - 表に載らない(空欄の)セルは行のテキストから消え、「－」は残る。そのため位置は
        決算短信の種類(period_type)で決まる: 当期実績は経過済みスロットが左詰め
        (Q1=1個、中間=2個、Q3=3個、通期=5個)、予想は未経過スロットが右詰め
        (Q1=Q2〜合計の4個、中間=3個、Q3=2個、通期=5個)。5個そろう行は全スロット。
      - 通期の実績・予想の行は、合計の後ろに配当金総額・配当性向・純資産配当率が続く
        (先頭5個だけを使う)。
      - 前期行は5個以上あり、Q1〜期末の合計が「合計」列と一致するのが98.1%
        (一致しない行は空欄詰めや円・銭の分割でスロットがずれている。判定は後段で行う)。
    行の数が(実績2行・予想1行)または(実績2行・予想0行)でない会社(種類株、
    普通配当と特別配当の内訳行等)、レイアウトを解釈できない行は、その行をNULLのままにする。
    """
    out: dict[str, str | None] = {}
    for start in _find_dividend_heading_candidates(lines):
        end = next(
            (i for i in range(start + 1, min(len(lines), start + 40))
             if _NEXT_SECTION_RE.match(lines[i].strip())),
            min(len(lines), start + 40),
        )
        actual: list[list[str | None]] = []
        forecast: list[list[str | None]] = []
        for line in lines[start + 1:end]:
            m = _PERIOD_ROW_RE.match(line.strip())
            if not m:
                continue
            tokens = _tokenize_dividend(m.group("rest"))
            (forecast if "予想" in m.group("label") else actual).append(tokens)
        if len(actual) != 2 or len(forecast) > 1:
            continue

        def put(prefix: str, slots: list[str], tokens: list[str | None]) -> None:
            for slot, value in zip(slots, tokens):
                out[f"dividend_{prefix}_{slot}"] = value

        all_slots = list(_DIVIDEND_SLOTS)
        prior, cur = actual
        if len(prior) >= 5:
            put("prior", all_slots, prior[:5])
        n = _ELAPSED_SLOTS.get(period_type or "")
        if n is not None and (len(cur) == n or len(cur) == 5 or (n == 5 and len(cur) > 5)):
            put("cur", all_slots, cur[:5])
        if forecast:
            fc = forecast[0]
            if period_type == "annual":
                if len(fc) >= 5:
                    put("fcst", all_slots, fc[:5])
            elif 2 <= len(fc) <= 5:
                put("fcst", all_slots[5 - len(fc):], fc)
        break
    return out


# 会計基準ごとの「業績」セクションの列順(売上/営業利益の後に続く利益指標の意味)。
# JGAAPは4列(売上・営業利益・経常利益・純利益)、IFRSは6列(売上収益・営業利益・
# 税引前利益・当期利益・親会社の所有者に帰属する当期利益・当期包括利益合計額)
# であることを実機確認済み(2026-09-30)。
_RESULTS_COLUMNS_BY_STANDARD = {
    "jgaap": ["sales", "operating_income", "ordinary_income", "net_income"],
    "us_gaap": ["sales", "operating_income", "ordinary_income", "net_income"],
    "ifrs": [
        "sales", "operating_income", "income_before_tax", "_skip_net_income_total",
        "net_income", "_skip_comprehensive_income",
    ],
}

# ３．業績予想セクションはIFRSでも「当期利益(全体)」「当期包括利益合計額」を
# 含まない4列(売上収益・営業利益・税引前利益・親会社の所有者に帰属する当期利益)
# であることを実機確認済み(2026-09-30)。実績セクションの6列マッピングをそのまま
# 使うと列がずれてforecast_epsまで取れなくなるため、専用のマッピングを用いる。
_FORECAST_COLUMNS_BY_STANDARD = {
    "jgaap": ["sales", "operating_income", "ordinary_income", "net_income"],
    "us_gaap": ["sales", "operating_income", "ordinary_income", "net_income"],
    "ifrs": ["sales", "operating_income", "income_before_tax", "net_income"],
}


@dataclass
class FactRow:
    """landing.ir_disclosure_kessan_facts の1行(1PDF)。全列 str|None
    (数値は正規化済みの文字列、最終的な型付けはcleansed層のtry_cast)。"""

    docid: str
    edinet_code: str | None
    sec_code: str | None
    extraction_status: str
    period_type: str | None
    consolidation: str | None
    accounting_standard: str | None
    fiscal_period_label: str | None
    period_end: str | None

    sales: str | None
    sales_prior: str | None
    sales_yoy_pct: str | None
    operating_income: str | None
    operating_income_prior: str | None
    operating_income_yoy_pct: str | None
    ordinary_income: str | None
    ordinary_income_prior: str | None
    ordinary_income_yoy_pct: str | None
    income_before_tax: str | None
    income_before_tax_prior: str | None
    income_before_tax_yoy_pct: str | None
    net_income: str | None
    net_income_prior: str | None
    net_income_yoy_pct: str | None

    eps_actual: str | None
    eps_actual_prior: str | None
    eps_diluted_actual: str | None
    eps_diluted_actual_prior: str | None

    total_assets: str | None
    total_assets_prior: str | None
    net_assets: str | None
    net_assets_prior: str | None
    equity_ratio: str | None
    equity_ratio_prior: str | None

    cf_operating: str | None
    cf_investing: str | None
    cf_financing: str | None
    cf_cash_end: str | None

    forecast_sales: str | None
    forecast_sales_yoy_pct: str | None
    forecast_operating_income: str | None
    forecast_operating_income_yoy_pct: str | None
    forecast_ordinary_income: str | None
    forecast_ordinary_income_yoy_pct: str | None
    forecast_income_before_tax: str | None
    forecast_income_before_tax_yoy_pct: str | None
    forecast_net_income: str | None
    forecast_net_income_yoy_pct: str | None
    forecast_eps: str | None
    forecast_period_end: str | None

    # 配当表(「配当の状況」): 前期実績・当期実績(経過分のみ)・予想の各行×5スロット
    # (第1四半期末・第2四半期末・第3四半期末・期末・合計)。「－」は0(無配)、
    # 表に無い(空欄)スロットとレイアウトを解釈できない行はNULL。
    # 詳細はparse_dividend_table参照(2026-11-12追加、#108)。
    dividend_prior_q1: str | None
    dividend_prior_q2: str | None
    dividend_prior_q3: str | None
    dividend_prior_q4: str | None
    dividend_prior_total: str | None
    dividend_cur_q1: str | None
    dividend_cur_q2: str | None
    dividend_cur_q3: str | None
    dividend_cur_q4: str | None
    dividend_cur_total: str | None
    dividend_fcst_q1: str | None
    dividend_fcst_q2: str | None
    dividend_fcst_q3: str | None
    dividend_fcst_q4: str | None
    dividend_fcst_total: str | None


FACT_COLUMNS = [f.name for f in fields(FactRow)]


def _empty_row(
    docid: str, edinet_code: str | None, sec_code: str | None, extraction_status: str
) -> FactRow:
    _explicit = {"docid", "edinet_code", "sec_code", "extraction_status"}
    kwargs = {f.name: None for f in fields(FactRow) if f.name not in _explicit}
    return FactRow(
        docid=docid, edinet_code=edinet_code, sec_code=sec_code,
        extraction_status=extraction_status, **kwargs,
    )


def _assign_results_row(
    out: dict[str, str | None], standard: str, rest: list[str | None], suffix: str
) -> None:
    columns = _RESULTS_COLUMNS_BY_STANDARD.get(standard, _RESULTS_COLUMNS_BY_STANDARD["jgaap"])
    idx = 0
    for col in columns:
        value = rest[idx] if idx < len(rest) else None
        pct = rest[idx + 1] if idx + 1 < len(rest) else None
        idx += 2
        if col.startswith("_skip"):
            continue
        out[f"{col}{suffix}"] = value
        if suffix == "":
            out[f"{col}_yoy_pct"] = pct


def run(
    text: str, docid: str, title: str, edinet_code: str | None = None, sec_code: str | None = None
) -> FactRow:
    """テキスト全体からFactRowを組み立てる便利関数。edinet_code/sec_codeは
    ir-disclosure-dlのメタデータJSONから渡される企業識別子で、この関数自身は
    解釈しない(素通し)。EDINET側のmart（mart__jpx_edinet__disclosed_fundamentals
    等）とedinet_codeで結合できるようにするため(2026-10-03追加)。"""
    status = classify_title(title)
    if status != "genuine":
        return _empty_row(docid, edinet_code, sec_code, status)

    lines = text.split("\n")
    bounds = _find_section_bounds(lines)

    period_type = parse_period_type(title)
    consolidation = parse_consolidation(title)
    accounting_standard = parse_accounting_standard(title) or "jgaap"
    fye = parse_fiscal_year_end(title)
    period_end = compute_period_end(fye[0], fye[1], period_type) if fye else None
    if period_end is None:
        period_end = parse_period_end_from_text(lines)
    # forecast_epsが指す対象期間(forecast_period_end)は、当該開示自身の期間
    # (period_end)とは異なる(2026-10-03にmart設計で判明、実データで検証済み)。
    # 四半期決算短信(q1/q2_half/q3)の「３．業績予想」は今期進行中の通期予想
    # (タイトルと同じFYE)だが、本決算(annual)の予想は次期の通期予想(FYE+1年)
    # になる(実機確認: クラウディアHD「2026年8月期」本体の予想セクションは
    # 「2027年8月期の連結業績予想」)。
    if fye:
        forecast_fye_year = fye[0] + 1 if period_type == "annual" else fye[0]
        forecast_period_end = compute_period_end(forecast_fye_year, fye[1], "annual")
    else:
        forecast_period_end = None

    out: dict[str, str | None] = {}

    # 業績実績(当期・前期)
    start1 = bounds.get("bounds_1", 0)
    end1 = bounds.get("bounds_eps", bounds.get("bounds_2", len(lines)))
    results_rows = _find_period_rows(lines, start1, end1)
    fiscal_period_label = results_rows[0][0] if results_rows else None
    if results_rows:
        _assign_results_row(out, accounting_standard, results_rows[0][1], "")
    if len(results_rows) >= 2:
        _assign_results_row(out, accounting_standard, results_rows[1][1], "_prior")

    # EPS実績(当期・前期): 「円 銭」行の直後、先頭2トークンのみを使う
    # (ROE等の追加列を持つ企業もあるため、先頭2つ=EPS実績・潜在株式調整後に限定)。
    if "bounds_eps" in bounds:
        end_eps = bounds.get("bounds_2", len(lines))
        eps_rows = _find_period_rows(lines, bounds["bounds_eps"], end_eps)
        if eps_rows:
            vals = eps_rows[0][1]
            out["eps_actual"] = vals[0] if len(vals) > 0 else None
            out["eps_diluted_actual"] = vals[1] if len(vals) > 1 else None
        if len(eps_rows) >= 2:
            vals = eps_rows[1][1]
            out["eps_actual_prior"] = vals[0] if len(vals) > 0 else None
            out["eps_diluted_actual_prior"] = vals[1] if len(vals) > 1 else None

    # 財政状態(当期・前期): total_assets, net_assets, equity_ratio
    if "bounds_2" in bounds:
        end2 = bounds.get("bounds_cf", bounds.get("bounds_dividend", len(lines)))
        fin_rows = _find_period_rows(lines, bounds["bounds_2"], end2)
        if fin_rows:
            vals = fin_rows[0][1]
            out["total_assets"] = vals[0] if len(vals) > 0 else None
            out["net_assets"] = vals[1] if len(vals) > 1 else None
            out["equity_ratio"] = vals[2] if len(vals) > 2 else None
        if len(fin_rows) >= 2:
            vals = fin_rows[1][1]
            out["total_assets_prior"] = vals[0] if len(vals) > 0 else None
            out["net_assets_prior"] = vals[1] if len(vals) > 1 else None
            out["equity_ratio_prior"] = vals[2] if len(vals) > 2 else None

    # キャッシュ・フローの状況(任意、無い会社もある)
    if "bounds_cf" in bounds:
        end_cf = bounds.get("bounds_dividend", len(lines))
        cf_rows = _find_period_rows(lines, bounds["bounds_cf"], end_cf)
        if cf_rows:
            vals = cf_rows[0][1]
            out["cf_operating"] = vals[0] if len(vals) > 0 else None
            out["cf_investing"] = vals[1] if len(vals) > 1 else None
            out["cf_financing"] = vals[2] if len(vals) > 2 else None
            out["cf_cash_end"] = vals[3] if len(vals) > 3 else None

    # 業績予想(通期、任意、無い会社もある)
    if "bounds_3" in bounds:
        for line in lines[bounds["bounds_3"]:]:
            m = _FORECAST_ROW_RE.match(line.strip())
            if not m:
                continue
            rest = _tokenize_rest(m.group("rest"))
            columns = _FORECAST_COLUMNS_BY_STANDARD.get(
                accounting_standard, _FORECAST_COLUMNS_BY_STANDARD["jgaap"]
            )
            idx = 0
            for col in columns:
                value = rest[idx] if idx < len(rest) else None
                pct = rest[idx + 1] if idx + 1 < len(rest) else None
                idx += 2
                if col.startswith("_skip"):
                    continue
                out[f"forecast_{col}"] = value
                out[f"forecast_{col}_yoy_pct"] = pct
            # EPS予想は残りトークンの最後の1つ(yoy%を持たない唯一の列)。
            if idx < len(rest):
                out["forecast_eps"] = rest[-1]
            break

    out.update(parse_dividend_table(lines, period_type))

    _explicit = {
        "docid", "edinet_code", "sec_code", "extraction_status", "period_type",
        "consolidation", "accounting_standard", "fiscal_period_label", "period_end",
        "forecast_period_end",
    }
    kwargs = {f.name: out.get(f.name) for f in fields(FactRow) if f.name not in _explicit}
    return FactRow(
        docid=docid,
        edinet_code=edinet_code,
        sec_code=sec_code,
        extraction_status="ok",
        period_type=period_type,
        consolidation=consolidation,
        accounting_standard=accounting_standard,
        fiscal_period_label=fiscal_period_label,
        period_end=period_end,
        forecast_period_end=forecast_period_end,
        **kwargs,
    )
