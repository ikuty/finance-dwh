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
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

# サマリー情報が通常1〜2ページ目に収まることを実機確認済み(2026-09-30)。カバーページが
# 付くケースも考慮し余裕を持たせるが、添付の詳細財務諸表(10ページ以上)まで読む必要は
# ないため上限を設ける。
MAX_PAGES_TO_SCAN = 6


def extract_text(pdf_path: Path) -> str:
    """先頭MAX_PAGES_TO_SCANページのテキストを改行区切りで連結して返す。"""
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:MAX_PAGES_TO_SCAN]:
            texts.append(page.extract_text() or "")
            page.flush_cache()  # jpx_stq_pdfと同じ理由(メモリ逼迫対策)
    return "\n".join(texts)
