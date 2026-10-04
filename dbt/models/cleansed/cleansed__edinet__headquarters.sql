-- EDINET開示の表紙記載「本店の所在の場所」を書類(doc_id)単位に取り出し、住所文字列
-- から都道府県を分割した型付け版（毎回フル rebuild、Parquet）。
--
-- cleansed__edinet__facts（EDINETタクソノミ全要素に同一スキーマを適用する汎用EAV
-- テーブル）に項目特化の列を増やすと汎用性が崩れるため、cleansed__mufg__stock_splits
-- （"1：2"をratio_before/ratio_afterへ分割）と同じ「1ソース(ここでは1項目)=1専用
-- cleansedモデル」という既存パターンに倣い、新規モデルとして分離した
-- (2026-10-04、ユーザー判断)。
--
-- item_name＝'本店の所在の場所、表紙'（表紙記載の項目で、タクソノミ名前空間は
-- 書類種別によりjpcrp_cor/jpcrp-esr_cor/jpsps_cor等に分かれるが、item_nameは共通の
-- ため名前空間を問わずこの1文字列でフィルタできる。実機確認済み）。context_idは
-- 常に'FilingDateInstant'、consolidationは常に'other'（経営指標等と異なり連結/個別
-- の混在が無い単純な表紙項目）。カバレッジ: 全書類140,288件中132,221件(94%)、有報
-- (doc_type_code=120)のみなら23,215件中23,180件(99.8%)（2026-10-04実機確認）。
--
-- 都道府県の抽出方法(2026-10-04実機確認、重要):
--   住所文字列の表記が統一されていないため、3段階で解決する。
--   1. 先頭の郵便番号表記（"〒101-0047 " 等）・前後の空白（半角/全角）を除去する。
--   2. 都道府県名(47種)のいずれかで始まっていれば、それを採用する(実機確認:
--      132,221件中107,143件が素の先頭一致。残りの多くは先頭の全角スペース等が
--      原因で先頭一致に失敗していただけで、1の前処理後はここで解決する)。
--   3. 1-2で解決しない場合、都道府県を省略して市区町村名から書き始める表記
--      (例:「大阪市中央区...」「名古屋市中区...」。政令指定都市・県庁所在地クラスの
--      約35都市で発生、実機確認済み)に対応するため、固定の市区町村→都道府県の
--      対応表で解決する。この規模（30〜40件）であれば全国的な重複の懸念はない。
--   1-3のいずれでも解決しない住所(数十件、全体の0.1%未満)は、提出書類自体の誤記
--   （例:「大阪部茨木市」「愛知管豊橋市」のような府→部、県→管の誤変換）や、住所欄に
--   役職者名・移転のお知らせ文・郵便番号のみ等、本来の住所ではない値が入っている
--   ケース。これらはデータそのものの不備であり、推測で補わずprefectureをNULLとする。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/edinet_headquarters.parquet',
    format='parquet'
) }}

with addr as (
    select
        doc_id,
        max(edinet_code)      as edinet_code,
        max(value_text)       as address_raw,
        max(submit_date_time) as submit_date_time
    from {{ ref('cleansed__edinet__facts') }}
    where item_name = '本店の所在の場所、表紙'
    group by doc_id
),

cleaned as (
    select
        doc_id,
        edinet_code,
        address_raw,
        submit_date_time,
        -- 先頭の郵便番号("〒101-0047 "等)と前後の空白(半角/全角)を除去する。
        trim(
            regexp_replace(
                trim(address_raw),
                '^〒?[0-9０-９]{3}[-－]?[0-9０-９]{3,4}[\s　]*',
                ''
            )
        ) as address_clean
    from addr
)

select
    doc_id,
    edinet_code,
    submit_date_time,
    address_raw,
    address_clean,
    case
        when address_clean like '北海道%' then '北海道'
        when address_clean like '青森県%' then '青森県'
        when address_clean like '岩手県%' then '岩手県'
        when address_clean like '宮城県%' then '宮城県'
        when address_clean like '秋田県%' then '秋田県'
        when address_clean like '山形県%' then '山形県'
        when address_clean like '福島県%' then '福島県'
        when address_clean like '茨城県%' then '茨城県'
        when address_clean like '栃木県%' then '栃木県'
        when address_clean like '群馬県%' then '群馬県'
        when address_clean like '埼玉県%' then '埼玉県'
        when address_clean like '千葉県%' then '千葉県'
        when address_clean like '東京都%' then '東京都'
        when address_clean like '神奈川県%' then '神奈川県'
        when address_clean like '新潟県%' then '新潟県'
        when address_clean like '富山県%' then '富山県'
        when address_clean like '石川県%' then '石川県'
        when address_clean like '福井県%' then '福井県'
        when address_clean like '山梨県%' then '山梨県'
        when address_clean like '長野県%' then '長野県'
        when address_clean like '岐阜県%' then '岐阜県'
        when address_clean like '静岡県%' then '静岡県'
        when address_clean like '愛知県%' then '愛知県'
        when address_clean like '三重県%' then '三重県'
        when address_clean like '滋賀県%' then '滋賀県'
        when address_clean like '京都府%' then '京都府'
        when address_clean like '大阪府%' then '大阪府'
        when address_clean like '兵庫県%' then '兵庫県'
        when address_clean like '奈良県%' then '奈良県'
        when address_clean like '和歌山県%' then '和歌山県'
        when address_clean like '鳥取県%' then '鳥取県'
        when address_clean like '島根県%' then '島根県'
        when address_clean like '岡山県%' then '岡山県'
        when address_clean like '広島県%' then '広島県'
        when address_clean like '山口県%' then '山口県'
        when address_clean like '徳島県%' then '徳島県'
        when address_clean like '香川県%' then '香川県'
        when address_clean like '愛媛県%' then '愛媛県'
        when address_clean like '高知県%' then '高知県'
        when address_clean like '福岡県%' then '福岡県'
        when address_clean like '佐賀県%' then '佐賀県'
        when address_clean like '長崎県%' then '長崎県'
        when address_clean like '熊本県%' then '熊本県'
        when address_clean like '大分県%' then '大分県'
        when address_clean like '宮崎県%' then '宮崎県'
        when address_clean like '鹿児島県%' then '鹿児島県'
        when address_clean like '沖縄県%' then '沖縄県'
        -- 都道府県を省略し市区町村名から始まる表記(政令指定都市・県庁所在地クラス、
        -- 実機確認済みの一覧)。
        when address_clean like 'さいたま市%' then '埼玉県'
        when address_clean like '仙台市%' then '宮城県'
        when address_clean like '佐賀市%' then '佐賀県'
        when address_clean like '北九州市%' then '福岡県'
        when address_clean like '千葉市%' then '千葉県'
        when address_clean like '名古屋市%' then '愛知県'
        when address_clean like '和歌山市%' then '和歌山県'
        when address_clean like '堺市%' then '大阪府'
        when address_clean like '大分市%' then '大分県'
        when address_clean like '大阪市%' then '大阪府'
        when address_clean like '奈良市%' then '奈良県'
        when address_clean like '姫路市%' then '兵庫県'
        when address_clean like '宮崎市%' then '宮崎県'
        when address_clean like '富山市%' then '富山県'
        when address_clean like '小樽市%' then '北海道'
        when address_clean like '岐阜市%' then '岐阜県'
        when address_clean like '岡山市%' then '岡山県'
        when address_clean like '川崎市%' then '神奈川県'
        when address_clean like '広島市%' then '広島県'
        when address_clean like '新潟市%' then '新潟県'
        when address_clean like '札幌市%' then '北海道'
        when address_clean like '横浜市%' then '神奈川県'
        when address_clean like '浜松市%' then '静岡県'
        when address_clean like '港区%' then '東京都'
        when address_clean like '相模原市%' then '神奈川県'
        when address_clean like '神戸市%' then '兵庫県'
        when address_clean like '福井市%' then '福井県'
        when address_clean like '福岡市%' then '福岡県'
        when address_clean like '秋田市%' then '秋田県'
        when address_clean like '長岡市%' then '新潟県'
        when address_clean like '長野市%' then '長野県'
        when address_clean like '青森市%' then '青森県'
        when address_clean like '静岡市%' then '静岡県'
        when address_clean like '高松市%' then '香川県'
        when address_clean like '高知市%' then '高知県'
        when address_clean like '鹿児島市%' then '鹿児島県'
        when address_clean like '京都市%' then '京都府'
    end as prefecture
from cleaned
