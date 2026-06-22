"""Recurring-document *series* detection and comparative statistics.

A series is the set of documents sharing one ``(sender_id, kind_id)`` — e.g.
the monthly energy bill from one provider. This module answers comparative
questions ("more than usual?", "vs last year?", "trending up?") over a series'
``amount_total``, on the fly (no materialised table). Pure statistics live in
module-level helpers; ``summarize_series`` orchestrates DB loading + bucketing.

Money is ``Decimal`` quantized to 2dp; percentages and z-scores are floats.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

_CENTS = Decimal("0.01")

Cadence = Literal["monthly", "quarterly", "yearly", "irregular"]
Verdict = Literal["higher", "typical", "lower"]
TrendDirection = Literal["rising", "falling", "flat"]

# (label, low_days, high_days) for median-gap classification.
_CADENCE_BANDS: tuple[tuple[Cadence, int, int], ...] = (
    ("monthly", 24, 38),
    ("quarterly", 80, 100),
    ("yearly", 330, 400),
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS)


@dataclass(frozen=True, slots=True)
class Distribution:
    """Summary stats over a series' amounts (one currency bucket)."""

    count: int
    mean: Decimal
    median: Decimal
    stdev: Decimal
    minimum: Decimal
    maximum: Decimal


def distribution(amounts: list[Decimal]) -> Distribution:
    """Distribution stats over a non-empty list of amounts.

    Sample stdev (``statistics.stdev``) needs n>=2; for a single value the
    stdev is 0.
    """
    if not amounts:
        raise ValueError("distribution requires at least one amount")
    stdev = statistics.stdev(amounts) if len(amounts) > 1 else Decimal("0")
    return Distribution(
        count=len(amounts),
        mean=_money(statistics.mean(amounts)),
        median=_money(statistics.median(amounts)),
        stdev=_money(Decimal(stdev)),
        minimum=_money(min(amounts)),
        maximum=_money(max(amounts)),
    )


def classify_cadence(dates: list[date]) -> Cadence:
    """Classify the recurrence cadence from the median gap between sorted dates."""
    if len(dates) < 2:
        return "irregular"
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in itertools.pairwise(ordered)]
    median_gap = statistics.median(gaps)
    for label, low, high in _CADENCE_BANDS:
        if low <= median_gap <= high:
            return label
    return "irregular"


# Tolerance (days) around the 1-year-prior anchor for YoY matching, by cadence.
_YOY_TOLERANCE: dict[Cadence, int] = {
    "monthly": 45,
    "quarterly": 60,
    "yearly": 120,
    "irregular": 60,
}


@dataclass(frozen=True, slots=True)
class ReferenceComparison:
    value: Decimal
    delta: Decimal
    vs_median_pct: float
    z_score: float | None
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class Trend:
    direction: TrendDirection
    change_pct: float


@dataclass(frozen=True, slots=True)
class YearOverYear:
    prior_value: Decimal
    change_pct: float
    document_id: int


def compare_reference(
    value: Decimal, dist: Distribution, typical_pct: float
) -> ReferenceComparison:
    """Where ``value`` falls relative to the series median.

    ``verdict`` is ``typical`` when within 1 stdev OR within ``typical_pct`` of
    the median; otherwise ``higher``/``lower`` by sign of the delta.
    """
    delta = _money(value - dist.median)
    median = dist.median
    vs_median_pct = float(delta / median) if median != 0 else 0.0
    z_score = float(delta / dist.stdev) if dist.stdev != 0 else None
    within_stdev = dist.stdev != 0 and abs(delta) <= dist.stdev
    within_pct = median != 0 and abs(vs_median_pct) <= typical_pct
    if within_stdev or within_pct or delta == 0:
        verdict: Verdict = "typical"
    elif delta > 0:
        verdict = "higher"
    else:
        verdict = "lower"
    return ReferenceComparison(
        value=_money(value),
        delta=delta,
        vs_median_pct=vs_median_pct,
        z_score=z_score,
        verdict=verdict,
    )


def compute_trend(points: list[tuple[date, Decimal]], flat_pct: float) -> Trend | None:
    """Trend over chronologically-ordered (date, amount) points.

    ``flat`` when ``|first→last change|`` <= ``flat_pct``; else the sign of the
    least-squares slope decides rising/falling.
    """
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda p: p[0])
    first_amount = float(ordered[0][1])
    last_amount = float(ordered[-1][1])
    if first_amount != 0:
        change_pct = (last_amount - first_amount) / first_amount
        if abs(change_pct) <= flat_pct:
            return Trend(direction="flat", change_pct=change_pct)
    elif last_amount == 0:
        # Both endpoints are zero — genuinely flat.
        return Trend(direction="flat", change_pct=0.0)
    else:
        # first==0 but last!=0: percent change undefined; direction from slope.
        change_pct = 0.0
    base = ordered[0][0]
    xs = [float((d - base).days) for d, _ in ordered]
    ys = [float(a) for _, a in ordered]
    slope = statistics.linear_regression(xs, ys).slope
    return Trend(direction="rising" if slope > 0 else "falling", change_pct=change_pct)


def year_over_year(
    points: list[tuple[date, Decimal, int]], reference_date: date, cadence: Cadence
) -> YearOverYear | None:
    """The member closest to ~1 year before ``reference_date`` (within tolerance)."""
    try:
        anchor = reference_date.replace(year=reference_date.year - 1)
    except ValueError:  # Feb 29 → prior non-leap year
        anchor = reference_date - timedelta(days=365)
    tolerance = _YOY_TOLERANCE[cadence]
    best: tuple[int, date, Decimal, int] | None = None
    for d, amount, doc_id in points:
        if d == reference_date:
            continue
        distance = abs((d - anchor).days)
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, d, amount, doc_id)
    if best is None:
        return None
    _, _, prior_value, doc_id = best
    ref_value = next((a for dt, a, _ in points if dt == reference_date), None)
    change_pct = (
        float((ref_value - prior_value) / prior_value)
        if ref_value is not None and prior_value != 0
        else 0.0
    )
    return YearOverYear(prior_value=_money(prior_value), change_pct=change_pct, document_id=doc_id)
