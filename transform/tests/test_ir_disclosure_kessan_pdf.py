"""ir_disclosure_kessan_pdf.py の単体テスト。

実際のir-disclosure-dlのPDFは提出企業の著作物を含むためリポジトリにコミット
できない。代わりに最小限の合成PDF(Helvetica・ASCII文字のみ、test_jpx_stq_pdf.py
と同じ自前ビルダー)でextract_text()の機械的な振る舞い(ページ連結・上限ページ数)
のみを検証する。
"""

from __future__ import annotations

from pathlib import Path

import ir_disclosure_kessan_pdf as m


def _pdf_object(num: int, body: bytes) -> bytes:
    return f"{num} 0 obj\n".encode() + body + b"\nendobj\n"


def build_minimal_pdf(pages: list[list[tuple[str, float, float, float]]]) -> bytes:
    """pages: 各ページの [(text, x, y, font_size), ...]。Helvetica固定。
    test_jpx_stq_pdf.pyと同じ自前の最小PDFビルダー(テスト専用)。"""
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


def test_extract_text_concatenates_pages_with_newline(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    write_pdf(pdf_path, [[("PAGE1", 74, 700, 10.0)], [("PAGE2", 74, 700, 10.0)]])
    text = m.extract_text(pdf_path)
    assert text == "PAGE1\nPAGE2"


def test_extract_text_stops_at_max_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    pages = [[(f"PAGE{i}", 74.0, 700.0, 10.0)] for i in range(1, m.MAX_PAGES_TO_SCAN + 3)]
    write_pdf(pdf_path, pages)
    text = m.extract_text(pdf_path)
    lines = text.split("\n")
    assert len(lines) == m.MAX_PAGES_TO_SCAN
    assert lines[-1] == f"PAGE{m.MAX_PAGES_TO_SCAN}"


def test_extract_text_empty_page_yields_empty_string(tmp_path: Path) -> None:
    pdf_path = tmp_path / "test.pdf"
    write_pdf(pdf_path, [[]])
    assert m.extract_text(pdf_path) == ""
