-- 値が出ている行（status='ok'）が、整合性の条件を満たしていること。行が返れば失敗。
--   - 実績: 通期なら当期行のQ1〜期末の合計が合計列と一致、四半期・中間なら前期行の
--     Q1〜期末の合計が合計列と一致（前期4スロットの合計＝前期の合計、#108）。
--   - 予想: 年間予想が合計列そのもの（空欄や不一致のまま値を出していない）。
-- 抽出のずれ（空欄詰め・円銭の分割）を、値の算出前に落とす安全装置が外れていないかの検知。

select
    edinet_code,
    period_end,
    period_type,
    dividend_ttm,
    dividend_forecast_annual,
    dividend_prior_total,
    dividend_cur_total
from {{ ref('intermediate__ir_disclosure__dividend_ttm') }}
where (dividend_ttm_status = 'ok' and (dividend_ttm is null or dividend_ttm < 0))
   or (dividend_forecast_status in ('ok', 'ok_total_only') and (dividend_forecast_annual is null or dividend_forecast_annual < 0
        or dividend_forecast_annual != dividend_fcst_total))
   or (dividend_ttm_status != 'ok' and dividend_ttm is not null)
   or (dividend_forecast_status not in ('ok', 'ok_total_only') and dividend_forecast_annual is not null)
