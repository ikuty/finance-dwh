-- 企業自己申告のPER(per_disclosed)と、決算期末日の実終値から算出したper_computedを
-- 突合する。実データ検証(2026-09-25、三井住友FG直近5期)で、per_disclosed×epsが
-- 決算期末日（休業日の場合は直前営業日）の終値と円単位までほぼ完全に一致した
-- （5期中4期。残り1期は当時の日次株価データ自体の欠落が原因と判明、抽出ロジックの
-- 問題ではない）ことを踏まえ、相対誤差2%を許容しseverity=warnとする（buildは
-- 失敗させない、目視確認用。結合キー・日付ルールが壊れていないかの継続的な
-- ヘルスチェックを兼ねる）。

{{ config(severity='warn') }}

select
    doc_id, edinet_code, fiscal_year, period_type,
    per_disclosed, per_computed, period_end_close_date
from {{ ref('mart__jpx_edinet__valuation_indicators') }}
where per_disclosed is not null
  and per_computed is not null
  and per_disclosed != 0
  and abs(per_disclosed - per_computed) / abs(per_disclosed) > 0.02
