{{ config(severity='warn') }}
-- J-Quantsで補完した終値(Issue #99)の信頼性を保つ見張り。同じ(銘柄, 日付)で
-- stq_prices(東証日報)とJ-Quantsの調整前終値が食い違う行があれば警告する。
-- 実測(2026-10-22〜11-05、44,375件)では不一致0件だった。食い違いが出たら、
-- J-Quants側の仕様変更(調整前/調整後の取り違え等)を疑う。行が返れば警告。

select
    p.code,
    p.file_date,
    coalesce(p.pm_close, p.am_close) as jpx_close,
    q.close_price as jquants_close
from {{ ref('cleansed__jpx__stq_prices') }} p
join {{ ref('cleansed__jquants__daily_quotes') }} q
    on q.file_date = p.file_date
    and substr(q.code, 1, 4) = p.code
where length(p.code) = 4
  and length(q.code) = 5
  and right(q.code, 1) = '0'
  and coalesce(p.pm_close, p.am_close) is distinct from q.close_price
