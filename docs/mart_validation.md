# mart層の値の妥当性検証（2026-09-21〜）

## 2026-09-25: 指標追加（shares_outstanding/diluted_eps/comprehensive_income/cash_and_equivalents）

`cleansed__edinet__facts`の全item_name（5,771種類）を棚卸しし、「経営指標等」
カテゴリ（73種類、既存14指標と同じ5期比較表の構造）のうち未使用の項目から、書類数
カバレッジが高い4項目を追加した。特に`shares_outstanding`（発行済株式総数）は、
以前「今後の拡張候補」に記載していた`eps`/`bps`の再計算検証テストを可能にするために
追加した。各intermediateモデル・martの変更内容は共通のコメント規約に従い各モデル
ファイル冒頭に記載。

### 2026-09-25追記: shares_outstandingのソース見直し

導入直後にeps/bps再計算検証テストの母数を確認したところ、`shares_outstanding`
（経営指標等の`発行済株式総数（普通株式）`ベース）のカバレッジが35,045書類と、他の
新規3指標（63,000〜48,000件台）に比べて低いことが判明した。item_nameを棚卸しした
結果、「株式の総数等」開示セクションの`事業年度末現在発行数（株）、発行済株式、
株式の総数等`（context_id='FilingDateInstant_OrdinaryShareMember'）の方がカバレッジが
77,666書類と2.2倍高いことを確認し、こちらを正とするよう変更した（会計基準・連結決算
スコープに依存しない企業単位の事実のため、`intermediate__edinet__dei_facts`に一元化）。
旧ソース（経営指標等ベース、各会計基準別モデル）はフォールバックとして残している。
無サフィックスの`FilingDateInstant`context（全種類株式の合算）ではなく
`_OrdinaryShareMember`（普通株式限定）を選んだ理由: 複数種類株式（優先株等）を持つ
企業では両者の値が乖離する（実データで約1,686書類確認）。EPS/BPSの分母は通常
「普通株式数」のため、旧ソースの「（普通株式）」限定と概念を揃える必要がある。

## 背景

`mart__edinet__financial_indicators`の実装後、実データを確認する過程で複数のバグが
見つかった（いずれも外部データとの突合ではなく、値を直接見て気づいたもの）。

1. 半期報告書(period_type='half')のcontext_id判定漏れ（`Interim`のみを見ていたが
   `CurrentQuarter`/`CurrentYTD`ラベルを使う書類が実在し、指標が全てNULLになっていた）
2. 複数候補項目名（J-GAAP内の売上高/営業収益/経常収益等）のcoalesce優先順位誤り
   （個別のみ存在する項目が、連結で存在する別項目より優先されてしまうケースがあった。
   sec_code=8316で発覚、PR#13で修正）
3. 会計基準をまたいだcoalesceの誤り（IFRS採用企業でも個別財務諸表は通常J-GAAPの
   まま作成されるため、1つの書類にJ-GAAP名タグとIFRS名タグが混在しうる。優先順位
   だけで選ぶと企業自身の会計基準と無関係な値を採用してしまう。sec_code=2282等で
   発覚）。**この根本原因への対応として、2026-09-21にintermediate層を導入**し、
   会計基準ごとに独立したモデル（`intermediate__edinet__{jgaap,ifrs,usgaap}_
   financial_facts`）で指標を抽出するよう再設計した（詳細は`docs/architecture.md`・
   各モデルのコメント参照）。martはそれらを企業自身のaccounting_standardに基づいて
   組み合わせるだけの薄い層になった。
4. 通貨単位(unit_id)を無視していた誤り（一部のIFRS採用企業は経営指標等のIFRSタグを
   USD建てで開示し、同一書類内にJPY建てのJ-GAAP名タグも別途存在するケースがある。
   生の数値をそのまま比較すると通貨単位の違いにより無関係な値に見える。
   sec_code=6269（三井海洋開発）で発覚、2026-09-22修正）。金額系指標に
   `unit_id='JPY'`（1株当たり指標は`JPYPerShares`）を必須条件として追加し、
   外貨建ての値は個別/連結と同様にフォールバック対象外とした（他の会計基準側で
   JPY建ての値が見つかればそちらが採用される）。影響範囲は実機調査により該当
   1社・15書類のみと確認済み。

いずれも「値を目視して気づく」形で発見されており、体系的なテストで検出できる性質の
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
| `eps` | | | ✓(warn) `..._eps_recomputed` | 期中加重平均株式数と期末発行済株式数の差により構造的に乖離しうる、誤差許容(相対20%) |
| `bps` | | | ✓(warn) `..._bps_recomputed` | 自己株式の扱いの差により乖離しうる、誤差許容(相対20%) |
| `roe` | - | - | ✓(warn) `..._roe_recomputed` | 期末値ベースの簡易再計算、誤差許容(5pt) |
| `per` | | | | 未カバー（株価データを保持していないため再計算不可） |
| `operating_cf`/`investing_cf`/`financing_cf` | | | | 未カバー（損益同様、単調性は不成立） |
| `capital` | | | | 未カバー（通常期中不変、増減時のみ意味を持つ） |
| `payout_ratio` | | | | 未カバー |
| `shares_outstanding` | | | | 未カバー（eps/bps再計算の分母として利用、単体の検証観点なし） |
| `diluted_eps` | | ✓(warn) `..._diluted_eps_lte_eps` | | `eps>0`の場合のみ、`diluted_eps <= eps`（希薄化効果） |
| `comprehensive_income` | | | | 未カバー（net_incomeとの単純な近似関係が立てられないため） |
| `cash_and_equivalents` | | ✓ `..._cash_lte_total_assets` | | `cash_and_equivalents <= total_assets` |

## 実装済みテスト

- `dbt/tests/assert_mart__edinet__financial_indicators_sales_monotonic_ytd.sql`
  （severity=error）
- `dbt/tests/assert_mart__edinet__financial_indicators_total_assets_gte_net_assets.sql`
  （severity=error）
- `dbt/tests/assert_mart__edinet__financial_indicators_equity_ratio_recomputed.sql`
  （severity=warn）
- `dbt/tests/assert_mart__edinet__financial_indicators_roe_recomputed.sql`
  （severity=warn）
- `dbt/tests/assert_mart__edinet__financial_indicators_cash_lte_total_assets.sql`
  （severity=error、2026-09-25追加）
- `dbt/tests/assert_mart__edinet__financial_indicators_diluted_eps_lte_eps.sql`
  （severity=warn、2026-09-25追加）
- `dbt/tests/assert_mart__edinet__financial_indicators_bps_recomputed.sql`
  （severity=warn、2026-09-25追加）
- `dbt/tests/assert_mart__edinet__financial_indicators_eps_recomputed.sql`
  （severity=warn、2026-09-25追加）

severity=warnのテストはbuildを失敗させない（目視確認用、閾値超過を検知したら実データで
個別に調査する）。severity=errorのテストは、成立しなければ確実に何らかの選択ミスが
起きているとみなせる関係のみに限定している。

## 既知の外れ値（調査終了）

intermediate層導入（会計基準ごとのモデル分離）により、`sales`単調性違反は24件→9件まで
減少した（日本ハム(2282)・CLホールディングス(4286)は会計基準またぎのcoalesce誤りが
根本原因で、intermediate層導入により解消）。

残り7件（6社）のうち、**三井海洋開発(6269)2件（38期・40期）は通貨単位(unit_id)の
バグが根本原因**と判明し、2026-09-22の修正（intermediateモデルに`unit_id='JPY'`
条件を追加）で解消した。同時に、2021年分バックフィルの反映により新たに36期・39期
でも同型の違反が顕在化していたが、これも同じ修正で解消される見込み。

残る6件（5社）は、element_idレベルまで確認した結果、当システム側の抽出ロジックの
問題ではなく、**annual書類側のXBRL値自体が四半期報告書と整合しない特異点**（提出者
側のデータ特性、通貨単位の問題ではない）と判断した。

対象: クシム(2345)28期、アステリア(3853)26期、マクロミル(3978)10期、
アドベンチャー(6030)16期、アジア開発キャピタル(9318)103期。
加えてメタップス(6172)14期は、同一XBRL要素・同一通貨(JPY)で約4%の小幅な差異のみ
（事業区分変更等の可能性、通貨単位の問題ではない）と確認し、同様に除外リストへ
追加した。

`assert_mart__edinet__financial_indicators_sales_monotonic_ytd.sql`で
`(sec_code, fiscal_year)`単位の除外リストとして明示的に記録し、それ以外のケースは
引き続きseverity=errorで検出する（テストを緩める＝severity=warnに変更する、のでは
なく、既知の分だけを名指しで除外することで、新規の未知バグの検出力を落とさない
方針とした）。

## 今後の拡張候補

- `per`の再計算検証には株価データが必要（当システムはJPX相場データを別マートで保持して
  いるが、`mart__edinet__financial_indicators`とは未結合）。結合すれば
  `per ≈ 株価 / eps`等の整合性チェックが可能になる。
- `capital`（資本金）は通常期中不変のため、同一edinet_codeの連続する期で大きく変動して
  いないかのチェックは追加できる余地がある（増資等の正当な変動は許容する必要がある）。
- **intermediate層単体でのテスト**: 現在の4テストはmart（会計基準統合後の最終出力）に
  対するものだが、`intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts`単体に
  対しても同種のテスト（単調性・大小関係等）を会計基準ごとに書けば、「どの会計基準の
  抽出ロジックに問題があるか」をより直接的に特定できる。今回は導入していない
  （2026-09-21時点でのnext step候補）。
