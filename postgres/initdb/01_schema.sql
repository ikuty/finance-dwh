-- finance-dwh の 3 層スキーマ。
--   raw      : レイク(finance-lake)のファイルを file_fdw で接続した層。dbt source として参照。
--              このスキーマの中身は 02_fdw.sql と（本番では）レイクの実データが決める。
--   cleansed : dbt が生成する型付け・名寄せ済みの層。
--   mart     : dbt が生成する業務エンティティ層。
--
-- 個人運用の単一DBのため、層ごとのロール分割は行わない（POSTGRES_USER が全層を所有する）。

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS cleansed;
CREATE SCHEMA IF NOT EXISTS mart;

COMMENT ON SCHEMA raw IS 'レイク(finance-lake)のファイルを file_fdw で接続した層。dbt source。';
COMMENT ON SCHEMA cleansed IS 'dbt が生成する型付け・名寄せ済みの層。';
COMMENT ON SCHEMA mart IS 'dbt が生成する業務エンティティ層。';
