"""Unit tests for series statistics (pure helpers, no DB)."""

from datetime import date
from decimal import Decimal

from library.series import Distribution, classify_cadence, distribution


def test_distribution_basic() -> None:
    d = distribution([Decimal("100"), Decimal("150"), Decimal("200")])
    assert d == Distribution(
        count=3,
        mean=Decimal("150.00"),
        median=Decimal("150.00"),
        stdev=Decimal("50.00"),
        minimum=Decimal("100.00"),
        maximum=Decimal("200.00"),
    )


def test_distribution_single_value_zero_stdev() -> None:
    d = distribution([Decimal("42")])
    assert d.count == 1
    assert d.stdev == Decimal("0.00")
    assert d.median == Decimal("42.00")


def test_classify_cadence_monthly() -> None:
    dates = [date(2025, 1, 5), date(2025, 2, 4), date(2025, 3, 6), date(2025, 4, 5)]
    assert classify_cadence(dates) == "monthly"


def test_classify_cadence_quarterly() -> None:
    dates = [date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1)]
    assert classify_cadence(dates) == "quarterly"


def test_classify_cadence_yearly() -> None:
    dates = [date(2023, 6, 1), date(2024, 6, 1), date(2025, 6, 1)]
    assert classify_cadence(dates) == "yearly"


def test_classify_cadence_irregular() -> None:
    dates = [date(2025, 1, 1), date(2025, 1, 10), date(2025, 9, 1)]
    assert classify_cadence(dates) == "irregular"


def test_classify_cadence_too_few() -> None:
    assert classify_cadence([date(2025, 1, 1)]) == "irregular"
