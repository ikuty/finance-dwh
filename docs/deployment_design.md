# デプロイ・実行基盤設計（2026-09-11、DuckDB/Parquet 化）

`finance-lake` の `services/edinet-dl/docs/deployment_design.md` と同じ方針
（ビルドとデプロイを分離、ghcr.io、Tailscale+SSH、`latest` タグのみ）。ここでは
finance-dwh 固有の点だけ記す。

GitHub リポジトリ（public, `ikuty/finance-dwh`）・Secrets・Mac Mini への初回デプロイは
Postgres 版（2026-09-10〜11）で完了済み。本ドキュメントは DuckDB/Parquet 移行後の
状態を記す。

## Mac Mini 上のパス

| | パス |
|---|---|
| リポジトリ | `/home/ikuty/finance-dwh` |
| compose | `/home/ikuty/finance-dwh/compose/docker-compose.yml`（+ 同ディレクトリの `.env`） |
| DuckDB カタログ・landing・cleansed の Parquet | `/home/ikuty/finance-dwh/data/`（HDD 側 `/home` 配下） |
| レイク（read-only mount 元） | `/home/ikuty/finance-lake/data` → コンテナ内 `/lake:ro` |
| systemd unit | `/etc/systemd/system/finance-dwh-{transform.service,transform.timer}`（常駐サービスは無い） |

## `compose/.env`（deploy 時に手で用意、git 管理外）

```
LAKE_DIR=/home/ikuty/finance-lake/data
DATA_DIR=/home/ikuty/finance-dwh/data
DBT_TARGET=prod
LANDING_LOOKBACK_DAYS=7
S3_BUCKET_NAME=ikuty-finance
AWS_ACCESS_KEY_ID=<finance-lake と共用の IAM ユーザー>
AWS_SECRET_ACCESS_KEY=<同上>
AWS_DEFAULT_REGION=ap-northeast-1
SLACK_WEBHOOK_URL=<finance-lake と共用>
```

Postgres 版にあった `PGDATA_DIR` / `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` は不要になった（DuckDB は組み込み型でユーザー・パスワードの概念が無い）。

- S3 バケット（`ikuty-finance`、静的サイトホスティング、7 日ライフサイクル）と IAM ユーザーは
  `finance-lake` で作成済みのものを共用。レポートのキーは `finance-dwh/run_report.html`
  （lake は `edinet-dl/…` / `jpx-daily-pdf-dl/…`。キーで名前空間分離）。
- `S3_BUCKET_NAME` / `SLACK_WEBHOOK_URL` 未設定なら該当ステップは静かにスキップされる。

## GitHub Actions

| ワークフロー | トリガー | 内容 |
|---|---|---|
| `finance-dwh-ci.yml` | push(main) / PR | `mypy --strict` + `pytest` + `dbt parse`（DuckDB `ci` ターゲットは `:memory:`、DB 接続設定不要） |
| `finance-dwh-build-push.yml` | push(main, paths: transform/dbt) | `finance-dwh-transform` の1イメージを ghcr.io へ push（postgres イメージは廃止） |
| `finance-dwh-deploy.yml` | `workflow_dispatch`（手動） | Tailscale+SSH で `git pull` + `docker pull` + ローカルタグへ retag |
| `gitleaks.yml` | push(main) | 秘密情報スキャン |

### Secrets（登録済み）

`TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` / `SSH_PRIVATE_KEY` / `MAC_MINI_HOST` /
`MAC_MINI_USER`。

### イメージタグの retag

compose / systemd はレジストリ接頭辞の無いローカルタグ（`finance-dwh-transform:latest`）
を参照する。deploy は `docker pull ghcr.io/...` の後に同名タグへ `docker tag` する
（edinet-dl で実機確認済みの不具合対策）。

### 反映タイミング

`docker compose run --rm` 方式（コンテナ内にデーモンを置かない）のため、pull 後の
再起動やサービス再読み込みは不要。次回の systemd タイマー実行から新しいイメージが使われる。
Postgres 版にあった「postgres サービスの即時反映」の考慮は不要になった（常駐サービスが無いため）。

## systemd unit と実行順序

- `finance-dwh-transform.service`: `Type=oneshot`。`docker compose run --rm transform`。
  `After=` に docker / edinet-dl / jpx-daily-pdf-dl を列挙（順序制約のみ、`Requires=`
  無し）。旧 `finance-dwh-postgres.service` は廃止（DuckDB は組み込み型で常駐不要）。
- `finance-dwh-transform.timer`: `OnCalendar=*-*-* 04:01:45 Asia/Tokyo`、`Persistent=true`。

タイミング: **jpx 04:01:00 → edinet 04:01:30 → transform 04:01:45 → shutdown 04:02:00**。
transform のタイマーを lake の両タイマーより後にするのは、遅延起動時に取得ジョブより先に
発火して `After=` が空振りするのを避けるため。shutdown のタイマーより前にするのは、
shutdown 側の `After=` がこのサービスを「実行中」と認識できるようにするため。

`finance-lake/systemd/finance-lake-shutdown.service` の `After=` には
`finance-dwh-transform.service` が既に追記済み（`finance-dwh-postgres.service` への
参照はそもそも含まれていなかったため、今回の Postgres 撤去にあたって finance-lake
側の変更は不要）。

## Postgres → DuckDB 移行手順（Mac Mini、既存デプロイからの切り替え）

Postgres 版が既に Mac Mini 上で稼働・初回バックフィル済みの状態からの移行。

1. `sudo systemctl disable finance-dwh-transform.timer`（移行中に日次実行が走らないように。
   Postgres 版からの移行作業中に一時停止した状態のまま）
2. `git pull origin main` で DuckDB 版を取得
3. `sudo systemctl stop finance-dwh-postgres.service`
   `sudo systemctl disable finance-dwh-postgres.service`
   `sudo rm /etc/systemd/system/finance-dwh-postgres.service`
4. `sudo cp systemd/finance-dwh-transform.service /etc/systemd/system/`
   （`After=`/`Requires=` の更新版で上書き）
5. `sudo systemctl daemon-reload`
6. `compose/.env` を更新（`PGDATA_DIR`/`POSTGRES_*` を削除、`DATA_DIR` を追加。上記参照）
7. `docker pull` + `docker tag` で `finance-dwh-transform:latest` を更新（deploy workflow
   を回すか手動で）
8. 動作確認: `sudo systemctl start finance-dwh-transform.service` →
   `journalctl -u finance-dwh-transform.service` で landing 取り込み・dbt build・
   レポート URL を確認
9. 実スケール（2000万行超）での初回 landing 投入・cleansed フルビルドの所要時間を計測
   （ローカルサンプルでは 0.2〜8秒だが、実データでの検証はここが初回）
10. 問題無ければ `sudo systemctl enable --now finance-dwh-transform.timer`
11. Postgres 版のデータ（`/home/ikuty/finance-dwh/data/pgdata`）は当面残す
    （ロールバック用）。安定稼働を確認後に `sudo rm -rf` で削除する
12. `docker image rm finance-dwh-postgres:latest ghcr.io/ikuty/finance-dwh-postgres:latest`
    で不要イメージを削除

## 実装状況（2026-09-11 時点）

- ローカル: pytest / mypy --strict / `docker compose` フルフロー（コンテナ含む）緑。
- 未了: 上記移行手順の Mac Mini での実施、実スケールでの性能検証。
