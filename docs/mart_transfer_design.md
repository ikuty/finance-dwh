# mart 転送設計（2026-10-31）

dbt build 完了後、mart の Parquet を ikuty.com（VPS）へ転送する。VPS 上の MCP サーバ
（`finance-poc/mcp-mart`）が「常に最新の mart」を前提に参照する。実装は
`transform/flows/mart_transfer.py`（Mac Mini 側）と `vps/`（VPS 側）。

## 全体像

| 順 | 場所 | 処理 | 失敗時 |
|---|---|---|---|
| 0 | Mac Mini / transform | `daily_transform` が dbt build 直後に `MART_ROOT/_BUILD_RESULT.json`（`ok`・`finished_at`）を書く | - |
| 1 | Mac Mini / mart-transfer | マーカー確認（`ok=true` かつ `MART_TRANSFER_MAX_BUILD_AGE_HOURS`=6 時間以内） | 送らず終了コード1・Slack 通知 |
| 2 | 同上 | 転送対象を dbt の mart モデル(`dbt/models/mart/*.sql` の `location=`)から導出。期待ファイルが欠けていれば中止。対象を DuckDB で読み、0行・破損が無いか確認。sha256・行数入り `MANIFEST.json` を作る | 同上 |
| 3 | 同上 → VPS | rsync（SSH 鍵認証）で `incoming/<run_id>/` へ。`--partial`・最大3回再試行 | VPS の `current` は変わらない |
| 4 | VPS / `vps/activate_mart.sh` | manifest とファイル構成・サイズ・sha256 を再検証 → `releases/<run_id>/` へ移動 → `current` symlink を `mv -T` で原子的に置換 → 古い世代を掃除 | 検証失敗なら `current` に触れない |

`run_id` は UTC の `YYYYMMDDTHHMMSSZ`。VPS には `current` が常に「検証済みで完全な直近の mart」
だけを指す。送信が途中で切れても、検証が失敗しても、MCP は前日の mart を配信し続ける。

## 実行順序（systemd）

| 時刻 JST | unit | 備考 |
|---|---|---|
| 04:01:45 | `finance-dwh-transform` | dbt build |
| 04:01:46 | `finance-dwh-mart-transfer` | `After=finance-dwh-transform.service`、`TimeoutStartSec=600` |
| 04:01:48 | `dbt-docs-gen` | `After=` に mart-transfer を追加（mart 配信を docs より優先） |
| 04:02:00 | `finance-lake-shutdown` | **finance-lake 側の `After=` に `finance-dwh-mart-transfer.service` の追記が必要** |

Tapo の固定電源枠が尽きると転送は途中で止まる（`deployment_design.md`「落とし穴」節）。
mart を最優先で送るため docs-gen より前に置き、10分で打ち切る。

## VPS 側のセキュリティ

| 対策 | 内容 |
|---|---|
| 専用ユーザー | `martsync`（ログインシェル不要・パスワード無効）。転送専用の ed25519 鍵 |
| 強制コマンド | `authorized_keys` に `restrict,command="/srv/mart/bin/mart_gate.sh"`。許可は `rsync --server`（`rrsync -wo incoming`：書き込み専用・`incoming/` 配下のみ）と `activate <run_id>` のみ |
| ホスト鍵固定 | Mac Mini 側は `StrictHostKeyChecking=yes` + 事前に取得した `known_hosts`（初回接続での自動受け入れをしない） |
| 入力検証 | `run_id` は正規表現で検証（パス操作・コマンド注入を防ぐ） |
| データ | JPX・kabu.com 由来で個人利用限定。VPS 上の公開範囲は MCP 側の認証で制御する |

実機（Linux コンテナ上の sshd + rrsync）で、任意コマンド・`..` を含むパス・読み出しが
拒否されること、sha256 不一致の `activate` が `current` を変えないことを確認済み。

## VPS 側の初回セットアップ

```
sudo useradd -m -s /bin/bash martsync && sudo passwd -l martsync
sudo mkdir -p /srv/mart/{bin,incoming,releases} && sudo chown -R martsync: /srv/mart
sudo install -m 755 -o martsync vps/mart_gate.sh vps/activate_mart.sh /srv/mart/bin/
sudo apt install rsync python3        # rrsync は rsync パッケージに含まれる
# ~martsync/.ssh/authorized_keys（mode 600）に1行:
#   restrict,command="/srv/mart/bin/mart_gate.sh" ssh-ed25519 AAAA... finance-dwh-mart-transfer
```

MCP コンテナの mart マウントは `/srv/mart` を `:ro` で親ごと渡す（`current` が相対 symlink のため）。
切替後に実行したい処理（例: MCP コンテナの再起動）は `/srv/mart/bin/post_activate.sh` に置く
（実行可能なら有効化後に呼ばれる。失敗しても切替は巻き戻さない）。

## Mac Mini 側のセットアップ

```
ssh-keygen -t ed25519 -N "" -f /home/ikuty/finance-dwh/.ssh-mart-transfer/id_ed25519
ssh-keyscan -t ed25519 ikuty.com > /home/ikuty/finance-dwh/.ssh-mart-transfer/known_hosts
# known_hosts は VPS 上の /etc/ssh/ssh_host_ed25519_key.pub と突き合わせて確認すること
sudo cp systemd/finance-dwh-mart-transfer.{service,timer} /etc/systemd/system/
sudo cp systemd/dbt-docs-gen.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now finance-dwh-mart-transfer.timer
```

`compose/.env` に追加:

| 変数 | 内容 |
|---|---|
| `MART_TRANSFER_HOST` | `ikuty.com`（未設定なら転送をスキップ） |
| `MART_TRANSFER_USER` | 既定 `martsync` |
| `MART_TRANSFER_PORT` | 既定 22 |
| `MART_TRANSFER_SSH_DIR` | 鍵と known_hosts のホスト側ディレクトリ（既定 `../.ssh-mart-transfer`、git 管理外） |

イメージに `openssh-client`・`rsync` を追加した（`transform/Dockerfile`）ため、反映には
build-push → deploy でイメージの更新が必要。

## 転送対象の決め方（2026-10-31）

`MART_ROOT/*.parquet` を丸ごと送ると、廃止したモデルの古いファイルが毎回 VPS へ紛れ込む
（実際に旧 `jpx_edinet_valuation_indicators.parquet` が約5週間残り、転送されていた）。
出力名の正は dbt のモデル定義なので、イメージに焼き込まれた `models/mart/*.sql` の
`location=env_var('MART_ROOT', ...) ~ '/xxx.parquet'` から導出する。

| ケース | 挙動 |
|---|---|
| MART_ROOT に dbt モデルに無い Parquet がある | 送らず、警告ログを出す（削除してよいファイル） |
| 期待する Parquet が MART_ROOT に無い | 転送を中止（不完全な mart を `current` にしない） |
| モデルから出力名を1つも取れない | 転送を中止（SQL の書式変更等。`test_expected_mart_files_matches_real_dbt_models` が CI で検知する） |

mart モデルを追加するときは `location=env_var('MART_ROOT', ...) ~ '/xxx.parquet'` の書式を守る
こと。`manifest.json` ではなくモデルの SQL を読むのは、mart-transfer コンテナに dbt の
`target/` をマウントしていない（イメージに焼き込んだ `/app/dbt` だけが見える）ため。

## 既知の制約

| 項目 | 内容 |
|---|---|
| 転送量 | 毎回 mart 全体（現状 約200MB、日次バリュエーションが大半）。初回実機テストは 181秒（約1.2MB/s、rsync が約176秒）。Parquet は丸ごと再生成され rsync の差分が効きにくいため全量送信 |
| 手動実行 | `docker compose run --rm mart-transfer`。マーカーが古いと見送られる（`MART_TRANSFER_MAX_BUILD_AGE_HOURS` で調整） |
| 世代管理 | VPS は直近3世代を保持（`MART_KEEP_RELEASES`）。ロールバックは `current` を張り替えるだけ |
| 未検証 | Mac Mini 実機・実 VPS での通し実行、実データ量での所要時間は未計測 |
