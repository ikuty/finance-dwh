"""ir-disclosure-dlが取得するREIT(投資法人)決算短信のiXBRL(inline XBRL)HTMLから、
`tse-re-t:`タクソノミの数値ファクトを抽出する。

事業会社向け決算短信PDFのstage1/2(ir_disclosure_kessan_pdf.py/ir_disclosure_kessan_
facts.py)とは別の、REIT専用パイプライン(load_ir_disclosure_reit.py)の抽出ロジック。
REITの決算短信はPDFに加えてiXBRL(`tse-re-t:`タクソノミ)が付随し、構造化タグとして
1口当たり指標(EPS/BPS相当)・発行済投資口総数等を直接取得できる(EDINET側の有報には
これらが構造化データとして存在しない、詳細はservices/ir-disclosure-dl/docs/
reit_support_design.md@finance-lake参照)。

edinet_csv_fdw.pyと同じ「解釈しすぎない」方針: `tse-re-t:`で始まる要素は全て抽出し、
どのcontextRef(当期/前期/予想等)を「今期の実績」として使うかの判断はcleansed層・
intermediate層に委ねる。

iXBRLの数値表現(実機確認、2026-10-12、アドバンス・レジデンス投資法人等の実データ):
  - `<ix:nonFraction name="tse-re-t:X" contextRef="..." unitRef="..." scale="6"
    sign="-" format="ixt:numdotdecimal">5,562</ix:nonFraction>`のように開始・終了
    タグの間にカンマ区切りの数値テキストを持つ形と、
  - `<ix:nonFraction name="tse-re-t:Y" contextRef="..." unitRef="..." xsi:nil="true"/>`
    のように値が無い(自己終了、中身なし)形の両方がある(実機確認: 1ファイル中
    126個のnonFraction要素のうち50個が自己終了形)。
  - 表示値は`scale`(10のべき乗、例: scale="6"は100万倍)と`sign="-"`(負数)を
    適用して初めて実際の値になる(例: "5,562"・scale="6" → 5,562,000,000)。
    decimalsは精度情報のみでここでは使わない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NONFRACTION_RE = re.compile(
    r"<ix:nonFraction\b(?P<attrs>[^>]*?)(?:/>|>(?P<content>.*?)</ix:nonFraction>)",
    re.S,
)
_ATTR_RE = re.compile(r'([\w:]+)="([^"]*)"')
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ReitIxbrlFact:
    element_id: str
    context_id: str
    unit_ref: str | None
    value_num: float | None


def _clean_text(content: str | None) -> str | None:
    if content is None:
        return None
    text = _TAG_STRIP_RE.sub("", content).strip().replace(",", "")
    return text or None


def _compute_value(attrs: dict[str, str], content: str | None) -> float | None:
    if attrs.get("xsi:nil") == "true" or attrs.get("nil") == "true":
        return None
    text = _clean_text(content)
    if text is None:
        return None
    try:
        raw = float(text)
    except ValueError:
        return None
    scale = int(attrs.get("scale", "0"))
    sign = -1 if attrs.get("sign") == "-" else 1
    return float(sign) * raw * (10.0**scale)


def parse_facts(html: str) -> list[ReitIxbrlFact]:
    """htmlからtse-re-t:要素のみを抽出する(DEI等の他タクソノミは対象外)。"""
    facts: list[ReitIxbrlFact] = []
    for m in _NONFRACTION_RE.finditer(html):
        attrs = dict(_ATTR_RE.findall(m.group("attrs")))
        name = attrs.get("name")
        if not name or not name.startswith("tse-re-t:"):
            continue
        facts.append(
            ReitIxbrlFact(
                element_id=name,
                context_id=attrs.get("contextRef", ""),
                unit_ref=attrs.get("unitRef"),
                value_num=_compute_value(attrs, m.group("content")),
            )
        )
    return facts
