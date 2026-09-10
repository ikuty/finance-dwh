FROM postgres:17-bookworm

# file_fdw の外部テーブルは `program` オプションで Python(stdlib のみ)のラッパーを
# 呼び出す。公式 postgres イメージには python3 が無いため追加する。
# file_fdw 拡張自体は公式イメージに同梱の contrib モジュールで利用可能。
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

# FDW ラッパー（外部テーブルの program から `python3 /opt/fdw/*.py /lake` で起動される）
COPY fdw/edinet_csv_fdw.py fdw/edinet_docindex_fdw.py fdw/jpx_catalog_fdw.py /opt/fdw/

# 初回起動時（PGDATA が空のとき）に一度だけ実行される。ファイル名の昇順。
COPY initdb/01_schema.sql initdb/02_fdw.sql /docker-entrypoint-initdb.d/
