-- EDINET CSV 明細の型付け・名寄せ版。
--
-- source は landing.edinet_csv_facts（Parquet、日付ごとに Prefect が更新）。
-- 当初は「DuckDB + Parquetは全量の読み込み自体が速い」という前提でexternal
-- materializationにより毎回全量rebuildしていたが、EDINETバックフィルが進み
-- landingが813日・2.8GBまで積み上がった結果、dbt build全体の所要時間の約95%
-- （980秒）をこのモデル単体が占めるようになった(2026-09-17実機判明)。incremental化した。
--
-- 粒度(2026-09-20修正、重要):
--   当初は(edinet_code, element_id, context_id, consolidation)単位でsubmit_date_time
--   最新の1行のみを残す設計だった(「同じcontext_idが複数書類にまたがるのは同一期間の
--   訂正報告書である」という前提)。この前提が誤りだったことが判明した。EDINETの
--   経営指標等(5期比較)のcontext_idは絶対年度を含まない相対ラベル
--   (CurrentYearInstant="この書類の当期"等)であり、訂正ではない通常の翌年の提出でも
--   同じcontext_id文字列が再利用される。このため「同一期間の訂正」と「別の期間の
--   通常の新規提出」をキーだけで区別できず、後者まで誤って古い方を破棄してしまい、
--   企業ごとに最新の1書類分(直近5期の比較列)以外の過去データが失われていた
--   (実機確認: E01738の総資産額、経営指標等は最新1 doc_idの5期分のみが残存)。
--   dedupキーにdoc_idを追加し、書類をまたいだ収縮をやめた(1行=1書類内の1項目)。
--   EDINETのdoc_idは提出ごとに一意・不変で訂正報告書も新しいdoc_idを持つため、
--   doc_id単位では収縮の必要がそもそも無い。「最新の値だけが欲しい」という用途は
--   消費側(mart__edinet__financial_indicators等)がsubmit_date_time順に絞り込む。
--
-- incremental化の要点:
--   - EDINETのlandingは未来方向だけでなく過去方向にも成長する(2022年分365日を
--     後からバックフィルした実績あり)。よって「file_date > 既存最大値」という
--     素朴な透かし方式は使えない。landingに存在するfile_date集合と{{ this }}に
--     存在するfile_date集合の差分を毎回計算し、フォワード・バックフィルを
--     問わず「まだ処理していないfile_date」だけを対象にする。
--   - doc_id単位に粒度を変更した(上記)ことで、新規file_date分の候補行が既存行と
--     キー衝突することは無くなった(同一doc_idが複数file_dateに現れることは無い)。
--     よって「既存より新しければ置き換える」upsert比較は不要になり、単純な
--     insertで足りる(is_incremental()によるSELECT文の分岐も不要)。
--   - materialized='external'はDuckDB内部テーブルを持たないため incremental
--     戦略を使えない(dbt-duckdbのexternalマテリアライゼーションにincremental
--     相当が無いことを確認済み)。materialized='incremental'(DuckDBカタログ内の
--     ネイティブテーブル)を使い、post_hookで従来通りのParquetファイルへ
--     エクスポートする(run_report.py等、下流はParquetファイルを直接読む契約の
--     ため)。
--   - landing側で既に処理済みのfile_dateの内容が後から`--force`等で修正された
--     場合(過去に2022年分EDINET一覧APIフレーキネスの復旧で発生した実績あり)、
--     file_date集合差分方式では検知できない。発生したら
--     `dbt run --full-refresh --select cleansed__edinet__facts`で手動フル
--     再構築すること。
--   - 書類をまたいだ収縮が無くなった分、行数はlandingの全履歴に近い規模まで
--     増加する見込み。post_hookのParquetフルエクスポートが将来的に遅くなる
--     可能性があるが、incremental化の際と同様「実機で問題が顕在化してから
--     対処する」方針とする(2026-09-20時点で先回りの最適化はしない)。
--
-- 連結/個別は EDINET CSV の「連結・個別」列をそのまま使う（EDINET 算出済み）。
-- ただし経営指標等(5期比較)項目はこの列が常に'other'になる(EDINET側の仕様、
-- 連結/個別はcontext_idの_NonConsolidatedMemberサフィックス有無で判定する必要が
-- あり、この列では判定できない。実機確認済み、下流のmartモデル側で対応)。
-- 数値化は try_cast に任せる（"－" 等の非数値は NULL、decimal(38,4) で桁落ちしない）。

{{ config(
    materialized='incremental',
    unique_key=['doc_id', 'element_id', 'context_id', 'consolidation'],
    incremental_strategy='delete+insert',
    post_hook="COPY (select * from {{ this }}) TO '" ~ env_var('CLEANSED_ROOT', '/data/cleansed') ~ "/edinet_facts.parquet' (FORMAT PARQUET)"
) }}

with new_file_dates as (
    -- landingのfile_date一覧は、実データ列を読む(実測: 813ファイルで8.6秒)のではなく
    -- ディレクトリ名(file_date=YYYY-MM-DD)をglob()で列挙する(実測: 0.13秒、約65倍高速。
    -- 2026-09-17実機ベンチマークで判明)。ファイル内容を一切開かないため、landingが
    -- 何日分に増えても実質定数時間で終わる。
    select distinct
        regexp_extract(file, 'file_date=([0-9]{4}-[0-9]{2}-[0-9]{2})', 1)::date as file_date
    from glob('{{ env_var("LANDING_ROOT", "/data/landing") }}/edinet_csv_facts/file_date=*/part.parquet')
    {% if is_incremental() %}
    except
    select distinct file_date from {{ this }}
    {% endif %}
),

facts as (
    select * from {{ source('landing', 'edinet_csv_facts') }}
    where cast(file_date as date) in (select file_date from new_file_dates)
),

docs as (
    select doc_id, sec_code, submit_date_time
    from {{ ref('cleansed__edinet__documents') }}
),

typed as (
    select
        cast(f.file_date as date)               as file_date,
        f.edinet_code,
        f.doc_id,
        d.sec_code,
        f.element_id,
        f.item_name,
        coalesce(nullif(f.context_id, ''), '')   as context_id,
        nullif(f.relative_year, '')              as relative_year,
        case f.consolidated_individual
            when '連結' then 'consolidated'
            when '個別' then 'non_consolidated'
            else 'other'
        end                                      as consolidation,
        nullif(f.period_instant, '')             as period_instant,
        nullif(f.unit_id, '')                    as unit_id,
        nullif(f.unit, '')                       as unit,
        f.value                                  as value_text,
        try_cast(f.value as decimal(38, 4))      as value_num,
        d.submit_date_time
    from facts f
    join docs d on d.doc_id = f.doc_id
),

deduped as (
    -- doc_id単位の重複排除(書類をまたいだ収縮はしない、1書類内の重複CSV行のみ対象)
    select
        *,
        row_number() over (
            partition by doc_id, element_id, context_id, consolidation
            order by file_date desc
        ) as _rn
    from typed
)

select
    file_date,
    edinet_code,
    doc_id,
    sec_code,
    element_id,
    item_name,
    context_id,
    relative_year,
    consolidation,
    period_instant,
    unit_id,
    unit,
    value_text,
    value_num,
    submit_date_time
from deduped
where _rn = 1
