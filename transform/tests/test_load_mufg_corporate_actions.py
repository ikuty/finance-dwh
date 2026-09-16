"""load_mufg_corporate_actions.py の単体テスト。

`mufg_corporate_actions.py`のHTML解析ロジック自体は`test_mufg_corporate_actions.py`
で個別に検証済みのため、ここではload_mufg_corporate_actions.py自身の責務
（取り込み対象日の判定・3ページ→3landingテーブルへの書き分け）に絞って検証する。
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from flows import load_mufg_corporate_actions as m

_SPLIT_HTML = """<html><body><main><table>
<tr><th>割当日</th><th>銘柄コード</th><th>銘柄名</th><th>割当比率</th>
<th>権利付最終日</th><th>効力発生日</th><th>売却可能予定日</th></tr>
<tr><td>2026/09/30</td><td>6465</td><td>ホシザキ</td><td>1：2</td>
<td>2026/09/28</td><td>2026/10/01</td><td>2026/09/29</td></tr>
</table></main></body></html>"""

_CONSOLIDATION_HTML = """<html><body><main><table>
<tr><th>効力発生日</th><th>銘柄コード</th><th>銘柄名</th><th>併合比率</th><th>権利付最終日</th></tr>
<tr><td>2026/11/01</td><td>6574</td><td>コンヴァノ</td><td>10株→1株</td><td>2026/10/28</td></tr>
</table></main></body></html>"""

_NAME_CHANGE_HTML = """<html><body><main><table>
<tr><th>変更日</th><th>銘柄コード</th><th>旧商号</th><th>新商号</th></tr>
<tr><td>2027/07/01</td><td>2904</td><td>一正蒲鉾</td><td>一正ホールディングス</td></tr>
</table></main></body></html>"""


def make_day(lake: Path, date: str, *, missing: str | None = None) -> None:
    yyyy, mm, dd = date.split("-")
    day_dir = lake / "mufg-corporate-actions" / "raw" / yyyy / mm / dd
    day_dir.mkdir(parents=True, exist_ok=True)
    contents = {"bunkatu": _SPLIT_HTML, "gensi": _CONSOLIDATION_HTML, "syougou_henkou": _NAME_CHANGE_HTML}
    for fmt, html in contents.items():
        if fmt == missing:
            continue
        (day_dir / f"{fmt}.html").write_text(html, encoding="utf-8")


def parquet_rows(path: Path) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        return con.execute(f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)").fetchall()
    finally:
        con.close()


# --- disk_dates / landing_logged_dates / dates_to_load --------------------------


def test_disk_dates_requires_all_three_pages(tmp_path: Path) -> None:
    make_day(tmp_path, "2026-09-14")
    make_day(tmp_path, "2026-09-21", missing="gensi")  # 1ファイル欠けているので対象外

    assert m.disk_dates(tmp_path) == {"2026-09-14"}


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


def test_landing_logged_dates_requires_splits_part_parquet(tmp_path: Path) -> None:
    done_dir = tmp_path / "mufg_stock_splits" / "file_date=2026-09-14"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")
    partial_dir = tmp_path / "mufg_stock_splits" / "file_date=2026-09-21"
    partial_dir.mkdir(parents=True)
    (partial_dir / "part.parquet.tmp").write_bytes(b"x")

    assert m.landing_logged_dates(tmp_path) == {"2026-09-14"}


def test_dates_to_load_excludes_already_loaded(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_day(lake, "2026-09-14")
    make_day(lake, "2026-09-21")
    done_dir = landing / "mufg_stock_splits" / "file_date=2026-09-14"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")

    assert m.dates_to_load(lake, landing) == ["2026-09-21"]


# --- load_one_date ----------------------------------------------------------------


def test_load_one_date_writes_three_landing_tables(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_day(lake, "2026-09-14")

    counts = m.load_one_date(lake, landing, "2026-09-14")

    assert counts == {
        "mufg_stock_splits": 1,
        "mufg_stock_consolidations": 1,
        "mufg_company_name_changes": 1,
    }

    splits_out = landing / "mufg_stock_splits" / "file_date=2026-09-14" / "part.parquet"
    assert splits_out.exists() and not splits_out.with_suffix(".parquet.tmp").exists()
    rows = parquet_rows(splits_out)
    assert len(rows) == 1
    columns = [
        d[0]
        for d in duckdb.connect()
        .execute(f"select * from read_parquet('{splits_out.as_posix()}', hive_partitioning=false) limit 0")
        .description
    ]
    row = dict(zip(columns, rows[0], strict=True))
    assert row["code"] == "6465"
    assert row["file_date"] == "2026-09-14"
    assert row["_loaded_at"] is not None

    consolidations_out = landing / "mufg_stock_consolidations" / "file_date=2026-09-14" / "part.parquet"
    assert len(parquet_rows(consolidations_out)) == 1

    name_changes_out = landing / "mufg_company_name_changes" / "file_date=2026-09-14" / "part.parquet"
    assert len(parquet_rows(name_changes_out)) == 1
