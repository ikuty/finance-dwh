# mart層の値の妥当性検証（2026-09-21〜）

## 背景

`mart__edinet__financial_indicators`の実装後、実データを確認する過程で2件のバグが
見つかった（いずれも外部データとの突合ではなく、値を直接見て気づいたもの）。

1. 半期報告書(period_type='half')のcontext_id判定漏れ（`Interim`のみを見ていたが
   `CurrentQuarter`/`CurrentYTD`ラベルを使う書類が実在し、指標が全てNULLになっていた）
2. 複数候補項目名（J-GAAP内の売上高/営業収益/経常収益等）のcoalesce優先順位誤り
   （個別のみ存在する項目が、連結で存在する別項目より優先されてしまうケースがあった。
   sec_code=8316で発覚）

どちらも「値を目視して気づく」形で発見されており、体系的なテストで検出できる性質の
バグだった。外部データ（IR Bank等）との突合は、利用規約上の制約・データ取得自体の
不確実性（PDF OCR等）を伴うため採用せず、**当システム内部だけで成立する数学的な
整合性**をdbtのsingular testとして実装する方針とした（`dbt/tests/assert_*.sql`、
既存パターンを踏襲）。

## カバレッジ表

指標×検証観点のマトリクス。空欄は未カバー（今後の拡張候補）。

| 指標 | 単調性(YTD累積) | 大小関係 | 再計算整合 | 備考 |
|---|---|---|---|---|
| `total_assets` | - (B/S残高、単調性の想定なし) | ✓ `..._total_assets_gte_net_assets` | - | |
| `net_assets` | - | ✓ (上記テストで同時検証) | - | |
| `equity_ratio` | - | - | ✓(warn) `..._equity_ratio_recomputed` | 非支配株主持分の影響で誤差許容(15pt) |
| `sales` | ✓ `..._sales_monotonic_ytd` | - | - | |
| `ordinary_income` | | | | 未カバー（損失計上四半期がありうるため単調性は不成立） |
| `net_income` | | | | 未カバー（同上） |
| `eps` | | | | 未カバー（発行済株式数を保持していないため再計算不可） |
| `bps` | | | | 未カバー（同上） |
| `roe` | - | - | ✓(warn) `..._roe_recomputed` | 期末値ベースの簡易再計算、誤差許容(5pt) |
| `per` | | | | 未カバー（株価データを保持していないため再計算不可） |
| `operating_cf`/`investing_cf`/`financing_cf` | | | | 未カバー（損益同様、単調性は不成立） |
| `capital` | | | | 未カバー（通常期中不変、増減時のみ意味を持つ） |
| `payout_ratio` | | | | 未カバー |

## 実装済みテスト

- `dbt/tests/assert_mart__edinet__financial_indicators_sales_monotonic_ytd.sql`
  （severity=error）
- `dbt/tests/assert_mart__edinet__financial_indicators_total_assets_gte_net_assets.sql`
  （severity=error）
- `dbt/tests/assert_mart__edinet__financial_indicators_equity_ratio_recomputed.sql`
  （severity=warn）
- `dbt/tests/assert_mart__edinet__financial_indicators_roe_recomputed.sql`
  （severity=warn）

severity=warnのテストはbuildを失敗させない（目視確認用、閾値超過を検知したら実データで
個別に調査する）。severity=errorのテストは、成立しなければ確実に何らかの選択ミスが
起きているとみなせる関係のみに限定している。

## 今後の拡張候補

- `eps`/`bps`/`per`の再計算検証には発行済株式数（経営指標等の`発行済株式総数`項目）を
  martに追加すれば、`bps ≈ net_assets / 発行済株式数`等の整合性チェックが可能になる。
- `capital`（資本金）は通常期中不変のため、同一edinet_codeの連続する期で大きく変動して
  いないかのチェックは追加できる余地がある（増資等の正当な変動は許容する必要がある）。
