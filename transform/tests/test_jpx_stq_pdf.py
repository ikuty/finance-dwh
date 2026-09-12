"""jpx_stq_pdf.py の単体テスト。

実際にダウンロードしたJPXのPDFは個人利用限定の利用規約(商用二次利用不可)の
ため、テストフィクスチャとしてリポジトリに含めることはできない。代わりに
最小限の合成PDF(Helvetica・ASCII文字のみ)を自前のビルダーで生成し、
pdfplumberによる座標・フォントサイズ抽出そのものを検証する(実際の日本語
JPXデータに対する構造化ロジックの正しさは`jpx_stq_facts.py`側でWordを
直接組み立てて検証する)。
"""

from __future__ import annotations

from pathlib import Path

import jpx_stq_pdf as m


def _pdf_object(num: int, body: bytes) -> bytes:
    return f"{num} 0 obj\n".encode() + body + b"\nendobj\n"


def build_minimal_pdf(pages: list[list[tuple[str, float, float, float]]]) -> bytes:
    """pages: 各ページの [(text, x, y, font_size), ...]。Helvetica固定。

    依存追加を避けるための自前の最小PDFビルダー(テスト専用)。
    """
    n_pages = len(pages)
    objects: dict[int, bytes] = {}
    font_obj_num = 3 + n_pages * 2
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode()
    for i, items in enumerate(pages):
        page_num = 3 + i
        content_num = 3 + n_pages + i
        stream_parts = []
        for text, x, y, size in items:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_parts.append(f"BT /F1 {size} Tf {x} {y} Td ({escaped}) Tj ET")
        stream = "\n".join(stream_parts).encode()
        objects[page_num] = (
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
            f"/MediaBox [0 0 1400 900] /Contents {content_num} 0 R >>"
        ).encode()
        objects[content_num] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    objects[font_obj_num] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    buf = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(buf)
        buf += _pdf_object(num, objects[num])

    xref_offset = len(buf)
    max_num = max(objects)
    buf += f"xref\n0 {max_num + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for num in range(1, max_num + 1):
        buf += f"{offsets[num]:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {max_num + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(buf)


def write_pdf(path: Path, pages: list[list[tuple[str, float, float, float]]]) -> None:
    path.write_bytes(build_minimal_pdf(pages))


def test_iter_words_extracts_text_position_and_size(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    write_pdf(
        pdf_path,
        [[("1301", 74, 700, 10.0), ("100TestCo", 140, 700, 6.0)]],
    )
    words = list(m.iter_words(pdf_path))
    assert [w.text for w in words] == ["1301", "100TestCo"]
    assert all(w.page == 1 for w in words)
    assert words[0].size == 10.0
    assert words[1].size == 6.0
    assert words[0].x0 < words[1].x0


def test_iter_words_covers_multiple_pages_in_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    write_pdf(
        pdf_path,
        [
            [("PAGE1", 74, 700, 10.0)],
            [("PAGE2", 74, 700, 10.0)],
        ],
    )
    words = list(m.iter_words(pdf_path))
    assert [(w.page, w.text) for w in words] == [(1, "PAGE1"), (2, "PAGE2")]


def test_iter_words_empty_page_yields_nothing(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    write_pdf(pdf_path, [[]])
    assert list(m.iter_words(pdf_path)) == []
