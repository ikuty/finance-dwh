"""`dbt docs generate` を実行し、生成された静的ファイル（index.html・manifest.json・
catalog.json等）をS3へアップロードして静的ホスティングする。

サービスチェーンの最後（finance-dwh-transform完了後）に実行する専用サービス
（2026-10-25追加、compose/docker-compose.ymlのdbt-docs-genサービス）。`dbt docs
generate`はカタログ情報（列の型・実データの統計等）を実際にDBへクエリして生成する
ため、本体のdbt build完了後でなければ正しい内容にならない（本体より前に実行しては
ならない）。

アップロード先は既存のikuty-financeバケット（実行レポートのS3公開
`flows/notify.py`と共用、既に静的サイトホスティングが有効）。キーの接頭辞
`finance-dwh/docs/`を切り、同じ接頭辞の下に全ファイルを並べる（index.htmlが
manifest.json・catalog.json等を相対パスで読み込むため、同じ「ディレクトリ」に
置く必要がある）。

Prefectは使わない（landing取り込み等の複数ステップを持つdaily_transformとは異なり、
dbt docs generate→S3アップロードの2ステップのみのため、Prefectのephemeralサーバ
起動コストに対して得られる利点が無いと判断、compare_jquants.py等の単純スクリプト
と同じ方針）。

Usage:
    python -m flows.dbt_docs_gen
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from flows.notify import send_slack_notification

DBT_PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR", "/app/dbt")
DBT_PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", DBT_PROJECT_DIR)
DBT_TARGET = os.environ.get("DBT_TARGET", "prod")

DEFAULT_S3_REGION = "ap-northeast-1"
S3_KEY_PREFIX = "finance-dwh/docs/"

# dbt docs generateが`target/`に書く、実際にサイト表示に必要なファイルとContent-Type
# （graph.gpickle・partial_parse.msgpack等の内部キャッシュ・中間物は対象外、実機確認済み）。
DOC_FILES: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "manifest.json": "application/json",
    "catalog.json": "application/json",
    "run_results.json": "application/json",
    "semantic_manifest.json": "application/json",
}


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("dbt-docs-gen")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger  # 同一プロセス内で複数回呼ばれても二重登録しない（テスト等）

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def run_docs_generate(logger: logging.Logger) -> bool:
    cmd = [
        "dbt",
        "docs",
        "generate",
        "--project-dir",
        DBT_PROJECT_DIR,
        "--profiles-dir",
        DBT_PROFILES_DIR,
        "--target",
        DBT_TARGET,
    ]
    logger.info("実行: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    for line in (proc.stdout + proc.stderr).splitlines():
        logger.info(line)
    return proc.returncode == 0


def upload_docs_to_s3(logger: logging.Logger) -> str | None:
    """生成済みの静的ファイルをS3へ置き、公開URL（静的サイトホスティング経由）を
    返す。`S3_BUCKET_NAME`が未設定、またはアップロード失敗時はNone。
    （flows/notify.py の upload_report_to_s3 と同じ設計方針: 失敗はログのみ、
    例外は上げない。)"""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        return None

    region = os.environ.get("AWS_DEFAULT_REGION", DEFAULT_S3_REGION)
    target_dir = Path(DBT_PROJECT_DIR) / "target"

    try:
        import boto3

        s3 = boto3.client("s3")
        uploaded = 0
        for filename, content_type in DOC_FILES.items():
            path = target_dir / filename
            if not path.exists():
                logger.info(f"{filename}: 見つからないためスキップ")
                continue
            s3.put_object(
                Bucket=bucket,
                Key=S3_KEY_PREFIX + filename,
                Body=path.read_bytes(),
                ContentType=content_type,
            )
            uploaded += 1
        logger.info(f"S3へアップロード: {uploaded}/{len(DOC_FILES)}ファイル")
        return f"http://{bucket}.s3-website-{region}.amazonaws.com/{S3_KEY_PREFIX}index.html"
    except Exception as e:  # noqa: BLE001 - 通知系はジョブを落とさない
        logger.error(f"dbt docsのS3アップロードに失敗しました: {e}")
        return None


def main() -> None:
    logger = setup_logger()

    started = time.monotonic()
    ok = run_docs_generate(logger)
    duration = time.monotonic() - started

    doc_url = upload_docs_to_s3(logger) if ok else None

    status = "✅ 成功" if ok else "❌ 失敗"
    summary = f"{status} / dbt docs generate / {duration:.1f}s"
    if doc_url:
        summary += f"\n\n📚 dbt docs: {doc_url}"
    logger.info(summary)

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        send_slack_notification(webhook, summary, logger)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
