"""landing/ir_disclosure_kessan_facts を、保存済みのテキストから再抽出して更新する
一回限りの移行スクリプト(finance-dwh Issue #108)。

背景: 決算短信の配当表(dividend_*の15列)を抽出器に追加したが、追加前に書いた日付の
パーティションには配当列が無い。PDFを再度テキスト化せず、landing/ir_disclosure_kessan_text
に保存済みのテキストへ `ir_disclosure_kessan_facts.run` を再度通して、配当列を埋める。
タイトル・edinet_code・sec_codeはレイクのメタデータJSON({yyyy}/{mm}/{dd}/*/{docid}.json)
から取る(load_ir_disclosure_kessan.load_oneと同じ値)。

- 既に配当列を持つパーティションは何もしない(再実行しても安全)。
- 対応するメタデータJSONが無いdocidは、既存の行をそのまま残し、配当列だけNULLを足す。
- 配当列以外の列が再抽出の結果で変わった文書は、件数と列名を報告する(見出し判定の
  変更で他の項目が変わっていないかの確認用)。--dry-runなら書かずに報告だけ行う。
- 書き込みはload_ir_disclosure_kessanと同じく一時ファイル→renameの原子的置換。

実行例(Mac Mini、finance-dwh-transformコンテナ内):
    python -m flows.reextract_ir_disclosure_kessan_facts --dry-run
    python -m flows.reextract_ir_disclosure_kessan_facts
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from collections import Counter
from pathlib import Path

import duckdb
import ir_disclosure_kessan_facts as facts_stage
import pyarrow as pa

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")

DIVIDEND_COLUMNS = [c for c in facts_stage.FACT_COLUMNS if c.startswith("dividend_")]
_NON_COMPARED = {"_loaded_at", "file_date", *DIVIDEND_COLUMNS}


def _read_parquet(path: Path) -> list[dict[str, str | None]]:
    con = duckdb.connect()
    try:
        cur = con.execute(f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        con.close()


def _atomic_write_parquet(tbl: pa.Table, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "part.parquet.tmp"
    final_path = out_dir / "part.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM tbl) TO '{tmp_path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    with open(tmp_path, "rb") as f:
        os.fsync(f.fileno())
    tmp_path.replace(final_path)
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _load_meta(lake_root: Path, date: str) -> dict[str, dict[str, str]]:
    """対象日のメタデータJSONを docid -> 中身 で返す。"""
    yyyy, mm, dd = date.split("-")
    day = lake_root / "ir-disclosure-dl" / "raw" / yyyy / mm / dd
    out: dict[str, dict[str, str]] = {}
    for json_path in day.glob("*/*.json"):
        try:
            out[json_path.stem] = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def reextract_date(landing_root: Path, lake_root: Path, date: str, dry_run: bool = False) -> Counter[str]:
    """1日ぶんを再抽出する。統計(docs/skipped/no_meta/changed_docs/changed:<列名>)を返す。"""
    stats: Counter[str] = Counter()
    facts_dir = landing_root / "ir_disclosure_kessan_facts" / f"file_date={date}"
    text_path = landing_root / "ir_disclosure_kessan_text" / f"file_date={date}" / "part.parquet"
    facts_path = facts_dir / "part.parquet"

    old_rows = _read_parquet(facts_path)
    if old_rows and all(c in old_rows[0] for c in DIVIDEND_COLUMNS):
        stats["skipped_dates"] += 1
        return stats
    if not text_path.exists() or text_path.stat().st_size == 0:
        stats["no_text_dates"] += 1
        return stats

    old_by_docid = {str(r["docid"]): r for r in old_rows}
    texts = {str(r["docid"]): r["text"] or "" for r in _read_parquet(text_path)}
    metas = _load_meta(lake_root, date)

    new_rows: list[dict[str, str | None]] = []
    for docid, old in old_by_docid.items():
        meta = metas.get(docid)
        if meta is None or docid not in texts:
            stats["no_meta_or_text"] += 1
            new_rows.append({**old, **{c: None for c in DIVIDEND_COLUMNS}})
            continue
        title = meta.get("jpx_title") or meta.get("tdnet_title") or ""
        fact = facts_stage.run(
            texts[docid], docid, title, edinet_code=meta.get("edinet_code"), sec_code=meta.get("sec_code")
        )
        row: dict[str, str | None] = {c: getattr(fact, c) for c in facts_stage.FACT_COLUMNS}
        row["file_date"] = date
        row["_loaded_at"] = old.get("_loaded_at")
        new_rows.append(row)
        stats["docs"] += 1
        changed = [c for c in row if c not in _NON_COMPARED and row[c] != old.get(c)]
        if changed:
            stats["changed_docs"] += 1
            for c in changed:
                stats[f"changed:{c}"] += 1
        if any(row[c] is not None for c in DIVIDEND_COLUMNS):
            stats["with_dividend"] += 1

    if not dry_run and new_rows:
        # 列順は新規書き込み(load_one)と同じ: FACT_COLUMNS + file_date + _loaded_at
        cols = [*facts_stage.FACT_COLUMNS, "file_date", "_loaded_at"]
        tbl = pa.table({c: pa.array([r.get(c) for r in new_rows], type=pa.string()) for c in cols})
        _atomic_write_parquet(tbl, facts_dir)
        stats["written_dates"] += 1
    return stats


def landing_dates(landing_root: Path) -> list[str]:
    base = landing_root / "ir_disclosure_kessan_facts"
    return sorted(
        d.name.removeprefix("file_date=")
        for d in base.iterdir()
        if d.is_dir() and d.name.startswith("file_date=") and (d / "part.parquet").exists()
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="書き込まず、変更の統計だけ報告する")
    ap.add_argument("--from-date", help="この日付以降だけ処理する(YYYY-MM-DD)")
    ap.add_argument("--to-date", help="この日付以前だけ処理する(YYYY-MM-DD)")
    args = ap.parse_args(argv)

    landing_root, lake_root = Path(LANDING_ROOT), Path(LAKE_ROOT)
    dates = [
        d for d in landing_dates(landing_root)
        if (not args.from_date or d >= args.from_date) and (not args.to_date or d <= args.to_date)
    ]
    total: Counter[str] = Counter()
    started = datetime.datetime.now()
    for i, date in enumerate(dates, 1):
        total += reextract_date(landing_root, lake_root, date, dry_run=args.dry_run)
        if i % 100 == 0:
            print(f"  {i}/{len(dates)} 日処理 ({datetime.datetime.now() - started})", flush=True)
    print(f"対象 {len(dates)} 日 dry_run={args.dry_run}")
    for k in sorted(total):
        print(f"  {k}: {total[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
