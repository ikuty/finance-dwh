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

**ページ処理後に`page.flush_cache()`を呼ぶ**（2026-09-13、実機のメモリ
逼迫障害を踏まえた修正）。pdfplumberは`extract_words()`の内部で使う文字
単位のデータ等をページごとに内部キャッシュし、明示的に破棄しない限り
`PDF`オブジェクトの生存期間中(=このジェネレータを消費し終えるまで)ずっと
保持し続ける。1200ページ超の月次PDF(形式B)でこれを未対策のまま流したところ、
ピークメモリが約9.8GBに達し、Mac Mini(物理メモリ7.7GB)でメモリ逼迫
（`Under memory pressure`の連発、tailscaledのダウンによる外部からの
到達不能）を実機で引き起こした。`flush_cache()`を呼ぶことでピークメモリが
約109MBまで下がることを実測済み（詳細はdocs/raw_landing_design.md参照）。

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
            page.flush_cache()  # ページ単位の内部キャッシュを破棄(メモリ逼迫対策)
