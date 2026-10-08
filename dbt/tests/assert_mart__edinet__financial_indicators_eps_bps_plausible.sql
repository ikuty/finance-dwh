-- eps/bpsが明らかに桁違いな値(提出者側のXBRL自体の入力誤り)を検知する。
-- 2026-10-09、J-Quants照合プロジェクトで養命酒製造(2540)・イーストアー(4304)・
-- カオナビ(4435)の経営指標等タグに、本来数十〜数千円台であるべき値ではなく
-- 数千万〜数百億円台の値が直接記載されていることを実機確認した(連結・個別
-- どちらのタグも同様に異常であり、DWH側の抽出ロジックの問題ではない)。
--
-- 閾値100万円の根拠: 全量データの実機確認で、正当な極端値（信金中央金庫の
-- BPS約32万円＝特殊な資本構成、LITALICOの発行済株式数200株という特殊な期の
-- EPS/BPS約86万円＝net_assets/sharesで内部的に整合）の最大値は約86万円に
-- とどまる一方、異常な値は最小でも約2,080万円から始まり、24倍以上のギャップが
-- ある。100万円はこのギャップの中に位置し、既知の正当なケースを誤検知しない。
--
-- 既存のassert_..._eps_recomputed/bps_recomputedは再計算との軽微な乖離も
-- 含め大量(1,000件超)にWARNを出すため、人間によるSlack確認には適さない。
-- 本テストは「桁が明らかにおかしい」という狭い基準に絞った独立のチェックで、
-- Slack通知対象(human-in-the-loop)としてmeta.notify_slack=trueを付与している。
-- 新しい通知対象テストを追加する場合も、このmetaを付けるだけでよい
-- （transform/flows/test_violations.pyがmanifest.jsonのmetaを見て自動検出する。
-- 2026-10-09決定、課題1のtag方式deny-listと同じ考え方: 通知すべきかという
-- 判断をテスト本体と切り離した別リストに置くと、将来追加時の記入漏れに気づけない）。
--
-- severity=warn(buildは失敗させない、目視確認用)。行が返れば対象。

{{ config(severity='warn', meta={'notify_slack': true}) }}

select doc_id, edinet_code, fiscal_year, period_type, eps, bps
from {{ ref('mart__edinet__financial_indicators') }}
where abs(eps) > 1000000 or abs(bps) > 1000000
