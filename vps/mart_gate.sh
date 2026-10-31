#!/usr/bin/env bash
# VPS(ikuty.com)側: 転送用SSH鍵の強制コマンド。この鍵で許可する操作を2つだけに絞る。
#   1. rsync の受信（書き込み専用、incoming/ 配下のみ。rrsync が範囲外パスを拒否する）
#   2. `activate <run_id>`（vps/activate_mart.sh の実行）
#
# ~martsync/.ssh/authorized_keys への登録例（1行）:
#   restrict,command="/srv/mart/bin/mart_gate.sh" ssh-ed25519 AAAA... finance-dwh-mart-transfer
#
# `restrict` で pty・ポート転送・agent転送等を全て禁止する。
set -euo pipefail

BASE=${MART_BASE:-/srv/mart}
cmd=${SSH_ORIGINAL_COMMAND:-}

case "$cmd" in
  "activate "*)
    exec "$BASE/bin/activate_mart.sh" "${cmd#activate }"
    ;;
  "rsync --server "*)
    # rrsync は SSH_ORIGINAL_COMMAND を読んで検証し、-wo(書き込み専用)・ルート固定で実行する
    exec rrsync -wo "$BASE/incoming"
    ;;
  *)
    echo "denied" >&2
    exit 126
    ;;
esac
