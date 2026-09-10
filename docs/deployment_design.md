# デプロイ・実行基盤設計（2026-09-10）

`finance-lake` の `services/edinet-dl/docs/deployment_design.md` と同じ方針
（ビルドとデプロイを分離、ghcr.io、Tailscale+SSH、`latest` タグのみ）。ここでは
finance-dwh 固有の点だけ記す。

## Mac Mini 上のパス

| | パス |
|---|---|
| リポジトリ | `/home/ikuty/finance-dwh` |
| compose | `/home/ikuty/finance-dwh/compose/docker-compose.yml`（+ 同ディレクトリの `.env`） |
| Postgres データ | `/home/ikuty/finance-dwh/data/pgdata`（HDD 側 `/home` 配下） |
| レイク（read-only mount 元） | `/home/ikuty/finance-lake/data` → コンテナ内 `/lake:ro` |
| systemd unit | `/etc/systemd/system/finance-dwh-{postgres.service,transform.service,transform.timer}` |

## `compose/.env`（deploy 時に手で用意、git 管理外）

```
LAKE_DIR=/home/ikuty/finance-lake/data
PGDATA_DIR=/home/ikuty/finance-dwh/data/pgdata
POSTGRES_USER=finance
POSTGRES_PASSWORD=<任意>
POSTGRES_DB=finance_dwh
DBT_TARGET=prod
S3_BUCKET_NAME=ikuty-finance
AWS_ACCESS_KEY_ID=<finance-lake と共用の IAM ユーザー>
AWS_SECRET_ACCESS_KEY=<同上>
AWS_DEFAULT_REGION=ap-northeast-1
SLACK_WEBHOOK_URL=<finance-lake と共用>
```

- S3 バケット（`ikuty-finance`、静的サイトホスティング、7 日ライフサイクル）と IAM ユーザーは
  `finance-lake` で作成済みのものを共用。レポートのキーは `finance-dwh/run_report.html`
  （lake は `edinet-dl/…` / `jpx-daily-pdf-dl/…`。キーで名前空間分離）。
- `S3_BUCKET_NAME` / `SLACK_WEBHOOK_URL` 未設定なら該当ステップは静かにスキップされる。

## GitHub Actions

| ワークフロー | トリガー | 内容 |
|---|---|---|
| `finance-dwh-ci.yml` | push(main) / PR | `mypy --strict` + `pytest` + `dbt parse` |
| `finance-dwh-build-push.yml` | push(main, paths: postgres/transform/dbt) | `finance-dwh-postgres` / `finance-dwh-transform` を ghcr.io へ push |
| `finance-dwh-deploy.yml` | `workflow_dispatch`（手動） | Tailscale+SSH で `git pull` + `docker pull` + ローカルタグへ retag |
| `gitleaks.yml` | push(main) | 秘密情報スキャン |

### Secrets（新リポジトリに登録が必要）

`finance-lake` と同種だがリポジトリ独立: `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` /
`SSH_PRIVATE_KEY` / `MAC_MINI_HOST` / `MAC_MINI_USER`。

### イメージタグの retag

compose / systemd はレジストリ接頭辞の無いローカルタグ（`finance-dwh-postgres:latest` /
`finance-dwh-transform:latest`）を参照する。deploy は `docker pull ghcr.io/...` の後に
同名タグへ `docker tag` する（edinet-dl で実機確認済みの不具合対策）。

### 反映タイミング

- **transform**: `docker compose run --rm` 方式なので、次回の systemd タイマー実行から
  新イメージが使われる（再起動不要）。
- **postgres**: 次回起動時に `finance-dwh-postgres.service` が `docker compose up -d` で
  イメージ差分を検知して再作成する。即時反映は `sudo systemctl restart
  finance-dwh-postgres.service`（DB が一時停止する点に注意）。

## systemd unit と実行順序

- `finance-dwh-postgres.service`: `Type=oneshot` + `RemainAfterExit=yes`。boot 時に
  `enable` で自動起動し `docker compose up -d postgres`、停止時に `ExecStop` で `stop`。
- `finance-dwh-transform.service`: `Type=oneshot`。`docker compose run --rm transform`。
  `After=` に docker / finance-dwh-postgres / edinet-dl / jpx-daily-pdf-dl を列挙
  （順序制約のみ、`Requires=` は finance-dwh-postgres だけ）。
- `finance-dwh-transform.timer`: `OnCalendar=*-*-* 04:01:45 Asia/Tokyo`、`Persistent=true`。

タイミング: **jpx 04:01:00 → edinet 04:01:30 → transform 04:01:45 → shutdown 04:02:00**。
transform のタイマーを lake の両タイマーより後にするのは、遅延起動時に取得ジョブより先に
発火して `After=` が空振りするのを避けるため。shutdown のタイマーより前にするのは、
shutdown 側の `After=` がこのサービスを「実行中」と認識できるようにするため。

### finance-lake 側で必要な変更（要実施）

`finance-lake/systemd/finance-lake-shutdown.service` の
`After=edinet-dl.service jpx-daily-pdf-dl.service` に **`finance-dwh-transform.service` を
追記する**。追記しないと DWH ジョブ完了前に halt されうる。同ファイルのコメント
（タイマー時刻の説明）も更新すること。

## 初回セットアップ手順（Mac Mini）

1. `git clone <repo> /home/ikuty/finance-dwh`
2. `compose/.env` を上記の内容で作成
3. `cd /home/ikuty/finance-dwh/compose && docker compose build`（または deploy を一度回して
   ghcr.io から pull + retag）
4. `sudo cp systemd/finance-dwh-*.{service,timer} /etc/systemd/system/`
   （テンプレートのパスは既に `/home/ikuty/...` で確定済み。`<user>` プレースホルダは無い）
5. `sudo systemctl daemon-reload`
6. `sudo systemctl enable --now finance-dwh-postgres.service`
7. `sudo systemctl enable --now finance-dwh-transform.timer`
8. 動作確認: `sudo systemctl start finance-dwh-transform.service` →
   `journalctl -u finance-dwh-transform.service` で dbt 実行とレポート URL を確認、
   Slack にサマリ + `http://ikuty-finance.s3-website-ap-northeast-1.amazonaws.com/finance-dwh/run_report.html`
9. 別の朝に `systemctl list-timers` と `journalctl -u finance-lake-shutdown.service` で
   transform 完了 → halt の順序を確認

## 実装状況（2026-09-11 時点）

- ローカル: pytest / mypy --strict / `docker compose` フルフロー緑。
- 未了: GitHub リポジトリ作成 + Secrets、Mac Mini 初回セットアップ、finance-lake の
  shutdown unit 追記。
