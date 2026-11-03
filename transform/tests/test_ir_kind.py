from __future__ import annotations

import pytest

from flows.ir_kind import is_reit_ixbrl_url

BASE = "https://www2.jpx.co.jp/disc/29140/"


@pytest.mark.parametrize(
    "name",
    [
        "081220261029558975_tse-qcedifsm-29140-20261029329140-ixbrl.htm",  # JT 第3四半期(IFRS)
        "x_tse-scedjpsy-13010-ixbrl.htm",  # 事業会社 通期(連結・日本基準)
        "x_tse-acedjpsm-13010-ixbrl.htm",
        "x_tse-snedjpsy-13010-ixbrl.htm",  # 非連結
    ],
)
def test_business_company_ixbrl_is_not_reit(name: str) -> None:
    assert is_reit_ixbrl_url(BASE + name) is False


@pytest.mark.parametrize("name", ["x_tse-anrejpsm-32690-ixbrl.htm", "x_tse-snrejpsm-32690-ixbrl.htm"])
def test_reit_ixbrl_is_reit(name: str) -> None:
    assert is_reit_ixbrl_url(BASE + name) is True


def test_no_ixbrl_url_is_not_reit() -> None:
    assert is_reit_ixbrl_url(None) is False
    assert is_reit_ixbrl_url("") is False


def test_unknown_taxonomy_falls_back_to_reit() -> None:
    assert is_reit_ixbrl_url("https://example/ixbrl.htm") is True


def test_taxonomy_in_directory_part_is_ignored() -> None:
    # ファイル名だけで判定する(パスの途中に`tse-…re`があっても影響しない)
    assert is_reit_ixbrl_url("https://x/tse-anre/y_tse-qcedjpsm-1-ixbrl.htm") is False
