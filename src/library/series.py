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
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.fx import convert_amount
from library.models import (
    AuthoredSeries,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    Kind,
    MemberOrigin,
    OverrideAction,
    ReviewStatus,
    Sender,
    SeriesInsight,
    SeriesMembershipOverride,
    SeriesMetaOverride,
    SeriesMode,
)
from library.search import DocumentFilters, filter_conditions

logger = logging.getLogger(__name__)

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


# Sentinel for the NULL currency bucket in a URL-safe series id.
_NO_CURRENCY = "none"


def encode_series_id(sender_id: int, kind_id: int, currency: str | None) -> str:
    """A stable, URL-safe id for a series identity: ``{sender}-{kind}-{currency}``.

    The currency is the bucket's 3-letter code, or ``none`` for the NULL bucket.
    Mirrors ``ChartsView.seriesKey`` on the frontend so the two are
    interchangeable (deep links, single-chart fetch).
    """
    return f"{sender_id}-{kind_id}-{currency if currency is not None else _NO_CURRENCY}"


def decode_series_id(series_id: str) -> tuple[int, int, str | None]:
    """Inverse of ``encode_series_id``; raises ``ValueError`` on a malformed id."""
    parts = series_id.split("-")
    if len(parts) != 3:
        raise ValueError(f"malformed series id: {series_id!r}")
    sender_part, kind_part, currency_part = parts
    sender_id = int(sender_part)  # raises ValueError on non-numeric
    kind_id = int(kind_part)
    currency = None if currency_part == _NO_CURRENCY else currency_part
    return sender_id, kind_id, currency


# Prefix marking an *authored* (user-curated) series id, distinguishing it from
# an emergent ``{sender}-{kind}-{currency}`` id. Authored ids are ``a-{id}``
# (two parts), so they never collide with the three-part emergent scheme.
_AUTHORED_PREFIX = "a-"


def encode_authored_series_id(authored_id: int) -> str:
    """A stable, URL-safe id for an authored series: ``a-{id}``."""
    return f"{_AUTHORED_PREFIX}{authored_id}"


def decode_authored_series_id(series_id: str) -> int | None:
    """The authored-series id if ``series_id`` is an ``a-{id}`` token, else ``None``.

    Returns ``None`` (rather than raising) for any non-authored id so callers can
    fall back to the emergent ``decode_series_id`` scheme.
    """
    if not series_id.startswith(_AUTHORED_PREFIX):
        return None
    rest = series_id[len(_AUTHORED_PREFIX) :]
    try:
        return int(rest)
    except ValueError:
        return None


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
    review_status: ReviewStatus


@dataclass(frozen=True, slots=True)
class _Dated:
    """A member whose ``document_date`` is known, with the date lifted out.

    ``_Member.document_date`` is ``date | None``, but every time-series statistic
    — ordering, cadence, trend, year-over-year — needs a real date. Filtering
    ``members`` in a generator establishes that at runtime and nowhere in the
    types: the narrowing does not survive past the comprehension, so each
    downstream use had to be trusted rather than checked.

    Lifting the date into its own non-optional field once, at the point the
    filter happens, is what lets the rest of ``_summarize_members`` be typed.
    """

    member: _Member
    date: date


@dataclass(frozen=True, slots=True)
class SeriesSignature:
    """The mechanical identity of a series: its dominant member triple + dominance.

    ``(sender_id, kind_id, currency)`` is the most common triple across a
    series' members; ``dominance`` is the fraction of members that share it
    (``dominant_count / member_count``). A high dominance means the series has a
    clear, consistent identity that a new document can be matched against
    (auto-continue); a low one means the membership is mixed and no confident
    match is possible.
    """

    sender_id: int | None
    kind_id: int | None
    currency: str | None
    member_count: int
    dominant_count: int
    dominance: float


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
    # A user description override (SeriesMetaOverride) replaces it when present.
    description: str | None
    # Present for emergent series (``summarize_series``); None for authored
    # series, whose narrowing rules differ and are not reported yet. Absent
    # therefore means "not reported", never "nothing was dropped" — an empty
    # `excluded` inside a present coverage is what means the latter.
    coverage: SeriesCoverage | None = None
    # A user title override (SeriesMetaOverride); None when no override is set,
    # in which case the frontend falls back to the derived heading.
    title: str | None = None
    # The authored-series id when this summary is a user-curated (authored)
    # series (W14); None for emergent ``(sender, kind, currency)`` series. The
    # frontend branches on this: an authored series edits its own row (PATCH)
    # rather than the meta-override endpoint.
    authored_id: int | None = None
    # Whether an authored series is hand-curated or a Smart Group; None for
    # emergent series (which have no ``mode`` concept).
    mode: SeriesMode | None = None
    # Count of this authored series' members whose ``origin`` is
    # ``MemberOrigin.AUTO`` (silently joined by the semantic auto-add job,
    # rather than added by hand or an accepted suggestion). Always 0 for
    # emergent series.
    auto_added_count: int = 0
    # document_id -> MemberOrigin, for authored series only (empty for
    # emergent series, which have no membership-origin concept). Lets each
    # point in the "documents in this series" list carry how it joined.
    origins: dict[int, MemberOrigin] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SeriesCoverage:
    """How much of the filtered set a series summary's statistics account for.

    ``summarize_series`` narrows aggressively and used to do it silently: it
    drops documents with no amount, then every ``(sender_id, kind_id)`` group
    but the most populous, then every currency bucket but the dominant one.
    Each narrowing is deliberate — a series must be one provider's one kind of
    document in one currency — but the result was a "usual" band computed over
    an unknown fraction of what the caller asked about.

    ``matched`` is every non-deleted document meeting the caller's filters,
    UNION anything a persisted PIN override pulled in from outside those
    filters (a PIN is keyed on the resolved series identity, not on
    ``filters``, and can reach documents the filters never touched).
    ``included`` is what the statistics were computed from, and ``excluded``
    maps a reason to a count — ``no_amount``, ``other_series_group``,
    ``other_currency``, and (when a persisted EXCLUDE override removed a
    document that would otherwise have been included)
    ``manually_excluded``. A document a PIN restores is subtracted back out of
    whichever reason it would otherwise have landed in, so it is never counted
    twice. ``included + sum(excluded.values()) == matched`` is an invariant,
    pinned by a test for every combination of PIN and EXCLUDE. Reasons that
    dropped nothing are omitted, so an empty ``excluded`` reads as "the
    statistics cover everything that matched".

    That invariant, and the ``manually_excluded``/override reconciliation
    above, hold once a currency bucket has been chosen and overrides resolved
    against it — which is every ``status="ok"`` result, and the
    ``status="insufficient"`` one returned when a chosen bucket still falls
    short of ``settings.series_min_documents`` after overrides run. There is
    an earlier, narrower ``status="insufficient"`` exit, before either of
    those has happened: when fewer than ``series_min_documents`` documents
    even match the caller's filters, ``summarize_series`` returns before
    picking a currency bucket and therefore before resolving any override
    (overrides are keyed on a resolved ``(sender, kind, currency)`` identity
    that does not exist yet at that point). On that path ``excluded`` holds
    only ``no_amount``/``other_series_group``, and both ``included`` and the
    ``"insufficient"`` verdict itself predate any PIN/EXCLUDE — a series a
    PIN would push over the threshold can still be reported insufficient
    there, and a document an EXCLUDE would drop is still counted in
    ``included``. This early-return-before-bucketing behaviour predates this
    coverage feature; only the numbers it now surfaces are new, and only the
    ``"ok"``/post-bucketing-``"insufficient"`` results carry the full
    invariant this class otherwise promises.

    ``needs_review`` counts documents *inside* ``included`` whose extracted
    metadata the validator flagged — most often an ``amount_grounding``
    finding, meaning an amount in this very distribution does not appear in
    its document's text.
    """

    matched: int
    included: int
    excluded: dict[str, int]
    needs_review: int


async def _load_members(
    session: AsyncSession, filters: DocumentFilters
) -> tuple[list[_Member], int, int]:
    """Members of the dominant series matching ``filters``, plus what was dropped.

    Returns ``(members, no_amount_count, other_group_count)``. The two counts
    are the documents this function discards: those with no ``amount_total``
    (they cannot contribute a data point) and those in a non-dominant
    ``(sender_id, kind_id)`` group (a loosely-filtered query must not mix two
    providers into one series). The caller folds them into
    :class:`SeriesCoverage`; returning them rather than logging them is what
    lets an answer say how much of the archive its "usual" band covers.
    """
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
            Document.review_status,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(*filter_conditions(filters), Document.amount_total.isnot(None))
    )
    rows = (await session.execute(statement)).all()

    # Counted with its own aggregate rather than inferred: the statement above
    # already excludes amountless documents, so they are not in `rows` to count.
    no_amount = int(
        (
            await session.execute(
                select(func.count(Document.id)).where(
                    *filter_conditions(filters), Document.amount_total.is_(None)
                )
            )
        ).scalar_one()
    )

    # Restrict to the single most-populous (sender_id, kind_id) group so a
    # loosely-filtered query (kind only) can't mix providers into one series.
    groups: dict[tuple[int | None, int | None], list[_Member]] = {}
    for did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status in rows:
        groups.setdefault((sid, kid), []).append(
            _Member(did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status)
        )
    if not groups:
        return [], no_amount, 0
    dominant = max(groups.values(), key=len)
    other_group = sum(len(group) for group in groups.values() if group is not dominant)
    return dominant, no_amount, other_group


async def _load_override_ids(
    session: AsyncSession,
    sender_id: int | None,
    kind_id: int | None,
    currency: str | None,
) -> tuple[set[int], set[int]]:
    """``(pinned_ids, excluded_ids)`` for one ``(sender, kind, currency)`` series.

    The override table's ``sender_id``/``kind_id`` are NOT NULL, so a series with
    no resolved identity simply matches nothing.
    """
    if sender_id is None or kind_id is None:
        return set(), set()
    currency_match = (
        SeriesMembershipOverride.currency.is_(None)
        if currency is None
        else SeriesMembershipOverride.currency == currency
    )
    statement = select(SeriesMembershipOverride.document_id, SeriesMembershipOverride.action).where(
        SeriesMembershipOverride.sender_id == sender_id,
        SeriesMembershipOverride.kind_id == kind_id,
        currency_match,
    )
    pinned: set[int] = set()
    excluded: set[int] = set()
    for document_id, action in (await session.execute(statement)).all():
        (pinned if action == OverrideAction.PIN else excluded).add(document_id)
    return pinned, excluded


async def _convert_into(
    session: AsyncSession,
    amount: Decimal,
    currency: str | None,
    target: str | None,
    on_date: date | None,
) -> Decimal | None:
    """Convert ``amount`` into ``target``, or ``None`` when that is impossible.

    A series bucket can be keyed on ``None`` — the documents that carry no
    currency at all — and there is nothing to convert *into* such a bucket: only
    a member that is itself currency-less belongs in it.

    That case used to reach ``convert_amount``, whose ``to_currency: str``
    contract it violates. The rate lookup for ``None`` then returned no rate, so
    the member was dropped and logged as "no FX rate EUR->None". The outcome was
    right by accident, arrived at through a failed lookup rather than a stated
    rule. This states the rule; the behaviour is unchanged in both directions.
    """
    if target is None or currency is None:
        return amount if currency == target else None
    return await convert_amount(session, amount, currency, target, on_date)


async def _load_pinned_members(
    session: AsyncSession, document_ids: list[int], target_currency: str | None
) -> list[_Member]:
    """Load force-pinned documents as members of the ``target_currency`` bucket.

    A pinned document is included by id regardless of its own sender/kind. Its
    amount is FX-converted into ``target_currency`` at its own date; a document
    with no amount or no resolvable rate is dropped from the stats (logged) — it
    cannot contribute a meaningful data point.
    """
    if not document_ids:
        return []
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
            Document.review_status,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(
            Document.id.in_(document_ids),
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
        )
    )
    rows = (await session.execute(statement)).all()
    members: list[_Member] = []
    for did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status in rows:
        converted = await _convert_into(session, amount, currency, target_currency, ddate)
        if converted is None:
            logger.warning(
                "series pin doc %s: no FX rate %s->%s; dropped from series stats",
                did,
                currency,
                target_currency,
            )
            continue
        members.append(
            _Member(
                did,
                sname,
                kslug,
                ddate,
                _money(converted),
                target_currency,
                sid,
                kid,
                title,
                review_status,
            )
        )
    return members


async def _apply_overrides(
    session: AsyncSession,
    bucket: list[_Member],
    *,
    sender_id: int | None,
    kind_id: int | None,
    currency: str | None,
) -> list[_Member]:
    """Apply persisted pin/exclude overrides to a currency bucket."""
    pinned_ids, excluded_ids = await _load_override_ids(session, sender_id, kind_id, currency)
    if not pinned_ids and not excluded_ids:
        return bucket
    result = [m for m in bucket if m.document_id not in excluded_ids]
    present = {m.document_id for m in result}
    to_pin = [pid for pid in pinned_ids if pid not in present]
    result.extend(await _load_pinned_members(session, to_pin, currency))
    return result


def _insufficient(members: list[_Member], coverage: SeriesCoverage | None = None) -> SeriesSummary:
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
        coverage=coverage,
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


async def load_series_meta_override(
    session: AsyncSession,
    sender_id: int | None,
    kind_id: int | None,
    currency: str | None,
) -> tuple[str | None, str | None]:
    """The user ``(title, description)`` override for one series, if any.

    Each is ``None`` when no override row exists or that column is unset. A
    series with no resolved identity (no sender/kind) matches nothing.
    """
    if sender_id is None or kind_id is None:
        return None, None
    currency_match = (
        SeriesMetaOverride.currency.is_(None)
        if currency is None
        else SeriesMetaOverride.currency == currency
    )
    statement = select(SeriesMetaOverride.title, SeriesMetaOverride.description).where(
        SeriesMetaOverride.sender_id == sender_id,
        SeriesMetaOverride.kind_id == kind_id,
        currency_match,
    )
    row = (await session.execute(statement)).first()
    if row is None:
        return None, None
    return row[0], row[1]


def _summarize_members(
    members: list[_Member],
    *,
    currency: str | None,
    settings: Settings,
    sender: str | None = None,
    kind: str | None = None,
    sender_id: int | None = None,
    kind_id: int | None = None,
    other_currencies: list[str] | None = None,
    reference: Decimal | Literal["latest"] | None = "latest",
    reference_date: date | None = None,
    description: str | None = None,
    title: str | None = None,
    authored_id: int | None = None,
    mode: SeriesMode | None = None,
    auto_added_count: int = 0,
    origins: dict[int, MemberOrigin] | None = None,
    coverage: SeriesCoverage | None = None,
) -> SeriesSummary:
    """Build a ``SeriesSummary`` from an already-resolved set of ``members``.

    ``members`` are the documents of one currency bucket (all amounts in
    ``currency``); membership/grouping/override resolution has already happened.
    Shared by emergent ``summarize_series`` and authored
    ``summarize_authored_series`` so both produce identical distribution / trend
    / reference / year-over-year math. The caller supplies the identity labels,
    the resolved ``description``/``title``, and (for authored series)
    ``authored_id``/``mode``/``auto_added_count``.
    """
    dated = sorted(
        (_Dated(m, d) for m in members if (d := m.document_date) is not None),
        key=lambda item: item.date,
    )
    points = [(item.date, item.member.amount, item.member.document_id) for item in dated]
    trend_points = [(item.date, item.member.amount) for item in dated]
    amounts = [m.amount for m in members]
    dist = distribution(amounts)
    cadence = classify_cadence([item.date for item in dated])
    trend = compute_trend(trend_points, settings.series_flat_pct)

    # Resolve the reference value + anchor date.
    ref_value: Decimal | None
    ref_date = reference_date
    if reference == "latest":
        ref_value = dated[-1].member.amount if dated else None
        ref_date = ref_date or (dated[-1].date if dated else None)
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
        yoy_points = [(item.date, item.member.amount, item.member.document_id) for item in dated]
        if ref_value is not None and ref_date not in {p[0] for p in points}:
            yoy_points.append((ref_date, ref_value, -1))
        yoy = year_over_year(yoy_points, ref_date, cadence)

    return SeriesSummary(
        status="ok",
        sender=sender,
        kind=kind,
        sender_id=sender_id,
        kind_id=kind_id,
        currency=currency,
        other_currencies=other_currencies or [],
        cadence=cadence,
        count=len(members),
        distribution=dist,
        reference=comparison,
        trend=trend,
        year_over_year=yoy,
        document_ids=sorted(m.document_id for m in members)[:MAX_CITED_IDS],
        points=points,
        titles={m.document_id: m.title for m in members},
        description=description,
        title=title,
        authored_id=authored_id,
        mode=mode,
        auto_added_count=auto_added_count,
        origins=origins or {},
        coverage=coverage,
    )


def _needs_review_count(bucket: list[_Member]) -> int:
    return sum(1 for member in bucket if member.review_status is ReviewStatus.NEEDS_REVIEW)


async def _coverage_after_overrides(
    session: AsyncSession,
    filters: DocumentFilters,
    *,
    matched_base: int,
    no_amount: int,
    other_group: int,
    other_currency: int,
    dominant_sender_id: int | None,
    dominant_kind_id: int | None,
    bucket_pre_override: list[_Member],
    final_bucket: list[_Member],
) -> SeriesCoverage:
    """Coverage once persisted PIN/EXCLUDE overrides have been applied.

    Overrides act on a resolved ``(sender, kind, currency)`` identity, not on
    ``filters`` — a PIN loads its document "regardless of its own sender/kind"
    (see ``_load_pinned_members``) and can therefore restore a document this
    call would otherwise have dropped as ``other_series_group`` or
    ``other_currency`` (subtracted back out of that reason so it isn't double
    counted), or add one from entirely outside ``filters`` (which grows
    ``matched`` itself: ``matched`` is "everything filters matched, union
    anything pinned in"). An EXCLUDE removes a document that WAS in
    ``bucket_pre_override`` into a new ``manually_excluded`` reason. A
    document that is both pinned and excluded nets to "unchanged" under this
    set-difference approach, which is the only sane reading of a
    self-contradictory override pair.

    ``no_amount`` never needs adjusting here: ``_load_pinned_members`` filters
    out amountless documents in its own SQL, so one can never reach
    ``final_bucket`` via a pin.
    """
    pre_ids = {m.document_id for m in bucket_pre_override}
    final_ids = {m.document_id for m in final_bucket}
    manually_excluded = len(pre_ids - final_ids)
    newly_pinned_ids = final_ids - pre_ids

    pinned_outside_filters = 0
    pinned_other_group = 0
    pinned_other_currency = 0
    if newly_pinned_ids:
        filter_matched_ids = set(
            (
                await session.execute(
                    select(Document.id).where(
                        *filter_conditions(filters), Document.id.in_(newly_pinned_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        for member in final_bucket:
            if member.document_id not in newly_pinned_ids:
                continue
            if member.document_id not in filter_matched_ids:
                pinned_outside_filters += 1
            elif (member.sender_id, member.kind_id) != (dominant_sender_id, dominant_kind_id):
                pinned_other_group += 1
            else:
                pinned_other_currency += 1

    excluded = {
        reason: count
        for reason, count in (
            ("no_amount", no_amount),
            ("other_series_group", other_group - pinned_other_group),
            ("other_currency", other_currency - pinned_other_currency),
            ("manually_excluded", manually_excluded),
        )
        if count > 0
    }
    return SeriesCoverage(
        matched=matched_base + pinned_outside_filters,
        included=len(final_bucket),
        excluded=excluded,
        needs_review=_needs_review_count(final_bucket),
    )


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
    members, no_amount, other_group = await _load_members(session, filters)
    matched_base = len(members) + no_amount + other_group

    if len(members) < settings.series_min_documents:
        # No currency bucket has been chosen yet, so overrides (which are
        # keyed on a resolved (sender, kind, currency) identity) have not
        # been looked up at all — nothing to reclassify.
        excluded = {
            reason: count
            for reason, count in (("no_amount", no_amount), ("other_series_group", other_group))
            if count > 0
        }
        coverage = SeriesCoverage(
            matched=matched_base,
            included=len(members),
            excluded=excluded,
            needs_review=_needs_review_count(members),
        )
        return _insufficient(members, coverage)

    # Currency bucket: the requested/dominant currency.
    by_currency: dict[str | None, list[_Member]] = {}
    for m in members:
        by_currency.setdefault(m.currency, []).append(m)
    # Annotated because the dominant bucket may legitimately be the NULL one:
    # inferring from the `reference_currency` branch alone would make this `str`
    # and hide that. `SeriesSummary.currency` is `str | None` for the same reason.
    currency: str | None
    if reference_currency is not None and reference_currency in by_currency:
        currency = reference_currency
    else:
        currency = max(by_currency, key=lambda c: len(by_currency[c]))
    bucket_pre_override = by_currency[currency]
    other_currencies = sorted(str(c) for c in by_currency if c != currency and c is not None)
    other_currency = sum(len(group) for code, group in by_currency.items() if code != currency)

    # The series identity is the dominant natural group; all members share it.
    # Capture it before overrides so pins (foreign docs) can't shift the labels.
    series_head = members[0]
    bucket = await _apply_overrides(
        session,
        bucket_pre_override,
        sender_id=series_head.sender_id,
        kind_id=series_head.kind_id,
        currency=currency,
    )
    coverage = await _coverage_after_overrides(
        session,
        filters,
        matched_base=matched_base,
        no_amount=no_amount,
        other_group=other_group,
        other_currency=other_currency,
        dominant_sender_id=series_head.sender_id,
        dominant_kind_id=series_head.kind_id,
        bucket_pre_override=bucket_pre_override,
        final_bucket=bucket,
    )

    if len(bucket) < settings.series_min_documents:
        return _insufficient(bucket, coverage)

    head = series_head
    description = await load_series_description(session, head.sender_id, head.kind_id, currency)
    # User overrides win over the derived heading / cached LLM description.
    title_override, description_override = await load_series_meta_override(
        session, head.sender_id, head.kind_id, currency
    )
    if description_override is not None:
        description = description_override
    return _summarize_members(
        bucket,
        currency=currency,
        settings=settings,
        sender=head.sender,
        kind=head.kind,
        sender_id=head.sender_id,
        kind_id=head.kind_id,
        other_currencies=other_currencies,
        reference=reference,
        reference_date=reference_date,
        description=description,
        title=title_override,
        coverage=coverage,
    )


async def _load_authored_members(
    session: AsyncSession, authored_series_id: int, target_currency: str | None
) -> list[_Member]:
    """Amount-bearing, non-deleted members of an authored series, FX-converted.

    Each member's amount is converted into ``target_currency`` at its own date
    (like a pinned emergent member); a member with no resolvable rate is dropped
    from the stats and logged — it cannot contribute a comparable data point.
    """
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
            Document.review_status,
        )
        .join(AuthoredSeriesMember, AuthoredSeriesMember.document_id == Document.id)
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(
            AuthoredSeriesMember.authored_series_id == authored_series_id,
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
        )
    )
    rows = (await session.execute(statement)).all()
    members: list[_Member] = []
    for did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status in rows:
        converted = await _convert_into(session, amount, currency, target_currency, ddate)
        if converted is None:
            logger.warning(
                "authored series %s doc %s: no FX rate %s->%s; dropped from series stats",
                authored_series_id,
                did,
                currency,
                target_currency,
            )
            continue
        members.append(
            _Member(
                did,
                sname,
                kslug,
                ddate,
                _money(converted),
                target_currency,
                sid,
                kid,
                title,
                review_status,
            )
        )
    return members


async def _load_authored_origins(
    session: AsyncSession, authored_series_id: int
) -> dict[int, MemberOrigin]:
    """``document_id -> origin`` for every (non-deleted) member of an authored series.

    Queried directly against the membership table rather than derived from the
    (amount-bearing only) ``_Member`` rows ``_load_authored_members`` returns, so
    an auto-added document with no resolvable amount/FX rate is still counted.
    Joins ``Document`` and filters ``deleted_at IS NULL`` to match
    ``_load_authored_members`` — otherwise a soft-deleted (not-yet-purged)
    member would still count toward ``auto_added_count`` even though the chart
    no longer shows it.
    """
    statement = (
        select(AuthoredSeriesMember.document_id, AuthoredSeriesMember.origin)
        .join(Document, Document.id == AuthoredSeriesMember.document_id)
        .where(
            AuthoredSeriesMember.authored_series_id == authored_series_id,
            Document.deleted_at.is_(None),
        )
    )
    # `.tuples()` rather than `.all()`: a SQLAlchemy `Row` is a sequence at
    # runtime but is not typed as a 2-tuple, so `dict()` cannot see the pairing.
    # Rewriting it as a comprehension satisfies mypy but trips ruff's C416,
    # which wants `dict()` back — `.tuples()` is what satisfies both.
    return dict((await session.execute(statement)).tuples().all())


def _empty_authored_summary(
    authored: AuthoredSeries,
    auto_added_count: int = 0,
    origins: dict[int, MemberOrigin] | None = None,
) -> SeriesSummary:
    """Summary for an authored series with no amount-bearing members yet.

    Distribution stats need at least one amount, so a fresh/empty authored
    series renders as an ``ok`` chart with no points rather than erroring out.
    """
    return SeriesSummary(
        status="ok",
        sender=None,
        kind=None,
        sender_id=None,
        kind_id=None,
        currency=authored.currency,
        other_currencies=[],
        cadence="irregular",
        count=0,
        distribution=None,
        reference=None,
        trend=None,
        year_over_year=None,
        document_ids=[],
        points=[],
        titles={},
        description=authored.description,
        title=authored.name,
        authored_id=authored.id,
        mode=authored.mode,
        auto_added_count=auto_added_count,
        origins=origins or {},
    )


async def _resolve_display_currency(session: AsyncSession, authored: AuthoredSeries) -> str | None:
    """The currency an authored series charts in.

    An explicit ``authored.currency`` wins. Otherwise fall back to the dominant
    currency among the series' amount-bearing, non-deleted members, so a group
    created without a currency still charts (its members are FX-converted into
    that currency) rather than rendering empty — every member would otherwise be
    dropped converting into a NULL target. ``None`` only when the series has no
    amount-bearing member carrying a currency.
    """
    if authored.currency:
        return authored.currency
    return (
        await session.execute(
            select(Document.currency)
            .join(AuthoredSeriesMember, AuthoredSeriesMember.document_id == Document.id)
            .where(
                AuthoredSeriesMember.authored_series_id == authored.id,
                Document.deleted_at.is_(None),
                Document.amount_total.isnot(None),
                Document.currency.isnot(None),
            )
            .group_by(Document.currency)
            .order_by(func.count().desc(), Document.currency)
            .limit(1)
        )
    ).scalar_one_or_none()


async def summarize_authored_series(
    session: AsyncSession,
    authored_series_id: int,
    settings: Settings,
    *,
    reference: Decimal | Literal["latest"] | None = "latest",
) -> SeriesSummary | None:
    """Summarise a user-curated (authored) series, or ``None`` if it doesn't exist.

    Loads the explicit membership (amount-bearing, non-deleted documents) and
    runs the same statistics as an emergent series via ``_summarize_members``,
    using the authored row's ``name`` as the title and ``description`` as the
    prose. Unlike emergent series there is no minimum-document gate — a single
    amount-bearing member already produces a bar chart. Also carries the
    series' ``mode`` (manual/semantic) and ``auto_added_count`` (members
    silently joined by the semantic auto-add job) for the API body.
    """
    authored = await session.get(AuthoredSeries, authored_series_id)
    if authored is None:
        return None
    display_currency = await _resolve_display_currency(session, authored)
    origins = await _load_authored_origins(session, authored_series_id)
    auto_added_count = sum(1 for o in origins.values() if o == MemberOrigin.AUTO)
    members = await _load_authored_members(session, authored_series_id, display_currency)
    if not members:
        return _empty_authored_summary(authored, auto_added_count, origins)
    return _summarize_members(
        members,
        currency=display_currency,
        settings=settings,
        reference=reference,
        description=authored.description,
        title=authored.name,
        authored_id=authored.id,
        mode=authored.mode,
        auto_added_count=auto_added_count,
        origins=origins,
    )


def derive_signature(members: list[_Member]) -> SeriesSignature | None:
    """The dominant ``(sender_id, kind_id, currency)`` triple over ``members``.

    Pure. ``None`` for an empty membership. Counts each distinct triple and picks
    the most common one; ``dominance`` is that count over the total member count
    (so a homogeneous series has dominance ``1.0``).
    """
    if not members:
        return None
    counts: dict[tuple[int | None, int | None, str | None], int] = {}
    for member in members:
        key = (member.sender_id, member.kind_id, member.currency)
        counts[key] = counts.get(key, 0) + 1
    (sender_id, kind_id, currency), dominant_count = max(counts.items(), key=lambda kv: kv[1])
    member_count = len(members)
    return SeriesSignature(
        sender_id=sender_id,
        kind_id=kind_id,
        currency=currency,
        member_count=member_count,
        dominant_count=dominant_count,
        dominance=dominant_count / member_count,
    )


async def load_authored_signature(
    session: AsyncSession, authored_series_id: int
) -> SeriesSignature | None:
    """The :class:`SeriesSignature` of an authored series' current membership."""
    authored = await session.get(AuthoredSeries, authored_series_id)
    if authored is None:
        return None
    display_currency = await _resolve_display_currency(session, authored)
    members = await _load_authored_members(session, authored_series_id, display_currency)
    return derive_signature(members)


async def suggest_signature_matches(
    session: AsyncSession,
    authored_series_id: int,
    settings: Settings,
    *,
    limit: int | None = None,
) -> list[_Member]:
    """Amount-bearing documents that match an authored series' signature.

    Returns ``[]`` unless the series has a confident signature: a resolved
    sender and kind and a dominance at or above
    ``settings.series_autocontinue_min_dominance``. Matches share the signature's
    ``(sender_id, kind_id, currency)`` (NULL currency matched NULL-aware) and are
    not already members, nor already carry a suggestion row (pending OR
    dismissed) for this series. Newest first, capped at ``limit`` (default
    ``settings.series_suggestion_limit``).
    """
    signature = await load_authored_signature(session, authored_series_id)
    if (
        signature is None
        or signature.sender_id is None
        or signature.kind_id is None
        or signature.dominance < settings.series_autocontinue_min_dominance
    ):
        return []

    member_ids = select(AuthoredSeriesMember.document_id).where(
        AuthoredSeriesMember.authored_series_id == authored_series_id
    )
    suggested_ids = select(AuthoredSeriesSuggestion.document_id).where(
        AuthoredSeriesSuggestion.authored_series_id == authored_series_id
    )
    currency_match = (
        Document.currency.is_(None)
        if signature.currency is None
        else Document.currency == signature.currency
    )
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
            Document.review_status,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.sender_id == signature.sender_id,
            Document.kind_id == signature.kind_id,
            currency_match,
            Document.id.not_in(member_ids),
            Document.id.not_in(suggested_ids),
        )
        .order_by(Document.document_date.desc().nullslast(), Document.id.desc())
        .limit(limit or settings.series_suggestion_limit)
    )
    rows = (await session.execute(statement)).all()
    return [
        _Member(did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status)
        for did, sname, kslug, ddate, amount, currency, sid, kid, title, review_status in rows
    ]


def _odd_one_out_reason(candidate: _Member, axis: str, representative: _Member | None) -> str:
    """A deterministic, grounded one-sentence reason a member breaks the signature.

    Built ONLY from values actually present on the documents — never an LLM — so
    it can never invent a sender / kind / currency that isn't in the series. (An
    LLM previously asked to phrase this hallucinated a sender name that appeared
    in none of the documents.) ``representative`` is any member carrying the
    dominant signature; its real sender/kind/currency names what "the rest of the
    series" is. Both the candidate's and the representative's values come straight
    from the database rows, so the sentence states only facts.
    """
    if axis == "sender":
        subject = f"is from {candidate.sender}" if candidate.sender else "has no sender set"
        usual = representative.sender if representative else None
    elif axis == "kind":
        subject = (
            f"is a {candidate.kind} document" if candidate.kind else "has no document kind set"
        )
        usual = representative.kind if representative else None
    else:  # currency
        subject = f"is in {candidate.currency}" if candidate.currency else "has no currency set"
        usual = representative.currency if representative else None
    if usual:
        return f"This document {subject}, unlike the rest of the series ({usual})."
    return f"This document {subject}, unlike the rest of the series."


def odd_ones_out(
    members: list[_Member], signature: SeriesSignature
) -> list[tuple[_Member, str, str]]:
    """Members whose triple differs from ``signature``'s dominant one.

    Pure. Returns ``(member, axis, reason)`` per odd member. ``axis`` is the
    *first* differing dimension in priority order ``sender → kind → currency`` (a
    document can differ on several; naming the highest-priority one keeps the
    rationale focused, since a different sender is usually the most telling reason
    a document doesn't belong). ``reason`` is a deterministic, grounded sentence
    (:func:`_odd_one_out_reason`) built only from real document values — there is
    no LLM in this path, so the reason can never name something not in the series.
    """
    dominant = (signature.sender_id, signature.kind_id, signature.currency)
    # Any member carrying the dominant signature, to name "the rest of the series".
    representative = next(
        (m for m in members if (m.sender_id, m.kind_id, m.currency) == dominant), None
    )
    result: list[tuple[_Member, str, str]] = []
    for member in members:
        if (member.sender_id, member.kind_id, member.currency) == dominant:
            continue
        if member.sender_id != signature.sender_id:
            axis = "sender"
        elif member.kind_id != signature.kind_id:
            axis = "kind"
        else:
            axis = "currency"
        result.append((member, axis, _odd_one_out_reason(member, axis, representative)))
    return result


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
    if summary.coverage is not None:
        body["coverage"] = asdict(summary.coverage)
    if summary.authored_id is not None:
        body["authored_id"] = summary.authored_id
    if summary.mode is not None:
        body["mode"] = summary.mode.value
        body["auto_added_count"] = summary.auto_added_count
    if summary.title is not None:
        body["title"] = summary.title
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
                **({"origin": summary.origins[did].value} if did in summary.origins else {}),
            }
            for d, a, did in summary.points
        ]
    return body
