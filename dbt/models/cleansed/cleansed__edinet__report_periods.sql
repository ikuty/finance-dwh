-- EDINET提出書類のうち「期」の概念を持つ定期開示（有価証券報告書・四半期報告書・
-- 半期報告書）を、銘柄×期の単位に正規化した期間ディメンション。cleansed__edinet__facts
-- の集計（銘柄ごと・期ごと）の土台として使う（確認書・内部統制報告書・臨時報告書等の
-- 随時開示はここでは扱わない）。
--
-- 粒度は cleansed__edinet__documents と同じ(1行=1書類)。cleansed__edinet__facts
-- （1行=1書類内の1項目）の集計はしない。
--
-- filer_category(company/fund)の分離(2026-09-19決定):
--   EDINET提出者には、通常の事業会社に加えて投資信託受益証券等のファンド型が
--   混在する(実機確認: ordinance_code='030'、全体の0.04%程度)。JPX側の
--   security_category_ja分離と同じ考え方で、ordinance_code='030'をfundとして
--   独立した属性にする。
--
-- regime(旧制度/移行期/新制度)の判定方式(2026-09-19決定、重要):
--   2024年度から、四半期報告書(第1・第3四半期)の提出義務が廃止され、半期報告書に
--   置き換わった(金融商品取引法改正)。日付の単純な閾値比較では判定できないケースが
--   実データで見つかっている(例: 事業年度が2024-04-01より前に開始するのに
--   四半期報告書が1件も存在しない移行期のケース)。そのため、同一(edinet_code,
--   fiscal_year)内に実際に四半期報告書(q1/q2/q3/q4/quarter)が存在すれば旧制度、
--   半期報告書(half)が存在すれば新制度、どちらも存在しなければ移行期、という
--   **実データの存在有無から導出する**方式にする(regulatory calendarの推測に
--   依存しないため頑健)。ファンド型、またはfiscal_yearが特定できない行は
--   regimeもNULLとする。
--
-- doc_descriptionのテキスト欠損について:
--   ごく少数(doc_type_code別に0.1〜0.2%程度)、doc_descriptionが定型文
--   (「四半期報告書」等)のみで期数・四半期号数を含まない書類が実在する
--   (撤回書類ではなく、period_start/period_endは正常。実機確認済み)。
--   正規表現で抽出できない場合はエラーにせず、fiscal_yearはNULL、
--   四半期のperiod_typeは号数を持たない'quarter'にフォールバックする。
--
-- period_type='q4'について:
--   「四半期報告書は第1・第3四半期のみで第4四半期は無い(年次報告書が代替する)」と
--   当初想定していたが、実データに「第4四半期」の四半期報告書が6件実在することを
--   確認した(2026-09-19)。事業年度の期間変更等に伴う変則的な区切りと見られる。
--   正規表現による抽出ロジック自体は変更不要(自動的にq4として抽出される)だが、
--   accepted_valuesテストの許容値にq4を追加している。
--
-- period_type='half'のperiod_endを period_start + 6ヶ月 - 1日 で再計算する
-- (2026-10-03発見・修正、重要): EDINETの書類一覧APIが返すperiodEndは、
-- 半期報告書(doc_type_code='160')については多数の提出者が「対象の半期自体の
-- 終了日」ではなく「事業年度全体の終了日」を記入している実態を実データで確認した
-- (実測: half型8,400件中7,010件=83.5%がperiod_end=同一事業年度のannual行の
-- period_endと一致。訂正履行(opeDateTime)の有無に関わらず広く見られるため、
-- 個別の訂正ミスではなく書類種別自体の提出慣行。実機確認例: カネコ種苗(E00004)
-- は訂正なしの初回提出時点から「半期報告書－第78期(2024/06/01－2025/05/31)」
-- （1年間丸ごと）とdocDescription自体に記載していた)。半期報告書は法定上
-- 必ず事業年度開始から6ヶ月間を対象とするため、period_start（信頼できる、
-- この異常の影響を受けない）から6ヶ月後の月末として算出する方が正しい。
-- q1/q2/q3/q4/annual/quarterはこの異常が実データで確認できなかったため対象外。
--
-- 訂正書類(doc_type_code='130'/'150'/'170')の取り込み(2026-10-08追加、重要):
-- 当初オリジナル書類(120/140/160)のみを対象にしていたが、訂正有価証券報告書等が
-- 一切反映されず、訂正前の値がEDINET側の「確定値」として使われ続けるバグがあった
-- (2026-10-08、J-Quants照合プロジェクトでイシン株式会社の実機データから発覚。
-- 訂正有報(2025-11-14提出)がレイクには正しく保存されているのに、本モデルのdoc_
-- type_codeフィルタで除外されていたため、ウェアハウス層には訂正前のEPS/BPS等が
-- 残り続けていた)。target_periods側は元々submit_date_time最新の1件を選ぶ設計
-- (mart__edinet__financial_indicators参照)だったため、このフィルタを直すだけで
-- 「訂正があれば訂正後を優先する」という意図していた挙動が有効になる。
--
-- 訂正書類はEDINET側がperiodStart/periodEndを常にNULLで返す(実機確認: イシンの
-- 訂正有報でperiodStart/periodEnd共にNULL、代わりにparentDocIDで訂正元の書類を
-- 指す)。そのため訂正元(parent_doc_id)からperiod_start/period_endを補完する。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/edinet_report_periods.parquet',
    format='parquet'
) }}

with raw_docs as (
    select *
    from {{ ref('cleansed__edinet__documents') }}
    where doc_type_code in ('120', '130', '140', '150', '160', '170')
),

base as (
    select
        d.doc_id,
        d.edinet_code,
        d.sec_code,
        d.filer_name,
        case when d.ordinance_code = '030' then 'fund' else 'company' end as filer_category,
        nullif(regexp_extract(d.doc_description, '第([0-9]+)期', 1), '')::integer as fiscal_year,
        case
            when d.doc_type_code in ('120', '130') then 'annual'
            when d.doc_type_code in ('160', '170') then 'half'
            when d.doc_type_code in ('140', '150') then
                case
                    when regexp_extract(d.doc_description, '第([0-9])四半期', 1) <> ''
                        then 'q' || regexp_extract(d.doc_description, '第([0-9])四半期', 1)
                    else 'quarter'
                end
        end as period_type,
        coalesce(d.period_start, p.period_start) as period_start,
        coalesce(d.period_end, p.period_end) as period_end,
        d.submit_date_time,
        d.doc_type_code,
        d.form_code
    from raw_docs d
    left join raw_docs p on p.doc_id = d.parent_doc_id
),

regime_flags as (
    select
        edinet_code,
        fiscal_year,
        max(case when period_type in ('q1', 'q2', 'q3', 'q4', 'quarter') then 1 else 0 end) as has_quarterly,
        max(case when period_type = 'half' then 1 else 0 end) as has_half
    from base
    where filer_category = 'company' and fiscal_year is not null
    group by 1, 2
)

select
    b.doc_id,
    b.edinet_code,
    b.sec_code,
    b.filer_name,
    b.filer_category,
    b.fiscal_year,
    b.period_type,
    b.period_start,
    case
        when b.period_type = 'half' and b.period_start is not null
            then (b.period_start + interval 6 month - interval 1 day)::date
        else b.period_end
    end as period_end,
    case
        when b.filer_category <> 'company' or b.fiscal_year is null then null
        when rf.has_quarterly = 1 then '旧制度'
        when rf.has_half = 1 then '新制度'
        else '移行期'
    end as regime,
    b.submit_date_time,
    b.doc_type_code,
    b.form_code
from base b
left join regime_flags rf
    on rf.edinet_code = b.edinet_code and rf.fiscal_year = b.fiscal_year
-- 訂正元(parent_doc_id)がレイクに存在せずperiod_start/period_endを補完できない行は
-- 除外する(2026-10-08確認、1,431件該当。全件が「訂正元書類がedinet-dlのバックフィル
-- 開始(2016-08-13)より前に提出されており取得範囲外」のケースで、period_type別の
-- 連鎖切れ等の別パターンは実データで確認されなかった。period_endが無いと後続の
-- 期間ベース結合で使えないため、NULLのまま残すより除外する方が安全)。
where b.period_start is not null and b.period_end is not null
