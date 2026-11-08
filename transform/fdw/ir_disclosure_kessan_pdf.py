"""ir-disclosure-dlの決算短信PDFから、サマリー情報セクションのテキストを取り出す。

Stage2(ir_disclosure_kessan_facts.py)の前段(Stage1、「テキスト化する層」)。ビジネス
ロジックを一切持たない、PDF→テキストへの機械的な変換のみを担う。

実機検証(2026-09-30、ir-disclosure-dlが保有する323件のkessan_tanshin PDF全件)の結果、
pdfplumber.extract_tables()は罫線の描画が提出企業のPDF生成ソフトウェアに依存して
不安定なため、同じ論理的な表(EPS実績等)が企業によって検出されたりされなかったりする
既知の問題がある。一方extract_text()で得られるテキストは全サンプルで一貫して整形
されているため、テキスト+正規表現ベースの解析を採用する(jpx_stq_pdfとは逆の判断:
あちらは単純なテキスト抽出では列順が保持されないため座標ベースを採用したが、この
文書種別ではテキスト抽出の方が安定していると実機データで確認済み)。

サマリー情報セクションは通常1ページ目にあるが、「公認会計士等による期中レビューの
完了」notice等、1ページ目が別内容のカバーページになっているケースがあるため
(実機確認、2026-09-30)、先頭複数ページを連結して返す。

【テキスト化エンジン: PyMuPDF(2026-11-08にpdfplumberから変更)】
大量バックフィル(finance-dwh Issue #94、約38,600件)でpdfplumberが約1.2〜2.6秒/件と
ボトルネックだったため、PyMuPDF(`get_text("text", sort=True)`)へ変更した。Mac Mini上で
ステージングの2,819件(2024-03〜2026-11の全月から均等に抽出)について、pdfplumberの
結果とStage2の全列を突き合わせた:

    - 全列一致 99.11%(2,794件)。速度は約132ms/件(4並列)で、pdfplumberの約17倍。
    - 不一致25件のセル: PyMuPDFだけがNULLになったのは2セル、pdfplumberだけがNULLに
      なったのは114セル、両者に値があり違ったのは36セル。違った36セルは、確認した範囲では
      pdfplumber側が数字を連結する誤り(`2,456△13.9`のように数字と`△`の間の空白を落とし、
      列がずれる)で、PyMuPDF側が正しかった。つまりpdfplumberの結果にも約0.5%の誤りがあった。
    - pypdfium2は一部の文字(`△`・`％`・`―`・`銭`等)をテキストとして持たず、読み順も崩れるため
      不採用。pdftotext -layoutは98%一致で、負数の欠落が残るため不採用。

PyMuPDFはpdfplumberと空白の入り方が違うため、Stage2の正規表現が前提とする形に揃える
正規化(normalize_pymupdf_text)を通す。いずれも上記2,819件の突き合わせで効果を確認した:

    1. 数字と「年月日期四半第」の間の空白を除く(`2024 年12 月期第1 四半期` → `2024年12月期第1四半期`)。
       除かないと期ラベル行の正規表現に合わず、主要列がNULLになる。
    2. 期ラベルの直後が数字なら空白を補う(`...第３四半期105,570` → `...第３四半期 105,570`)。
       PyMuPDFはラベルと最初の数値が近いと空白を出さない(pdfplumberは出す)。
    3. `－`と`△`が連結していたら空白を補う(`－△1,827` → `－ △1,827`)。連結のままだと
       トークンが数値として解釈できず、負数がNULLになる。
    4. 連続する空白・全角スペースを1つにし、空行と行頭行末の空白を除く。

注意: PyMuPDFのライセンスはAGPL。個人利用で成果物を外部に公開しない前提で採用している
(`CLAUDE.md`の留意事項を参照)。2.x系は`onnxruntime`・`numpy`等の依存を引き込むため、
依存のない1.26系にピン留めする(requirements.txt)。1.26.7と2.1で結果が同一なことを確認済み。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf  # type: ignore[import-untyped]

# サマリー情報が通常1〜2ページ目に収まることを実機確認済み(2026-09-30)。カバーページが
# 付くケースも考慮し余裕を持たせるが、添付の詳細財務諸表(10ページ以上)まで読む必要は
# ないため上限を設ける。
MAX_PAGES_TO_SCAN = 6

# 「－」「―」(数値なしの記号)の直後に「△」(マイナス)が空白なしで続く箇所。
_DASH_THEN_TRIANGLE_RE = re.compile(r"(?<=[－―])(?=[△▲▼])")
# 期ラベル末尾の「期」の直後に、空白なしで数値・記号が続く箇所。
_PERIOD_THEN_NUMBER_RE = re.compile(r"(?<=期)(?=[0-9△▲▼－-])")
# 「2024 年」「第1 四半期」のように、数字の後ろで期ラベルの語が空白で分断された箇所。
_DIGIT_SPACE_LABEL_RE = re.compile(r"(?<=\d) +(?=[年月日期四半第])")
# 「年 12」のように、年月日の後ろの数字が空白で分断された箇所。
_LABEL_SPACE_DIGIT_RE = re.compile(r"(?<=[年月日第]) +(?=\d)")
_SPACES_RE = re.compile(r"[ \t　]+")


def normalize_pymupdf_text(text: str) -> str:
    """PyMuPDFのテキストを、Stage2(ir_disclosure_kessan_facts.py)の正規表現が前提とする
    形(pdfplumber出力に近い空白の入り方)に揃える。詳細はモジュールのdocstring参照。"""
    text = _DASH_THEN_TRIANGLE_RE.sub(" ", text)
    text = _PERIOD_THEN_NUMBER_RE.sub(" ", text)
    text = _DIGIT_SPACE_LABEL_RE.sub("", text)
    text = _LABEL_SPACE_DIGIT_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def extract_text(pdf_path: Path) -> str:
    """先頭MAX_PAGES_TO_SCANページのテキストを改行区切りで連結して返す。"""
    texts: list[str] = []
    doc = pymupdf.open(pdf_path)
    try:
        for page_index in range(min(MAX_PAGES_TO_SCAN, len(doc))):
            # sort=True: PDF内部の描画順ではなく、画面上の位置で読み順を並べ替える
            # (sort=Falseでは表が見出しより前に出る等、読み順が崩れる)。
            texts.append(str(doc[page_index].get_text("text", sort=True)))
    finally:
        doc.close()
    return normalize_pymupdf_text("\n".join(texts))
