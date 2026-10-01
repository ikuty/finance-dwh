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

# 「2027年５月期第１四半期 6,334 5.5 585 0.5 ...」「2027年8月期（予想） － 5.00 ...」
# 「令和８年７月期 ...」のような、期ラベル+数値トークン列からなる行。元号年のデータ行は
# タイトルで「令和」を示した後、本文では「令和」を省略し「８年10月期...」のように
# 1〜2桁の年数字だけで始まることを実機確認済み(2026-09-30、株式会社キタック)。
_PERIOD_ROW_RE = re.compile(
    r"^(?P<label>\d{1,4}年\S*?期(?:\s*[（(]予想[）)])?)\s+"
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
        elif "bounds_dividend" not in bounds and re.match(r"^[２2][．.].*配当の状況", s):
            bounds["bounds_dividend"] = i
        elif "bounds_3" not in bounds and re.match(r"^[３3][．.]", s):
            bounds["bounds_3"] = i
    return bounds


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
    extraction_status: str
    period_type: str | None
    consolidation: str | None
    accounting_standard: str | None
    fiscal_period_label: str | None

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


FACT_COLUMNS = [f.name for f in fields(FactRow)]


def _empty_row(docid: str, extraction_status: str) -> FactRow:
    kwargs = {f.name: None for f in fields(FactRow) if f.name not in ("docid", "extraction_status")}
    return FactRow(docid=docid, extraction_status=extraction_status, **kwargs)


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


def run(text: str, docid: str, title: str) -> FactRow:
    """テキスト全体からFactRowを組み立てる便利関数。"""
    status = classify_title(title)
    if status != "genuine":
        return _empty_row(docid, status)

    lines = text.split("\n")
    bounds = _find_section_bounds(lines)

    period_type = parse_period_type(title)
    consolidation = parse_consolidation(title)
    accounting_standard = parse_accounting_standard(title) or "jgaap"

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

    _explicit = {
        "docid", "extraction_status", "period_type", "consolidation",
        "accounting_standard", "fiscal_period_label",
    }
    kwargs = {f.name: out.get(f.name) for f in fields(FactRow) if f.name not in _explicit}
    return FactRow(
        docid=docid,
        extraction_status="ok",
        period_type=period_type,
        consolidation=consolidation,
        accounting_standard=accounting_standard,
        fiscal_period_label=fiscal_period_label,
        **kwargs,
    )
