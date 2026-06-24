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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.models import Document, Kind, Sender, SeriesInsight
from library.search import DocumentFilters, filter_conditions

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


MAX_CITED_IDS: int = 25


@dataclass(frozen=True, slots=True)
class _Member:
    document_id: int
    sender: str | None
    kind: str | None
    document_date: date | None
    amount: Decimal
    currency: str | None
    sender_id: int | None
    kind_id: int | None
    title: str | None


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    status: Literal["ok", "insufficient"]
    sender: str | None
    kind: str | None
    sender_id: int | None
    kind_id: int | None
    currency: str | None
    other_currencies: list[str]
    cadence: Cadence
    count: int
    distribution: Distribution | None
    reference: ReferenceComparison | None
    trend: Trend | None
    year_over_year: YearOverYear | None
    document_ids: list[int]
    points: list[tuple[date, Decimal, int]]
    # document_id -> title, for per-point citation links on the chart.
    titles: dict[int, str | None]
    # Cached LLM prose summary of the series, or None when not yet generated.
    description: str | None


async def _load_members(session: AsyncSession, filters: DocumentFilters) -> list[_Member]:
    """All non-deleted documents matching ``filters`` that have an amount."""
    statement = (
        select(
            Document.id,
            Sender.name,
            Kind.slug,
            Document.document_date,
            Document.amount_total,
            Document.currency,
            Document.sender_id,
            Document.kind_id,
            Document.title,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(*filter_conditions(filters), Document.amount_total.isnot(None))
    )
    rows = (await session.execute(statement)).all()
    # Restrict to the single most-populous (sender_id, kind_id) group so a
    # loosely-filtered query (kind only) can't mix providers into one series.
    groups: dict[tuple[int | None, int | None], list[_Member]] = {}
    for did, sname, kslug, ddate, amount, currency, sid, kid, title in rows:
        groups.setdefault((sid, kid), []).append(
            _Member(did, sname, kslug, ddate, amount, currency, sid, kid, title)
        )
    if not groups:
        return []
    return max(groups.values(), key=len)


def _insufficient(members: list[_Member]) -> SeriesSummary:
    head = members[0] if members else None
    return SeriesSummary(
        status="insufficient",
        sender=head.sender if head else None,
        kind=head.kind if head else None,
        sender_id=head.sender_id if head else None,
        kind_id=head.kind_id if head else None,
        currency=None,
        other_currencies=[],
        cadence="irregular",
        count=len(members),
        distribution=None,
        reference=None,
        trend=None,
        year_over_year=None,
        document_ids=[m.document_id for m in members[:MAX_CITED_IDS]],
        points=[],
        titles={},
        description=None,
    )


async def load_series_description(
    session: AsyncSession,
    sender_id: int | None,
    kind_id: int | None,
    currency: str | None,
) -> str | None:
    """The cached LLM description for one ``(sender, kind, currency)`` series, if any."""
    if sender_id is None or kind_id is None:
        return None
    currency_match = (
        SeriesInsight.currency.is_(None) if currency is None else SeriesInsight.currency == currency
    )
    statement = select(SeriesInsight.description).where(
        SeriesInsight.sender_id == sender_id,
        SeriesInsight.kind_id == kind_id,
        currency_match,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def summarize_series(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    settings: Settings,
    reference: Decimal | Literal["latest"] | None = "latest",
    reference_date: date | None = None,
    reference_currency: str | None = None,
) -> SeriesSummary:
    """Detect the (sender, kind) series matching ``filters`` and summarise it."""
    members = await _load_members(session, filters)
    if len(members) < settings.series_min_documents:
        return _insufficient(members)

    # Currency bucket: the requested/dominant currency.
    by_currency: dict[str | None, list[_Member]] = {}
    for m in members:
        by_currency.setdefault(m.currency, []).append(m)
    if reference_currency is not None and reference_currency in by_currency:
        currency = reference_currency
    else:
        currency = max(by_currency, key=lambda c: len(by_currency[c]))
    bucket = by_currency[currency]
    other_currencies = sorted(str(c) for c in by_currency if c != currency and c is not None)

    if len(bucket) < settings.series_min_documents:
        return _insufficient(bucket)

    dated = sorted(
        (m for m in bucket if m.document_date is not None), key=lambda m: m.document_date
    )
    points = [(m.document_date, m.amount, m.document_id) for m in dated]
    trend_points = [(m.document_date, m.amount) for m in dated]
    amounts = [m.amount for m in bucket]
    dist = distribution(amounts)
    cadence = classify_cadence([m.document_date for m in dated])
    trend = compute_trend(trend_points, settings.series_flat_pct)

    # Resolve the reference value + anchor date.
    ref_value: Decimal | None
    ref_date = reference_date
    if reference == "latest":
        ref_value = dated[-1].amount if dated else None
        ref_date = ref_date or (dated[-1].document_date if dated else None)
    elif isinstance(reference, Decimal):
        ref_value = reference
    else:
        ref_value = None

    comparison = (
        compare_reference(ref_value, dist, settings.series_typical_pct)
        if ref_value is not None
        else None
    )
    yoy = None
    if ref_date is not None:
        yoy_points = [(m.document_date, m.amount, m.document_id) for m in dated]
        if ref_value is not None and ref_date not in {p[0] for p in points}:
            yoy_points.append((ref_date, ref_value, -1))
        yoy = year_over_year(yoy_points, ref_date, cadence)

    head = bucket[0]
    description = await load_series_description(session, head.sender_id, head.kind_id, currency)
    return SeriesSummary(
        status="ok",
        sender=head.sender,
        kind=head.kind,
        sender_id=head.sender_id,
        kind_id=head.kind_id,
        currency=currency,
        other_currencies=other_currencies,
        cadence=cadence,
        count=len(bucket),
        distribution=dist,
        reference=comparison,
        trend=trend,
        year_over_year=yoy,
        document_ids=sorted(m.document_id for m in bucket)[:MAX_CITED_IDS],
        points=points,
        titles={m.document_id: m.title for m in bucket},
        description=description,
    )


def _pct(fraction: float) -> str:
    return f"{fraction * 100:+.1f}%"


def serialise_summary(summary: SeriesSummary, *, include_points: bool = False) -> dict[str, object]:
    """A JSON-friendly dict (money→str, dates→ISO, fractions→'+6.4%')."""
    body: dict[str, object] = {
        "status": summary.status,
        "sender": summary.sender,
        "kind": summary.kind,
        "sender_id": summary.sender_id,
        "kind_id": summary.kind_id,
        "currency": summary.currency,
        "other_currencies": summary.other_currencies,
        "cadence": summary.cadence,
        "count": summary.count,
        "document_ids": summary.document_ids,
    }
    if summary.description is not None:
        body["description"] = summary.description
    if summary.distribution is not None:
        d = summary.distribution
        body |= {
            "mean": str(d.mean),
            "median": str(d.median),
            "stdev": str(d.stdev),
            "min": str(d.minimum),
            "max": str(d.maximum),
        }
    if summary.reference is not None:
        r = summary.reference
        body["reference"] = {
            "value": str(r.value),
            "delta": str(r.delta),
            "vs_median_pct": _pct(r.vs_median_pct),
            "z_score": None if r.z_score is None else round(r.z_score, 2),
            "verdict": r.verdict,
        }
    if summary.trend is not None:
        body["trend"] = {
            "direction": summary.trend.direction,
            "change_pct": _pct(summary.trend.change_pct),
        }
    if summary.year_over_year is not None:
        y = summary.year_over_year
        body["year_over_year"] = {
            "prior_value": str(y.prior_value),
            "change_pct": _pct(y.change_pct),
            "document_id": y.document_id,
        }
    if include_points:
        body["points"] = [
            {
                "date": d.isoformat(),
                "amount": str(a),
                "document_id": did,
                "title": summary.titles.get(did),
            }
            for d, a, did in summary.points
        ]
    return body
