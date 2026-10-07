from decimal import Decimal

from validate.compare_jquants import exact_matches, sales_matches


def test_sales_matches_allows_million_yen_truncation() -> None:
    assert sales_matches(Decimal("7312599000.0000"), Decimal("7312000000.00")) is True


def test_sales_matches_rejects_other_differences() -> None:
    assert sales_matches(Decimal("7312599000.0000"), Decimal("7000000000.00")) is False


def test_sales_matches_none_when_either_side_missing() -> None:
    assert sales_matches(None, Decimal("1.0")) is None
    assert sales_matches(Decimal("1.0"), None) is None


def test_exact_matches_requires_identical_value() -> None:
    assert exact_matches(Decimal("121.6000"), Decimal("121.6000")) is True
    assert exact_matches(Decimal("121.6000"), Decimal("121.6001")) is False


def test_exact_matches_none_when_either_side_missing() -> None:
    assert exact_matches(None, None) is None
