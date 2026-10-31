#!/usr/bin/env bash
# VPS(ikuty.com)側: incoming/<run_id>/ に届いた mart を検証し、current を原子的に切り替える。
#   incoming/<run_id>/ -> (MANIFEST.json と sha256/サイズを検証) -> releases/<run_id>/
#   current symlink を rename(2) で置換 -> 古い世代と古い incoming を掃除
# 検証に失敗したら current には一切触れない（MCPサーバは古い mart を配信し続ける）。
#
# 使い方: activate_mart.sh <run_id>   run_id 形式: 20261031T190102Z
# 環境変数: MART_BASE(既定 /srv/mart) / MART_KEEP_RELEASES(既定 3)
# GNU coreutils(mv -T) と python3 が前提。
#
# 切替後に実行したい処理（例: MCPコンテナの再起動）があれば $MART_BASE/bin/post_activate.sh
# を置く。実行可能なら current 切替後に呼ばれる（失敗しても切替は巻き戻さない）。
set -euo pipefail

run_id=${1:?run_id}
[[ $run_id =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "invalid run_id" >&2; exit 2; }

base=${MART_BASE:-/srv/mart}
keep=${MART_KEEP_RELEASES:-3}
src="$base/incoming/$run_id"
dst="$base/releases/$run_id"
[[ -d $src ]] || { echo "no such incoming: $run_id" >&2; exit 3; }

python3 - "$src" <<'PY'
import hashlib, json, os, sys
d = sys.argv[1]
m = json.load(open(os.path.join(d, "MANIFEST.json")))
want = {f["name"]: f for f in m["files"]}
have = {n for n in os.listdir(d) if n.endswith(".parquet")}
if have != set(want):
    sys.exit(f"file set mismatch: missing={sorted(set(want)-have)} extra={sorted(have-set(want))}")
for name, f in want.items():
    p = os.path.join(d, name)
    if os.path.getsize(p) != f["size"]:
        sys.exit(f"size mismatch: {name}")
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != f["sha256"]:
        sys.exit(f"sha256 mismatch: {name}")
print(f"verified {len(want)} files")
PY

mkdir -p "$base/releases"
mv "$src" "$dst"

ln -s "releases/$run_id" "$base/current.tmp"
mv -T "$base/current.tmp" "$base/current"
echo "current -> $(readlink "$base/current")"

# 古い世代を削除（current が指す最新は必ず残る）
ls -1d "$base"/releases/*/ | head -n -"$keep" | xargs -r rm -rf
# 検証失敗等で残った古い incoming(2日超)を削除
find "$base/incoming" -mindepth 1 -maxdepth 1 -type d -mtime +2 -exec rm -rf {} +

if [[ -x $base/bin/post_activate.sh ]]; then
  "$base/bin/post_activate.sh" || echo "post_activate failed (current は切替済み)" >&2
fi
