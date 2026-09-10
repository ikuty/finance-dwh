#!/usr/bin/env python3
"""レイクに保存された JPX 日次株式相場表 PDF/TIFF の**ファイル目録**を 1 つの CSV
ストリーム（stdout）として出力する。PostgreSQL の file_fdw の `program` オプションから
起動され、外部テーブル `raw.raw__jpx_file_catalog` の実体となる。

JPX 分は PDF / スキャン画像（TIFF）であり内容は SQL で扱えないため、raw 層では
「どのファイルがどの期間・形式で存在するか」の目録だけを提供する（内容抽出は対象外）。

レイク上のパス（形式ごとにトップレベルを分離。詳細は finance-lake 側の
jpx-daily-pdf-dl/docs/file_download_design.md）:
    {LAKE_ROOT}/jpx-daily-pdf-dl/raw/legacy-daily/{yyyy}/{mm}/{dd}/stq.pdf   (形式A: 1999-04〜2019-12)
    {LAKE_ROOT}/jpx-daily-pdf-dl/raw/legacy-daily/{yyyy}/{mm}/{dd}/stq.tif   (形式A: 1981-01〜1999-03)
    {LAKE_ROOT}/jpx-daily-pdf-dl/raw/monthly-ohlc/{yyyy}/{mm}/stq_monthly.pdf (形式B: 2020-現在)
    {LAKE_ROOT}/jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf (形式C: 直近13ヶ月)

出力（stdout）は 7 列の CSV（全フィールドをクォート、行終端 LF）。列順は OUTPUT_HEADER:
    format, granularity, period, file_kind, relative_path, byte_size, modified_at

- period: 日次は 'YYYY-MM-DD'、月次は 'YYYY-MM'（jpx-daily-pdf-dl の fetch_progress と同じ粒度）
- modified_at: ファイル mtime を UTC の ISO 8601 で

使い方:
    python3 jpx_catalog_fdw.py [LAKE_ROOT]   # LAKE_ROOT 既定は /lake

想定外のパス・stat 失敗は stderr に警告して読み飛ばし、走査は止めない。
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

DEFAULT_LAKE_ROOT = "/lake"

_JPX_RAW = ("jpx-daily-pdf-dl", "raw")

# 形式ディレクトリ名 -> (粒度, グロブ, 期間を構成するパス階層数)
# path_levels は {format} ディレクトリ配下の階層数（ファイル名を含む）。
_DAILY = "daily"
_MONTHLY = "monthly"
FORMATS: dict[str, tuple[str, str, int]] = {
    "legacy-daily": (_DAILY, "*/*/*/stq.*", 4),
    "detailed-daily": (_DAILY, "*/*/*/stq.pdf", 4),
    "monthly-ohlc": (_MONTHLY, "*/*/stq_monthly.pdf", 3),
}

_KIND_BY_SUFFIX = {".pdf": "pdf", ".tif": "tif"}

_YYYY = re.compile(r"^\d{4}$")
_MM_DD = re.compile(r"^\d{2}$")

OUTPUT_HEADER = [
    "format",
    "granularity",
    "period",
    "file_kind",
    "relative_path",
    "byte_size",
    "modified_at",
]


class RowWriter(Protocol):
    """csv.writer 互換の最小インターフェース。"""

    def writerow(self, row: list[str]) -> object: ...


def iter_catalog_files(lake_root: Path) -> Iterator[tuple[str, Path]]:
    """(format 名, ファイルパス) を relative path 順に列挙する。"""
    base = lake_root.joinpath(*_JPX_RAW)
    found: list[tuple[str, Path]] = []
    for fmt, (_granularity, glob, _levels) in FORMATS.items():
        for path in base.glob(f"{fmt}/{glob}"):
            if path.suffix.lower() in _KIND_BY_SUFFIX:
                found.append((fmt, path))
    found.sort(key=lambda pair: pair[1].as_posix())
    yield from found


def _period(granularity: str, parts: tuple[str, ...]) -> str:
    """{format} 配下のパス階層 parts（末尾はファイル名）から period 文字列を作る。"""
    if granularity == _DAILY:
        yyyy, mm, dd = parts[0], parts[1], parts[2]
        if not (_YYYY.match(yyyy) and _MM_DD.match(mm) and _MM_DD.match(dd)):
            raise ValueError(f"日次の年月日として解釈できない: {parts!r}")
        return f"{yyyy}-{mm}-{dd}"
    yyyy, mm = parts[0], parts[1]
    if not (_YYYY.match(yyyy) and _MM_DD.match(mm)):
        raise ValueError(f"月次の年月として解釈できない: {parts!r}")
    return f"{yyyy}-{mm}"


def row_for_file(lake_root: Path, fmt: str, path: Path) -> list[str]:
    """1 ファイルぶんの出力行（OUTPUT_HEADER 順）を作る。"""
    granularity, _glob, levels = FORMATS[fmt]
    rel_to_fmt = path.relative_to(lake_root.joinpath(*_JPX_RAW, fmt)).parts
    if len(rel_to_fmt) != levels:
        raise ValueError(f"想定外のパス階層数 {len(rel_to_fmt)}: {path}")
    period = _period(granularity, rel_to_fmt)
    stat = path.stat()
    modified_at = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat()
    relative_path = path.relative_to(lake_root).as_posix()
    return [
        fmt,
        granularity,
        period,
        _KIND_BY_SUFFIX[path.suffix.lower()],
        relative_path,
        str(stat.st_size),
        modified_at,
    ]


def run(lake_root: Path, out: RowWriter) -> int:
    """走査して out へ書き出す。戻り値は読み飛ばしたファイル数。"""
    skipped = 0
    for fmt, path in iter_catalog_files(lake_root):
        try:
            out.writerow(row_for_file(lake_root, fmt, path))
        except BrokenPipeError:
            raise
        except (OSError, ValueError) as e:
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
        print(f"warning: {skipped} 個のファイルを読み飛ばした", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
