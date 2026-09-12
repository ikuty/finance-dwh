-- cleansed__jpx__stq_prices の vwap（売買高加重平均価格）は
-- 売買代金 / 売買高 の再計算値と一致すること（相対誤差0.1%以内、丸め誤差吸収）。
-- 売買高が0またはNULL（前場・後場とも未約定）の行は対象外。
-- 実データ28日分・117,673件で実測検証済み（違反0件、列の取り違えを検出できる
-- 検算として採用。詳細はdocs/raw_landing_design.md参照）。行が返れば失敗。

select
    code,
    file_date,
    vwap,
    trading_value,
    trading_volume,
    trading_value / trading_volume as computed_vwap
from {{ ref('cleansed__jpx__stq_prices') }}
where trading_volume is not null
  and trading_volume != 0
  and vwap is not null
  and vwap != 0
  and abs(trading_value / trading_volume - vwap) / vwap > 0.001
