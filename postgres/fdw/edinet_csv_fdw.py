#!/usr/bin/env python3
"""レイクの EDINET CSV（type=5、UTF-16LE・タブ区切り）を走査し、全書類の明細行を
1つの CSV ストリーム（stdout）に平坦化する。PostgreSQL の file_fdw の `program`
オプションから起動され、外部テーブル `raw.raw__edinet_csv_facts` の実体となる。

レイク上のパス:
    {LAKE_ROOT}/edinet-dl/raw/{yyyy}/{mm}/{dd}/{edinetCode}/csv/{docID}/{name}.csv.gz

各 .csv.gz は EDINET CSV 仕様どおり「BOM 付き UTF-16LE・CRLF・タブ区切り・全フィールドを
ダブルクォートで囲む・固定 9 列」。値にはテキストブロック（改行を含む長文）が入りうるため、
行単位のテキスト処理ではなく csv モジュールでパースする。

出力（stdout）は 12 列の CSV（全フィールドをクォート、行終端 LF）:
    file_date, edinet_code, doc_id,
    element_id, item_name, context_id, relative_year, consolidated_individual,
    period_instant, unit_id, unit, value

使い方:
    python3 edinet_csv_fdw.py [LAKE_ROOT]                # 全期間（file_fdw から使われる形）
    python3 edinet_csv_fdw.py [LAKE_ROOT] --date 2026-09-10   # その日だけ（landing への load 用）

`--date` を付けると走査を `raw/{yyyy}/{mm}/{dd}/` 配下だけに絞る。file_fdw は述語プッシュ
ダウン不可でフルスキャンが重いため、日付単位の取り込みはこの引数付きで呼び出す。

破損ファイル（gzip でない・途中で切れている等）は stderr に警告を出して読み飛ばし、
走査全体は止めない（1 ファイルの不整合で外部テーブル全体が読めなくなるのを避ける）。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

DEFAULT_LAKE_ROOT = "/lake"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# EDINET CSV の明細列数（要素ID/項目名/コンテキストID/相対年度/連結・個別/期間・時点/
# ユニットID/単位/値）。
N_DATA_COLS = 9

# stdout に出す全列（provenance 3 列 + 明細 9 列）。file_fdw 側の CREATE FOREIGN TABLE と
# 列順を一致させること。
OUTPUT_HEADER = [
    "file_date",
    "edinet_code",
    "doc_id",
    "element_id",
    "item_name",
    "context_id",
    "relative_year",
    "consolidated_individual",
    "period_instant",
    "unit_id",
    "unit",
    "value",
]


class RowWriter(Protocol):
    """csv.writer 互換の最小インターフェース（テストから差し替え可能にするため）。"""

    def writerow(self, row: list[str]) -> object: ...


def iter_csv_files(lake_root: Path, date: str | None = None) -> Iterator[Path]:
    """レイク配下の EDINET CSV(.csv.gz) をパス順に列挙する。

    date（'YYYY-MM-DD'）を渡すと raw/{yyyy}/{mm}/{dd}/ 配下だけに絞る。
    """
    base = lake_root / "edinet-dl" / "raw"
    # {yyyy}/{mm}/{dd}/{edinetCode}/csv/{docID}/*.csv.gz
    if date is None:
        pattern = "*/*/*/*/csv/*/*.csv.gz"
    else:
        yyyy, mm, dd = date.split("-")
        pattern = f"{yyyy}/{mm}/{dd}/*/csv/*/*.csv.gz"
    yield from sorted(base.glob(pattern))


def provenance_from_path(path: Path, lake_root: Path) -> tuple[str, str, str]:
    """.csv.gz のパスから (file_date, edinet_code, doc_id) を取り出す。

    期待するパス構造:
        {lake_root}/edinet-dl/raw/{yyyy}/{mm}/{dd}/{edinetCode}/csv/{docID}/{name}.csv.gz
    """
    rel = path.relative_to(lake_root / "edinet-dl" / "raw")
    parts = rel.parts
    # parts = (yyyy, mm, dd, edinetCode, "csv", docID, name.csv.gz)
    if len(parts) != 7 or parts[4] != "csv":
        raise ValueError(f"想定外の CSV パス構造: {path}")
    file_date = f"{parts[0]}-{parts[1]}-{parts[2]}"
    return file_date, parts[3], parts[5]


def normalize_row(row: list[str]) -> list[str]:
    """明細行を必ず 9 列にそろえる。

    - 9 列ちょうど: そのまま
    - 不足: 空文字で右詰め（値の欠落を握りつぶさないため読み飛ばさない）
    - 超過: クォート外に漏れたタブが原因とみなし、余りを最終列へ連結して戻す
    """
    if len(row) == N_DATA_COLS:
        return row
    if len(row) < N_DATA_COLS:
        return row + [""] * (N_DATA_COLS - len(row))
    return row[: N_DATA_COLS - 1] + ["\t".join(row[N_DATA_COLS - 1 :])]


def iter_data_rows(path: Path) -> Iterator[list[str]]:
    """1 つの .csv.gz を開き、ヘッダを除いた明細行（9 列に正規化済み）を yield する。"""
    with gzip.open(path, mode="rt", encoding="utf-16", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        next(reader, None)  # ヘッダ行を捨てる
        for row in reader:
            if not row:
                continue
            yield normalize_row(row)


def run(lake_root: Path, out: RowWriter, date: str | None = None) -> int:
    """走査して out へ書き出す。戻り値は読み飛ばした破損ファイル数。"""
    skipped = 0
    for path in iter_csv_files(lake_root, date):
        try:
            file_date, edinet_code, doc_id = provenance_from_path(path, lake_root)
            for row in iter_data_rows(path):
                out.writerow([file_date, edinet_code, doc_id, *row])
        except BrokenPipeError:
            raise  # 下流がパイプを閉じた。読み飛ばしとして数えず即座に抜ける。
        except (OSError, EOFError, UnicodeError, ValueError, csv.Error) as e:
            print(f"warning: skip {path}: {e}", file=sys.stderr)
            skipped += 1
    return skipped


def _restore_default_sigpipe() -> None:
    """パイプ下流（file_fdw）が LIMIT 等で読み切らずに閉じたとき、SIGPIPE で素直に
    終了させる。既定の Python 動作（BrokenPipeError → 終了コード 120）を避ける。"""
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EDINET CSV をレイクから平坦化して stdout へ")
    parser.add_argument("lake_root", nargs="?", default=DEFAULT_LAKE_ROOT)
    parser.add_argument("--date", help="'YYYY-MM-DD'。指定日だけを走査する")
    args = parser.parse_args(argv[1:])
    if args.date is not None and not _DATE_RE.match(args.date):
        parser.error(f"--date は YYYY-MM-DD 形式で: {args.date!r}")
    return args


def main(argv: list[str]) -> int:
    _restore_default_sigpipe()
    args = _parse_args(argv)
    writer = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL, lineterminator="\n")
    try:
        skipped = run(Path(args.lake_root), writer, args.date)
    except BrokenPipeError:
        # 下流（例: `| head`）がパイプを閉じた。SIGPIPE 相当として静かに終わる。
        return 0
    if skipped:
        print(f"warning: {skipped} 個の CSV を読み飛ばした", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
