#!/usr/bin/env python3
"""レイクに保存された EDINET 書類一覧 API の生レスポンス（document_list_{date}.json）を
走査し、全 results[] レコードを 1 つの CSV ストリーム（stdout）に平坦化する。
PostgreSQL の file_fdw の `program` オプションから起動され、外部テーブル
`raw.edinet_document_index` の実体となる。

レイク上のパス:
    {LAKE_ROOT}/edinet-dl/raw/response/document_list_{YYYY-MM-DD}.json

各ファイルは `{"metadata": {...}, "results": [ {...29 キー...}, ... ]}`。results[] の
キー集合は固定で、値は文字列・null・（seqNumber のみ）整数。null は空文字にする。

出力（stdout）は 30 列の CSV（全フィールドをクォート、行終端 LF）。列順は
OUTPUT_HEADER のとおり: file_date + RESULT_KEYS を snake_case にしたもの。

使い方:
    python3 edinet_docindex_fdw.py [LAKE_ROOT]   # LAKE_ROOT 既定は /lake

壊れた JSON・results 欠落は stderr に警告して読み飛ばし、走査は止めない。
"""

from __future__ import annotations

import csv
import json
import re
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

DEFAULT_LAKE_ROOT = "/lake"

_FILENAME_RE = re.compile(r"^document_list_(\d{4}-\d{2}-\d{2})\.json$")

# results[] レコードのキー（EDINET API の並び）。この順で値を取り出す。
RESULT_KEYS = [
    "seqNumber",
    "docID",
    "edinetCode",
    "secCode",
    "JCN",
    "filerName",
    "fundCode",
    "ordinanceCode",
    "formCode",
    "docTypeCode",
    "periodStart",
    "periodEnd",
    "submitDateTime",
    "docDescription",
    "issuerEdinetCode",
    "subjectEdinetCode",
    "subsidiaryEdinetCode",
    "currentReportReason",
    "parentDocID",
    "opeDateTime",
    "withdrawalStatus",
    "docInfoEditStatus",
    "disclosureStatus",
    "xbrlFlag",
    "pdfFlag",
    "attachDocFlag",
    "englishDocFlag",
    "csvFlag",
    "legalStatus",
]

# 外部テーブルの列名（file_fdw 側の CREATE FOREIGN TABLE と一致させること）。
OUTPUT_HEADER = [
    "file_date",
    "seq_number",
    "doc_id",
    "edinet_code",
    "sec_code",
    "jcn",
    "filer_name",
    "fund_code",
    "ordinance_code",
    "form_code",
    "doc_type_code",
    "period_start",
    "period_end",
    "submit_date_time",
    "doc_description",
    "issuer_edinet_code",
    "subject_edinet_code",
    "subsidiary_edinet_code",
    "current_report_reason",
    "parent_doc_id",
    "ope_date_time",
    "withdrawal_status",
    "doc_info_edit_status",
    "disclosure_status",
    "xbrl_flag",
    "pdf_flag",
    "attach_doc_flag",
    "english_doc_flag",
    "csv_flag",
    "legal_status",
]


class RowWriter(Protocol):
    """csv.writer 互換の最小インターフェース。"""

    def writerow(self, row: list[str]) -> object: ...


def iter_response_files(lake_root: Path) -> Iterator[Path]:
    """レイク配下の document_list_*.json をファイル名順に列挙する。"""
    base = lake_root / "edinet-dl" / "raw" / "response"
    yield from sorted(base.glob("document_list_*.json"))


def file_date_from_path(path: Path) -> str:
    """document_list_{YYYY-MM-DD}.json のファイル名から日付文字列を取り出す。"""
    match = _FILENAME_RE.match(path.name)
    if match is None:
        raise ValueError(f"想定外のレスポンスファイル名: {path.name}")
    return match.group(1)


def _cell(value: object) -> str:
    """JSON の値を CSV セルへ。null は空文字、それ以外は str()。"""
    if value is None:
        return ""
    return str(value)


def iter_records(path: Path) -> Iterator[list[str]]:
    """1 つの JSON を開き、results[] の各レコードを RESULT_KEYS 順の値リストで yield する。"""
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    results = payload.get("results") or []
    for rec in results:
        yield [_cell(rec.get(key)) for key in RESULT_KEYS]


def run(lake_root: Path, out: RowWriter) -> int:
    """走査して out へ書き出す。戻り値は読み飛ばしたファイル数。"""
    skipped = 0
    for path in iter_response_files(lake_root):
        try:
            file_date = file_date_from_path(path)
            for values in iter_records(path):
                out.writerow([file_date, *values])
        except BrokenPipeError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"warning: skip {path}: {e}", file=sys.stderr)
            skipped += 1
    return skipped


def _restore_default_sigpipe() -> None:
    """パイプ下流（file_fdw）が LIMIT 等で読み切らずに閉じたとき、SIGPIPE で素直に
    終了させる。既定の Python 動作（BrokenPipeError → 終了コード 120）を避ける。"""
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def main(argv: list[str]) -> int:
    _restore_default_sigpipe()
    lake_root = Path(argv[1] if len(argv) > 1 else DEFAULT_LAKE_ROOT)
    writer = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL, lineterminator="\n")
    try:
        skipped = run(lake_root, writer)
    except BrokenPipeError:
        return 0
    if skipped:
        print(f"warning: {skipped} 個のレスポンスファイルを読み飛ばした", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
