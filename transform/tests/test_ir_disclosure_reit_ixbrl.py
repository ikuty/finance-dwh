"""ir_disclosure_reit_ixbrl.py の単体テスト。実機確認済み(2026-10-12、アドバンス・
レジデンス投資法人等)のiXBRL属性パターンを模した合成HTMLフィクスチャで検証する。"""

from __future__ import annotations

import ir_disclosure_reit_ixbrl as m


def test_parse_facts_extracts_only_tse_re_t_elements() -> None:
    html = """
    <ix:nonFraction name="tse-re-t:NetIncome" contextRef="CurrentYearDuration_NonConsolidatedMember_ResultMember"
        unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">2,593</ix:nonFraction>
    <ix:nonFraction name="jpdei_cor:SomeOtherElement" contextRef="FilingDateInstant"
        unitRef="JPY" decimals="0" scale="0" format="ixt:numdotdecimal">999</ix:nonFraction>
    """
    facts = m.parse_facts(html)
    assert len(facts) == 1
    assert facts[0].element_id == "tse-re-t:NetIncome"


def test_parse_facts_applies_scale() -> None:
    html = (
        '<ix:nonFraction name="tse-re-t:NetIncome" '
        'contextRef="CurrentYearDuration_NonConsolidatedMember_ResultMember" '
        'unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">2,593</ix:nonFraction>'
    )
    facts = m.parse_facts(html)
    assert facts[0].value_num == 2_593_000_000.0
    assert facts[0].context_id == "CurrentYearDuration_NonConsolidatedMember_ResultMember"
    assert facts[0].unit_ref == "JPY"


def test_parse_facts_applies_negative_sign() -> None:
    html = (
        '<ix:nonFraction name="tse-re-t:ChangeInNetIncome" '
        'contextRef="CurrentYearDuration_NonConsolidatedMember_ResultMember" '
        'unitRef="Pure" decimals="3" scale="-2" format="ixt:numdotdecimal" sign="-">3.8</ix:nonFraction>'
    )
    facts = m.parse_facts(html)
    assert facts[0].value_num == -0.038


def test_parse_facts_self_closing_nil_element_has_no_value() -> None:
    # 実機確認: 値が無い場合は自己終了形+xsi:nil="true"(中身にダミーの "－" 等の
    # 装飾テキストが続くが、それはiXBRLタグの外側の表示用マークアップで要素の中身ではない)。
    html = (
        '<ix:nonFraction name="tse-re-t:DistributionsInExcessOfProfitPerUnitREIT" '
        'contextRef="CurrentYearDuration_NonConsolidatedMember_ResultMember" '
        'unitRef="JPYPerUnits" xsi:nil="true"/>'
        '<span>－</span>'
    )
    facts = m.parse_facts(html)
    assert len(facts) == 1
    assert facts[0].value_num is None


def test_parse_facts_strips_inner_html_tags_from_content() -> None:
    # 値がネストしたHTMLタグ(フォント指定等)で囲まれているケース。
    html = (
        '<ix:nonFraction name="tse-re-t:NetAssetsPerUnitREIT" '
        'contextRef="CurrentYearInstant_NonConsolidatedMember_ResultMember" '
        'unitRef="JPYPerUnits" decimals="0" scale="0" format="ixt:numdotdecimal">'
        '<span style="font-family: sans-serif">99,293</span></ix:nonFraction>'
    )
    facts = m.parse_facts(html)
    assert facts[0].value_num == 99293.0


def test_parse_facts_returns_multiple_contexts_for_same_element() -> None:
    html = (
        '<ix:nonFraction name="tse-re-t:TotalAssets" '
        'contextRef="CurrentYearInstant_NonConsolidatedMember_ResultMember" '
        'unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">192,291</ix:nonFraction>'
        '<ix:nonFraction name="tse-re-t:TotalAssets" '
        'contextRef="PriorYearInstant_NonConsolidatedMember_ResultMember" '
        'unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">187,435</ix:nonFraction>'
    )
    facts = m.parse_facts(html)
    assert len(facts) == 2
    assert {f.context_id for f in facts} == {
        "CurrentYearInstant_NonConsolidatedMember_ResultMember",
        "PriorYearInstant_NonConsolidatedMember_ResultMember",
    }
