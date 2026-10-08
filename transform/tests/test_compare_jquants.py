from decimal import Decimal

from validate.compare_jquants import period_adjusted_matches, sales_matches, shares_matches


def test_sales_matches_allows_million_yen_truncation() -> None:
    assert sales_matches(Decimal("7312599000.0000"), Decimal("7312000000.00")) is True


def test_sales_matches_rejects_other_differences() -> None:
    assert sales_matches(Decimal("7312599000.0000"), Decimal("7000000000.00")) is False


def test_sales_matches_none_when_either_side_missing() -> None:
    assert sales_matches(None, Decimal("1.0")) is None
    assert sales_matches(Decimal("1.0"), None) is None


def test_sales_matches_exact_value_not_a_multiple_of_million() -> None:
    # 2026-10-08修正: 完全一致する値でも、それ自体が100万円単位の倍数でない場合に
    # 誤って不一致と判定していたバグの回帰テスト。
    assert sales_matches(Decimal("22329565000.0000"), Decimal("22329565000.00")) is True


def test_period_adjusted_matches_requires_identical_value_when_no_adjustment() -> None:
    assert period_adjusted_matches(Decimal("121.6000"), Decimal("121.6000"), None) is True
    assert period_adjusted_matches(Decimal("121.6000"), Decimal("121.6001"), None) is False


def test_period_adjusted_matches_none_when_either_side_missing() -> None:
    assert period_adjusted_matches(None, None, None) is None


def test_period_adjusted_matches_allows_split_adjustment() -> None:
    # 実機ケース(2026-10-09): DWH=分割前のEPS、J-Quants=分割後のEPSを遡及表示
    assert period_adjusted_matches(Decimal("67.2400"), Decimal("134.4800"), 0.5) is True


def test_period_adjusted_matches_allows_small_rounding_after_adjustment() -> None:
    # 実機ケース: 円単位の丸めが除算で拡大される分の誤差(0.02円)を許容する
    assert period_adjusted_matches(Decimal("-5.3200"), Decimal("-53.1800"), 0.1) is True


def test_period_adjusted_matches_rejects_when_adjustment_does_not_explain_diff() -> None:
    assert period_adjusted_matches(Decimal("100.0"), Decimal("200.0"), 1.0) is False


def test_period_adjusted_matches_rejects_when_adjustment_missing() -> None:
    assert period_adjusted_matches(Decimal("100.0"), Decimal("200.0"), None) is False


def test_shares_matches_exact_value() -> None:
    assert shares_matches(Decimal("1000.0"), Decimal("1000.0"), None) is True


def test_shares_matches_allows_split_adjustment() -> None:
    # ニデックの実機ケース(2024-09-30、1:2分割): DWH=分割前、J-Quants=分割後
    assert shares_matches(Decimal("596284468.0000"), 1192568936, 0.5) is True


def test_shares_matches_rejects_when_adjustment_does_not_explain_diff() -> None:
    assert shares_matches(Decimal("1000.0"), Decimal("2000.0"), 1.0) is False


def test_shares_matches_rejects_when_adjustment_missing() -> None:
    assert shares_matches(Decimal("1000.0"), Decimal("2000.0"), None) is False


def test_shares_matches_none_when_either_side_missing() -> None:
    assert shares_matches(None, Decimal("1.0"), 0.5) is None
    assert shares_matches(Decimal("1.0"), None, 0.5) is None
