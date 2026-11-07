-- J-Quants補完行(Issue #99)は、stq_pricesの最小file_date以降にだけ存在し、
-- 同じ(銘柄, 日付)にstq_prices行があってはならない(JPX優先の保証)。
-- 行が返れば失敗。

with stq as (
    select code, file_date from {{ ref('cleansed__jpx__stq_prices') }} where length(code) = 4
)
select p.code, p.file_date
from {{ ref('intermediate__jpx__daily_prices') }} p
where p.source_model = 'jquants_daily_quotes'
  and (
      exists (select 1 from stq s where s.code = p.code and s.file_date = p.file_date)
      or p.file_date < (select min(file_date) from stq)
  )
