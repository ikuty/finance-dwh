"""JPX形式C(株式相場表・詳細日次)のPDFから座標付き単語データを取り出す。

`jpx_stq_facts.py`の前段(Stage1、「テキスト化する層」)。ビジネスロジックを一切
持たない、PDF→座標付き単語データへの機械的な変換のみを担う。

理由: 単純なテキスト抽出(改行順にテキストを連結するだけの方式)では、この
PDFにおいて列の並び順が保持されないことを実機データで確認した(PDF生成時の
描画順序が視覚上の左→右の列順と一致しないため)。座標(x0)とフォントサイズを
保持しておき、列への割り当て・見出し行の判定は後段(`jpx_stq_facts.py`)の
責務とする。ここを分けることで、後段のロジック(セクション境界判定・列の
意味づけ等、今後何度も変わる想定)を直すたびに、PDF抽出(実測 約36秒/日)を
再実行しなくて済む。

詳細はdocs/raw_landing_design.md参照。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pdfplumber


class Word(NamedTuple):
    """1単語(pdfplumberのword)の座標・フォントサイズ付きテキスト。

    top/x0/x1はページ左上を原点とするポイント単位の座標(pdfplumberの座標系)。
    """

    page: int
    top: float
    x0: float
    x1: float
    size: float
    text: str


def iter_words(pdf_path: Path) -> Iterator[Word]:
    """PDFの全ページを走査し、単語ごとに座標・フォントサイズ付きで yield する。"""
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False, extra_attrs=["size"])
            for w in words:
                yield Word(
                    page=page_no,
                    top=w["top"],
                    x0=w["x0"],
                    x1=w["x1"],
                    size=w.get("size", 0.0),
                    text=w["text"],
                )
