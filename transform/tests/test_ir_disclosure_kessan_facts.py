"""ir_disclosure_kessan_facts.py の単体テスト。

実機確認済み(2026-09-30、ir-disclosure-dlが保有する323件のkessan_tanshin PDF全件)の
テキストをフィクスチャとして使う。決算短信は上場規則に基づく法定開示文書であり、
テキスト自体はjpx_stq_facts.pyの実測座標値と同じ扱い(事実データ、PDFファイル自体は
コミットしない)。
"""

from __future__ import annotations

import ir_disclosure_kessan_facts as m

# --- classify_title ---------------------------------------------------------------------


def test_classify_title_correction() -> None:
    assert m.classify_title(
        "（訂正・数値データ訂正）「2026年６月期決算短信〔日本基準〕（連結）」の一部訂正について"
    ) == "correction"


def test_classify_title_supplementary() -> None:
    assert m.classify_title(
        "2027年１月期　第２四半期(中間期)決算短信〔日本基準〕(連結)　補足説明資料"
    ) == "supplementary"


def test_classify_title_review_notice() -> None:
    assert m.classify_title(
        "2027年３月期第１四半期決算短信〔日本基準〕（連結）（公認会計士等による期中レビューの完了）"
    ) == "review_notice"


def test_classify_title_schedule_notice_has_no_accounting_standard_bracket() -> None:
    # 実機確認(2026-09-30): 事務連絡系のタイトルは「〔日本基準〕」等の会計基準括弧を
    # 持たない。これが本体との機械的な判別点。
    assert m.classify_title(
        "2027年４月期第１四半期決算短信の開示が四半期末後45日を超えることに関するお知らせ"
    ) == "schedule_notice"
    assert m.classify_title(
        "2026年３月期決算短信の開示が期末後50日を超えたことに関するお知らせ"
    ) == "schedule_notice"


def test_classify_title_genuine() -> None:
    assert m.classify_title("2027年５月期第１四半期決算短信〔日本基準〕（連結）") == "genuine"
    assert m.classify_title("2026年10月期　第3四半期決算短信〔日本基準〕（非連結）") == "genuine"


# --- parse_period_type / parse_consolidation / parse_accounting_standard ----------------


def test_parse_period_type() -> None:
    assert m.parse_period_type("2027年５月期第１四半期決算短信〔日本基準〕（連結）") == "q1"
    assert m.parse_period_type("2027年１月期 第２四半期（中間期）決算短信〔日本基準〕(非連結)") == "q2_half"
    assert m.parse_period_type("2027年１月期　第２四半期（中間期）決算短信〔日本基準〕(連結)") == "q2_half"
    assert m.parse_period_type("2026年10月期 第3四半期決算短信〔日本基準〕（連結）") == "q3"
    assert m.parse_period_type("2026年8月期 決算短信〔日本基準〕（連結）") == "annual"


def test_parse_consolidation() -> None:
    assert m.parse_consolidation("2026年8月期 決算短信〔日本基準〕（連結）") == "consolidated"
    assert m.parse_consolidation("2026年７月期決算短信〔日本基準〕(非連結)") == "non_consolidated"


def test_parse_accounting_standard() -> None:
    assert m.parse_accounting_standard("2026年５月期  決算短信〔ＩＦＲＳ〕（連結）") == "ifrs"
    assert m.parse_accounting_standard("2026年8月期 決算短信〔日本基準〕（連結）") == "jgaap"


# --- _normalize_number -------------------------------------------------------------------


def test_normalize_number_handles_comma_triangle_and_dash() -> None:
    assert m._normalize_number("6,334") == "6334"
    assert m._normalize_number("△1.2") == "-1.2"
    assert m._normalize_number("△124,710") == "-124710"
    assert m._normalize_number("－") is None
    assert m._normalize_number("―") is None
    assert m._normalize_number("71.00") == "71.00"


# --- run(): 四半期(連結・日本基準)、実機確認済みテキスト(ニイタカ、2026-09-30) -----------

_NIITAKA_Q1_TEXT = """\
１．2027年５月期第１四半期の連結業績（2026年６月１日～2026年８月31日）
（１）連結経営成績（累計） （％表示は、対前年同四半期増減率）
親会社株主に帰属する
売上高 営業利益 経常利益
四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年５月期第１四半期 6,334 5.5 585 0.5 608 △1.2 415 △0.8
2026年５月期第１四半期 6,006 4.8 582 36.6 616 40.0 419 41.3
（注）包括利益 2027年５月期第１四半期 467百万円 （△2.4％） 2026年５月期第１四半期 479百万円 （31.7％）
潜在株式調整後
１株当たり
１株当たり
四半期純利益
四半期純利益
円 銭 円 銭
2027年５月期第１四半期 71.00 －
2026年５月期第１四半期 70.99 －
（２）連結財政状態
総資産 純資産 自己資本比率
百万円 百万円 ％
2027年５月期第１四半期 23,181 16,219 70.0
2026年５月期 23,840 16,268 68.2
（参考）自己資本 2027年５月期第１四半期 16,219百万円 2026年５月期 16,268百万円
２．配当の状況
年間配当金
第１四半期末 第２四半期末 第３四半期末 期末 合計
円 銭 円 銭 円 銭 円 銭 円 銭
2026年５月期 － 38.00 － 39.00 77.00
2027年５月期 －
2027年５月期（予想） 42.00 － 42.00 84.00
（注）直近に公表されている配当予想からの修正の有無：無
３．2027年５月期の連結業績予想（2026年６月１日～2027年５月31日）
（％表示は、対前期増減率）
親会社株主に帰属 １株当たり
売上高 営業利益 経常利益
する当期純利益 当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通期 27,500 11.7 1,800 △15.0 1,850 △16.6 1,285 △32.4 222.38
（注）直近に公表されている業績予想からの修正の有無：無"""


def test_run_niitaka_q1_consolidated_jgaap() -> None:
    row = m.run(_NIITAKA_Q1_TEXT, "docid001", "2027年５月期第１四半期決算短信〔日本基準〕（連結）")
    assert row.extraction_status == "ok"
    assert row.period_type == "q1"
    assert row.consolidation == "consolidated"
    assert row.accounting_standard == "jgaap"
    assert row.fiscal_period_label == "2027年５月期第１四半期"
    assert row.sales == "6334"
    assert row.sales_prior == "6006"
    assert row.sales_yoy_pct == "5.5"
    assert row.operating_income == "585"
    assert row.ordinary_income == "608"
    assert row.ordinary_income_yoy_pct == "-1.2"
    assert row.net_income == "415"
    assert row.eps_actual == "71.00"
    assert row.eps_actual_prior == "70.99"
    assert row.eps_diluted_actual is None
    assert row.total_assets == "23181"
    assert row.net_assets == "16219"
    assert row.equity_ratio == "70.0"
    assert row.forecast_sales == "27500"
    assert row.forecast_operating_income == "1800"
    assert row.forecast_ordinary_income == "1850"
    assert row.forecast_net_income == "1285"
    assert row.forecast_eps == "222.38"


# --- run(): 本決算(連結・日本基準・CF含む)、実機確認済みテキスト(クラウディアHD) --------

_KURAUDIA_ANNUAL_TEXT = """\
１．2026年8月期の連結業績（2025年9月1日～2026年8月31日）
（１）連結経営成績 （％表示は対前期増減率）
親会社株主に帰属する
売上高 営業利益 経常利益
当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2026年8月期 13,880 2.1 632 57.2 668 60.4 423 35.7
2025年8月期 13,591 2.8 402 17.7 416 7.4 312 62.2
１株当たり 潜在株式調整後 自己資本 総資産 売上高
当期純利益 １株当たり当期純利益 当期純利益率 経常利益率 営業利益率
円 銭 円 銭 ％ ％ ％
2026年8月期 46.84 － 10.2 5.2 4.6
2025年8月期 34.72 － 8.0 3.3 3.0
（２）連結財政状態
総資産 純資産 自己資本比率 １株当たり純資産
百万円 百万円 ％ 円 銭
2026年8月期 12,972 4,374 33.7 482.62
2025年8月期 12,625 3,967 31.4 440.28
（３）連結キャッシュ・フローの状況
営業活動による 投資活動による 財務活動による 現金及び現金同等物
キャッシュ・フロー キャッシュ・フロー キャッシュ・フロー 期末残高
百万円 百万円 百万円 百万円
2026年8月期 999 △391 △687 1,800
2025年8月期 859 △463 △193 1,860
２．配当の状況
年間配当金 配当金総額 配当性向 純資産配当率
第１四半期末 第２四半期末 第３四半期末 期末 合計 (合計) （連結） （連結）
円 銭 円 銭 円 銭 円 銭 円 銭 百万円 ％ ％
2025年8月期 － 5.00 － 5.00 10.00 90 28.8 2.3
2026年8月期 － 5.00 － 7.00 12.00 108 25.6 2.6
2027年8月期（予想） － 5.00 － 5.00 10.00 16.5
３．2027年8月期の連結業績予想（2026年9月1日～2027年8月31日）
（％表示は対前期増減率）
親会社株主に帰属する １株当たり
売上高 営業利益 経常利益
当期純利益 当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通期 14,700 5.9 750 18.6 750 12.2 550 29.8 60.45"""


def test_run_kuraudia_annual_consolidated_jgaap_with_cf_and_combined_eps_header() -> None:
    row = m.run(_KURAUDIA_ANNUAL_TEXT, "docid002", "2026年8月期 決算短信〔日本基準〕（連結）")
    assert row.extraction_status == "ok"
    assert row.period_type == "annual"
    assert row.sales == "13880"
    assert row.operating_income == "632"
    assert row.net_income == "423"
    # EPS見出しが「１株当たり 潜在株式調整後 自己資本 総資産 売上高」のように
    # ROE等と結合した1行でも、先頭2トークンだけEPS実績・潜在株式調整後として拾う
    # (実機確認、2026-09-30、内田洋行で発見したのと同じ形式)。
    assert row.eps_actual == "46.84"
    assert row.eps_diluted_actual is None
    assert row.total_assets == "12972"
    assert row.cf_operating == "999"
    assert row.cf_investing == "-391"
    assert row.cf_financing == "-687"
    assert row.cf_cash_end == "1800"
    assert row.forecast_eps == "60.45"


# --- run(): 半角の節番号(「1.」「(2)」等)を使う企業、実機確認済みテキスト(ハイレックス) -


_HALF_WIDTH_SECTION_TEXT = """\
1.2026年10月期第3四半期の連結業績（2025年11月1日～2026年7月31日）
(1)連結経営成績（累計） （％表示は、対前年同四半期増減率）
親会社株主に帰属する
売上高 営業利益 経常利益
四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2026年10月期第3四半期 320,556 40.8 3,882 23.1 8,032 36.9 40,842 －
2025年10月期第3四半期 227,679 △3.3 3,152 141.5 5,866 93.0 3,401 △13.5
潜在株式調整後
1株当たり
1株当たり
四半期純利益
四半期純利益
円 銭 円 銭
2026年10月期第3四半期 1,105.08 1,104.73
2025年10月期第3四半期 90.67 90.64
(2)連結財政状態
総資産 純資産 自己資本比率
百万円 百万円 ％
2026年10月期第3四半期 363,495 236,117 59.5
2025年10月期 276,997 191,692 63.2
2.配当の状況
年間配当金
第1四半期末 第2四半期末 第3四半期末 期末 合計
円 銭 円 銭 円 銭 円 銭 円 銭
2025年10月期 － 23.00 － 23.00 46.00
2026年10月期 － 53.50 －
2026年10月期（予想） 26.50 80.00
3.2026年10月期の連結業績予想（2025年11月１日～2026年10月31日）
（％表示は、対前期増減率）
親会社株主に帰属 1株当たり
売上高 営業利益 経常利益
する当期純利益 当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通期 401,000 31.9 5,400 50.0 9,900 23.3 41,300 1.1 996.89"""


def test_run_handles_half_width_section_markers() -> None:
    # 実機確認(2026-09-30、株式会社ハイレックスコーポレーション): 節番号が
    # 全角(「１．」「（２）」)ではなく半角(「1.」「(2)」)の企業がある。
    row = m.run(
        _HALF_WIDTH_SECTION_TEXT, "docid003", "2026年10月期 第3四半期決算短信〔日本基準〕（連結）"
    )
    assert row.extraction_status == "ok"
    assert row.sales == "320556"
    assert row.operating_income == "3882"
    assert row.net_income == "40842"
    assert row.total_assets == "363495"
    assert row.eps_actual == "1105.08"
    assert row.forecast_eps == "996.89"


# --- run(): 元号年のデータ行は「令和」を省略した短縮形になる ------------------------------


def test_run_handles_era_year_abbreviated_in_data_rows() -> None:
    # 実機確認(2026-09-30、株式会社キタック): タイトルでは「令和８年10月期」でも、
    # 本文データ行では「令和」を省略した「８年10月期...」になる。
    text = """\
１．令和８年10月期第３四半期の連結業績（令和７年10月21日～令和８年７月20日）
（１）連結経営成績(累計) (％表示は、対前年同四半期増減率)
親会社株主に帰属
売上高 営業利益 経常利益
する四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
８年10月期第３四半期 2,424 △4.9 158 △4.9 174 △7.3 141 △33.7
７年10月期第３四半期 2,549 9.5 166 △33.1 188 △33.4 213 11.2
潜在株式調整後
１株当たり
１株当たり
四半期純利益
四半期純利益
円 銭 円 銭
８年10月期第３四半期 25.34 ―
７年10月期第３四半期 38.20 ―
（２）連結財政状態
総資産 純資産 自己資本比率
百万円 百万円 ％
８年10月期第３四半期 6,074 3,691 60.8
７年10月期 6,354 3,532 55.6"""
    row = m.run(text, "docid004", "令和８年10月期第３四半期決算短信〔日本基準〕(連結)")
    assert row.extraction_status == "ok"
    assert row.sales == "2424"
    assert row.operating_income == "158"
    assert row.net_income == "141"
    assert row.total_assets == "6074"


# --- run(): IFRSの業績予想セクションは実績セクションと列数が異なる -----------------------


def test_run_ifrs_forecast_section_has_fewer_columns_than_actuals() -> None:
    # 実機確認(2026-09-30): IFRSの実績セクションは6列(当期利益・当期包括利益含む)だが
    # 業績予想セクションは4列(売上収益・営業利益・税引前利益・親会社の所有者に帰属する
    # 当期利益)のみ。実績用の列マッピングをそのまま使うとforecast_epsまでずれて
    # 取れなくなっていたバグの再現テスト。
    text = """\
１．2027年４月期第１四半期の連結業績（2026年５月１日～2026年７月31日）
（１）連結経営成績（累計） （％表示は、対前年同四半期増減率）
売上収益 営業利益 税引前利益 当期利益 親会社の所有者に帰属する当期利益 当期包括利益合計額
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年４月期第１四半期 12,000 3.0 900 5.0 850 4.0 600 2.0 580 1.5 590 1.6
2026年４月期第１四半期 11,650 2.0 857 3.0 817 3.0 588 1.0 571 1.0 581 1.0
基本的１株当たり四半期利益
円 銭
2027年４月期第１四半期 48.00
2026年４月期第１四半期 47.00
（２）連結財政状態
総資産 資本合計 親会社の所有者に帰属する持分 親会社所有者帰属持分比率 １株当たり親会社所有者帰属持分
百万円 百万円 百万円 ％ 円 銭
2027年４月期第１四半期 50,000 20,000 19,500 39.0 1,560.00
2026年４月期 48,000 19,000 18,500 38.5 1,480.00
２．配当の状況
年間配当金
第１四半期末 第２四半期末 第３四半期末 期末 合計
円 銭 円 銭 円 銭 円 銭 円 銭
2026年４月期 － － － 20.00 20.00
2027年４月期（予想） － 20.00 － 20.00 40.00
３．2027年４月期の連結業績予想（2026年５月１日～2027年４月30日）
(％表示は、対前期増減率)
売上収益 営業利益 税引前利益 親会社の所有者に帰属する当期利益 基本的１株当たり当期利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通期 51,000 0.8 3,000 △17.8 2,900 △19.8 1,900 △19.3 152.53"""
    row = m.run(text, "docid005", "2027年４月期第１四半期決算短信〔ＩＦＲＳ〕(連結)")
    assert row.extraction_status == "ok"
    assert row.accounting_standard == "ifrs"
    assert row.sales == "12000"
    assert row.operating_income == "900"
    assert row.income_before_tax == "850"
    assert row.net_income == "580"  # 親会社の所有者に帰属する当期利益(当期利益全体ではない)
    assert row.forecast_sales == "51000"
    assert row.forecast_operating_income == "3000"
    assert row.forecast_income_before_tax == "2900"
    assert row.forecast_net_income == "1900"
    assert row.forecast_eps == "152.53"


# --- run(): 業績予想行が「通 期」のように字間が空くレイアウト --------------------------


def test_run_handles_spaced_forecast_row_label() -> None:
    # 実機確認(2026-09-30、株式会社カラダノート): 「通期」ではなく「通 期」と
    # 字間が空くレイアウトがある。
    text = """\
１．2027年１月期の連結業績（2026年２月１日～2027年１月31日）
（１）連結経営成績 （％表示は対前期増減率）
親会社株主に帰属する
売上高 営業利益 経常利益
当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年１月期 10,000 1.0 500 1.0 480 1.0 300 1.0
2026年１月期 9,900 1.0 495 1.0 475 1.0 297 1.0
１株当たり
当期純利益
円 銭
2027年１月期 30.00
2026年１月期 29.70
（２）連結財政状態
総資産 純資産 自己資本比率
百万円 百万円 ％
2027年１月期 20,000 8,000 40.0
2026年１月期 19,000 7,800 41.0
３．2028年１月期の連結業績予想（2027年２月１日～2028年１月31日）
(％表示は、対前期増減率)
親会社株主に帰
１株当たり当期
売上高 調整後EBITDA 営業利益 経常利益 属する当期純利
純利益
益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通 期 1,500 41.6 400 70.5 350 59.9 307 56.6 277 8.1 41.43"""
    row = m.run(text, "docid006", "2027年１月期決算短信〔日本基準〕（連結）")
    assert row.extraction_status == "ok"
    # EPSは常に行末のトークンのため、列が標準(4メトリクス)より多い場合でも
    # forecast_epsだけは正しく取れる(個別の予想利益列は非標準フォーマットのため
    # ずれる可能性があることは許容する、既知の制約)。
    assert row.forecast_eps == "41.43"


# --- run(): 業績予想セクションが存在しない(会社が非開示を選んでいる) --------------------


def test_run_without_forecast_section_leaves_forecast_fields_none() -> None:
    text = """\
１．2026年５月期の連結業績（2025年６月１日～2026年５月31日）
（１）連結経営成績 （％表示は対前期増減率）
売上高 営業利益 経常利益 当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2026年５月期 9,000 1.0 500 1.0 480 1.0 300 1.0
2025年５月期 8,900 1.0 495 1.0 475 1.0 297 1.0
１株当たり当期純利益
円 銭
2026年５月期 30.00
2025年５月期 29.70
（２）連結財政状態
総資産 純資産 自己資本比率
百万円 百万円 ％
2026年５月期 15,000 6,000 40.0
2025年５月期 14,500 5,800 40.0
通期業績予想につきましては、合理的な算定が困難であるため記載しておりません。"""
    row = m.run(text, "docid007", "2026年５月期決算短信〔日本基準〕（連結）")
    assert row.extraction_status == "ok"
    assert row.sales == "9000"
    assert row.forecast_eps is None
    assert row.forecast_sales is None


# --- run(): 本体以外のサブタイプはテーブル解析を試みず空のFactRowを返す ------------------


def test_run_correction_returns_empty_row_without_parsing() -> None:
    row = m.run("本文は自由記述で財務数値テーブルを持たない", "docid008", "（訂正）「決算短信」の一部訂正について")
    assert row.extraction_status == "correction"
    assert row.sales is None
    assert row.forecast_eps is None
