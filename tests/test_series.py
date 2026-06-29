"""Unit tests for series statistics (pure helpers, no DB)."""

from datetime import date
from decimal import Decimal

import pytest

from library.series import (
    Distribution,
    classify_cadence,
    compare_reference,
    compute_trend,
    decode_series_id,
    distribution,
    encode_series_id,
    year_over_year,
)


def test_encode_series_id_with_currency() -> None:
    assert encode_series_id(7, 2, "EUR") == "7-2-EUR"


def test_encode_series_id_null_currency() -> None:
    assert encode_series_id(7, 2, None) == "7-2-none"


def test_decode_series_id_roundtrip() -> None:
    assert decode_series_id("7-2-EUR") == (7, 2, "EUR")
    assert decode_series_id("7-2-none") == (7, 2, None)


def test_decode_series_id_rejects_malformed() -> None:
    for bad in ("", "7-2", "a-2-EUR", "7-2-EUR-extra", "not-a-series"):
        with pytest.raises(ValueError):
            decode_series_id(bad)


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


def _dist(values: list[str]) -> Distribution:
    return distribution([Decimal(v) for v in values])


def test_compare_reference_higher() -> None:
    dist = _dist(["100", "100", "100"])  # median 100, stdev 0
    cmp = compare_reference(Decimal("130"), dist, typical_pct=0.10)
    assert cmp.verdict == "higher"
    assert cmp.delta == Decimal("30.00")
    assert cmp.z_score is None  # stdev 0
    assert round(cmp.vs_median_pct, 3) == 0.30


def test_compare_reference_typical_within_pct() -> None:
    dist = _dist(["100", "100", "100"])
    cmp = compare_reference(Decimal("108"), dist, typical_pct=0.10)
    assert cmp.verdict == "typical"  # 8% <= 10% band


def test_compare_reference_typical_within_stdev() -> None:
    dist = _dist(["100", "150", "200"])  # median 150, stdev 50
    cmp = compare_reference(Decimal("180"), dist, typical_pct=0.01)
    assert cmp.verdict == "typical"  # within 1 stdev even though >1% of median
    assert cmp.z_score is not None


def test_compare_reference_lower() -> None:
    dist = _dist(["100", "100", "100"])
    cmp = compare_reference(Decimal("50"), dist, typical_pct=0.10)
    assert cmp.verdict == "lower"


def test_compute_trend_rising() -> None:
    pts = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 2, 1), Decimal("120")),
        (date(2025, 3, 1), Decimal("140")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "rising"
    assert round(trend.change_pct, 2) == 0.40


def test_compute_trend_flat() -> None:
    pts = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 2, 1), Decimal("101")),
        (date(2025, 3, 1), Decimal("102")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "flat"


def test_compute_trend_none_when_single() -> None:
    assert compute_trend([(date(2025, 1, 1), Decimal("100"))], flat_pct=0.05) is None


def test_year_over_year_match() -> None:
    pts = [
        (date(2024, 3, 1), Decimal("100"), 11),
        (date(2025, 1, 1), Decimal("130"), 12),
        (date(2025, 3, 5), Decimal("150"), 13),
    ]
    yoy = year_over_year(pts, reference_date=date(2025, 3, 5), cadence="monthly")
    assert yoy is not None
    assert yoy.document_id == 11
    assert yoy.prior_value == Decimal("100.00")
    assert round(yoy.change_pct, 2) == 0.50


def test_year_over_year_no_match() -> None:
    pts = [(date(2025, 1, 1), Decimal("130"), 12), (date(2025, 3, 5), Decimal("150"), 13)]
    assert year_over_year(pts, reference_date=date(2025, 3, 5), cadence="monthly") is None


def test_compute_trend_rising_from_zero() -> None:
    """first_amount==0 must not force 'flat'; direction should be 'rising'."""
    pts = [
        (date(2025, 1, 1), Decimal("0")),
        (date(2025, 2, 1), Decimal("100")),
        (date(2025, 3, 1), Decimal("200")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "rising"


def test_compute_trend_falling() -> None:
    """Clearly downward series returns 'falling'."""
    pts = [
        (date(2025, 1, 1), Decimal("300")),
        (date(2025, 2, 1), Decimal("200")),
        (date(2025, 3, 1), Decimal("100")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "falling"


def test_year_over_year_zero_reference() -> None:
    """ref_value==Decimal('0.00') must not be falsy-skipped; change_pct == -1.0."""
    pts = [
        (date(2024, 3, 1), Decimal("100"), 20),
        (date(2025, 3, 1), Decimal("0.00"), 21),
    ]
    yoy = year_over_year(pts, reference_date=date(2025, 3, 1), cadence="monthly")
    assert yoy is not None
    assert yoy.change_pct == -1.0
