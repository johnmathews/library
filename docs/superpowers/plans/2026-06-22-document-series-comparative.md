# Document series + comparative queries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect recurring-document "series" (same sender + kind) and answer comparative questions ("more expensive than usual?", "vs last year?", "going up?") in Ask and on the document-detail view, with citations.

**Architecture:** A new read-only module `src/library/series.py` (parallel to `structured_query.py`) owns all series logic via one entry point, `summarize_series`, consumed by both a new Ask tool (`compare_to_series`) and a new endpoint (`GET /api/documents/{id}/series`). No new table, migration, or pipeline stage — everything is computed on the fly. The frontend gets a `DocumentSeriesTrend.vue` widget rendering a Chart.js line chart.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 async, FastAPI, pytest; Vue 3 + TS + Vite + Tailwind + Chart.js (`chart.js` + `vue-chartjs`, new deps).

## Global Constraints

- Python 3.13, full type annotations on every signature and non-obvious variable. `uv` for all deps/tests. `pytest` + `coverage`.
- Money is `Decimal`, serialized to JSON as `str`; dates serialized as ISO strings — matches `structured_query.py`.
- Series logic is pure read-side: NO new table, NO migration, NO pipeline stage.
- Reuse `library.search.DocumentFilters` + `filter_conditions`; reuse `MAX_CITED_IDS = 25` semantics for capping cited ids.
- `ruff format` + `ruff check` clean (backend); `vue-tsc` + `eslint` clean (frontend).
- A series is a single `(sender_id, kind_id)` group; the **distribution baseline includes all members (including the reference document)** — an accepted simplification, fine at n≥3.
- Settings use the `LIBRARY_` env prefix. Config defaults: `SERIES_MIN_DOCUMENTS=3`, `SERIES_TYPICAL_PCT=0.10`, `SERIES_FLAT_PCT=0.05`.
- "Typical" band: reference is `typical` when `|delta| <= 1*stdev` OR `|delta| <= SERIES_TYPICAL_PCT * median` (OR keeps tight series from over-flagging).
- Commit after every task with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Config — three series settings

**Files:**
- Modify: `src/library/config.py` (after the `ask_history_turns` line, ~65)
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.series_min_documents: int`, `Settings.series_typical_pct: float`, `Settings.series_flat_pct: float`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_series_defaults() -> None:
    from library.config import Settings

    settings = Settings()
    assert settings.series_min_documents == 3
    assert settings.series_typical_pct == 0.10
    assert settings.series_flat_pct == 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_series_defaults -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'series_min_documents'`)

- [ ] **Step 3: Add the settings**

In `src/library/config.py`, immediately after the `ask_history_turns` line:

```python
    # Document series + comparative queries (see docs/ask.md, "Document series").
    series_min_documents: int = 3  # min members before stats are reported
    series_typical_pct: float = 0.10  # half-width of the "typical" band vs median
    series_flat_pct: float = 0.05  # |first→last change| at/below which trend is flat
```

In `.env.example`, near the other `LIBRARY_ASK_*` entries, add:

```
# Document series + comparative queries (docs/ask.md)
LIBRARY_SERIES_MIN_DOCUMENTS=3
LIBRARY_SERIES_TYPICAL_PCT=0.10
LIBRARY_SERIES_FLAT_PCT=0.05
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_series_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/library/config.py .env.example tests/test_config.py
git commit -m "feat(series): add series_* settings

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `series.py` — types, distribution stats, cadence (pure helpers)

**Files:**
- Create: `src/library/series.py`
- Test: `tests/test_series.py`

**Interfaces:**
- Produces (importable from `library.series`):
  - Type aliases: `Cadence = Literal["monthly", "quarterly", "yearly", "irregular"]`, `Verdict = Literal["higher", "typical", "lower"]`, `TrendDirection = Literal["rising", "falling", "flat"]`.
  - `@dataclass(frozen=True, slots=True) Distribution(count: int, mean: Decimal, median: Decimal, stdev: Decimal, minimum: Decimal, maximum: Decimal)`.
  - `distribution(amounts: list[Decimal]) -> Distribution` — requires non-empty; `stdev` is `Decimal("0")` when `len < 2`. All money quantized to 2dp.
  - `classify_cadence(dates: list[date]) -> Cadence` — median consecutive gap → band; `< 2` dates ⇒ `"irregular"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_series.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_series.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'library.series'`)

- [ ] **Step 3: Write the module**

Create `src/library/series.py`:

```python
"""Recurring-document *series* detection and comparative statistics.

A series is the set of documents sharing one ``(sender_id, kind_id)`` — e.g.
the monthly energy bill from one provider. This module answers comparative
questions ("more than usual?", "vs last year?", "trending up?") over a series'
``amount_total``, on the fly (no materialised table). Pure statistics live in
module-level helpers; ``summarize_series`` orchestrates DB loading + bucketing.

Money is ``Decimal`` quantized to 2dp; percentages and z-scores are floats.
"""

from __future__ import annotations

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
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:], strict=False)]
    median_gap = statistics.median(gaps)
    for label, low, high in _CADENCE_BANDS:
        if low <= median_gap <= high:
            return label
    return "irregular"
```

Note: the `statistics.fmean(...) if False else statistics.mean(...)` is a lint trap — write just `statistics.mean(amounts)`:

```python
        mean=_money(statistics.mean(amounts)),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_series.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/library/series.py tests/test_series.py
uv run ruff check src/library/series.py tests/test_series.py
git add src/library/series.py tests/test_series.py
git commit -m "feat(series): distribution stats + cadence classification

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `series.py` — reference comparison, trend, year-over-year (pure helpers)

**Files:**
- Modify: `src/library/series.py`
- Test: `tests/test_series.py`

**Interfaces:**
- Consumes: `Distribution`, `Verdict`, `TrendDirection`, `Cadence` (Task 2).
- Produces:
  - `@dataclass(frozen=True, slots=True) ReferenceComparison(value: Decimal, delta: Decimal, vs_median_pct: float, z_score: float | None, verdict: Verdict)`.
  - `@dataclass(frozen=True, slots=True) Trend(direction: TrendDirection, change_pct: float)`.
  - `@dataclass(frozen=True, slots=True) YearOverYear(prior_value: Decimal, change_pct: float, document_id: int)`.
  - `compare_reference(value: Decimal, dist: Distribution, typical_pct: float) -> ReferenceComparison`.
  - `compute_trend(points: list[tuple[date, Decimal]], flat_pct: float) -> Trend | None` (None when <2 points).
  - `year_over_year(points: list[tuple[date, Decimal, int]], reference_date: date, cadence: Cadence) -> YearOverYear | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series.py`:

```python
from library.series import (  # noqa: E402  (grouped with the others at top in final form)
    ReferenceComparison,
    Trend,
    YearOverYear,
    compare_reference,
    compute_trend,
    year_over_year,
)


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
    pts = [(date(2025, 1, 1), Decimal("100")), (date(2025, 2, 1), Decimal("120")),
           (date(2025, 3, 1), Decimal("140"))]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "rising"
    assert round(trend.change_pct, 2) == 0.40


def test_compute_trend_flat() -> None:
    pts = [(date(2025, 1, 1), Decimal("100")), (date(2025, 2, 1), Decimal("101")),
           (date(2025, 3, 1), Decimal("102"))]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_series.py -v`
Expected: FAIL (`ImportError` for the new names)

- [ ] **Step 3: Implement the helpers**

Append to `src/library/series.py`:

```python
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


def compare_reference(value: Decimal, dist: Distribution, typical_pct: float) -> ReferenceComparison:
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
    change_pct = (last_amount - first_amount) / first_amount if first_amount != 0 else 0.0
    if abs(change_pct) <= flat_pct:
        return Trend(direction="flat", change_pct=change_pct)
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
    change_pct = float((ref_value - prior_value) / prior_value) if ref_value and prior_value != 0 else 0.0
    return YearOverYear(prior_value=_money(prior_value), change_pct=change_pct, document_id=doc_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_series.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/library/series.py tests/test_series.py
uv run ruff check src/library/series.py tests/test_series.py
git add src/library/series.py tests/test_series.py
git commit -m "feat(series): reference comparison, trend, year-over-year

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `series.py` — `summarize_series` orchestrator + serialisation

**Files:**
- Modify: `src/library/series.py`
- Test: `tests/test_series_db.py` (integration; seeds documents like `tests/test_structured_query.py`)

**Interfaces:**
- Consumes: all Task 2/3 helpers; `library.search.DocumentFilters` + `filter_conditions`; `library.models.{Document, Sender, Kind}`; `library.config.Settings`.
- Produces:
  - `@dataclass(frozen=True, slots=True) SeriesSummary(status, sender, kind, currency, other_currencies, cadence, count, distribution, reference, trend, year_over_year, document_ids, points)` where `status: Literal["ok", "insufficient"]`, `points: list[tuple[date, Decimal]]`.
  - `async summarize_series(session, *, filters, settings, reference="latest", reference_date=None, reference_currency=None) -> SeriesSummary` where `reference: Decimal | Literal["latest"] | None`.
  - `serialise_summary(summary: SeriesSummary, *, include_points: bool = False) -> dict[str, object]`.
  - `MAX_CITED_IDS: int = 25`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_series_db.py` (reuse the seed/session fixtures pattern from `tests/test_structured_query.py`):

```python
"""Integration tests for summarize_series over seeded documents."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import Document, DocumentSource, Kind, Sender
from library.search import DocumentFilters
from library.series import serialise_summary, summarize_series

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Document))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


async def _sender(session: AsyncSession, name: str) -> Sender:
    existing = (
        await session.execute(select(Sender).where(Sender.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


async def seed(
    session: AsyncSession, marker: str, *, sender_name: str, kind_slug: str,
    document_date: date, amount: str, currency: str = "EUR",
) -> int:
    sender = await _sender(session, sender_name)
    kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf", source=DocumentSource.UPLOAD,
        sender=sender, kind=kind, document_date=document_date,
        amount_total=Decimal(amount), currency=currency,
    )
    session.add(document)
    await session.commit()
    return document.id


def _settings() -> Settings:
    return Settings(series_min_documents=3, series_typical_pct=0.10, series_flat_pct=0.05)


async def test_summarize_ok_latest_reference(session: AsyncSession) -> None:
    await seed(session, "j1", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 1, 3), amount="100.00")
    await seed(session, "f1", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 2, 2), amount="100.00")
    await seed(session, "m1", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 3, 4), amount="130.00")

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    assert summary.status == "ok"
    assert summary.sender == "Vattenfall"
    assert summary.count == 3
    assert summary.reference is not None
    assert summary.reference.value == Decimal("130.00")
    assert summary.reference.verdict == "higher"
    assert summary.cadence == "monthly"
    assert summary.currency == "EUR"


async def test_summarize_insufficient(session: AsyncSession) -> None:
    await seed(session, "only", sender_name="Eneco", kind_slug="utility-bill",
               document_date=date(2025, 1, 1), amount="50.00")
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="eneco"),
        settings=_settings(),
    )
    assert summary.status == "insufficient"
    assert summary.count == 1


async def test_summarize_picks_dominant_currency(session: AsyncSession) -> None:
    for i, amt in enumerate(["100.00", "100.00", "100.00"]):
        await seed(session, f"eur{i}", sender_name="Acme", kind_slug="invoice",
                   document_date=date(2025, 1, i + 1), amount=amt, currency="EUR")
    await seed(session, "usd", sender_name="Acme", kind_slug="invoice",
               document_date=date(2025, 1, 9), amount="999.00", currency="USD")
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="invoice", sender_contains="acme"),
        settings=_settings(),
    )
    assert summary.status == "ok"
    assert summary.currency == "EUR"
    assert summary.other_currencies == ["USD"]
    assert summary.count == 3  # USD doc excluded from the EUR bucket


async def test_serialise_summary_shape(session: AsyncSession) -> None:
    await seed(session, "a", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 1, 3), amount="100.00")
    await seed(session, "b", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 2, 2), amount="100.00")
    await seed(session, "c", sender_name="Vattenfall", kind_slug="utility-bill",
               document_date=date(2025, 3, 4), amount="130.00")
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(), reference="latest",
    )
    body = serialise_summary(summary, include_points=True)
    assert body["status"] == "ok"
    assert body["median"] == "100.00"
    assert body["reference"]["verdict"] == "higher"
    assert isinstance(body["document_ids"], list)
    assert isinstance(body["points"], list)
    assert body["points"][0]["amount"] == "100.00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_series_db.py -v`
Expected: FAIL (`ImportError: cannot import name 'summarize_series'`)

- [ ] **Step 3: Implement the orchestrator + serialisation**

Append to `src/library/series.py` (add `from dataclasses import dataclass` already present; add imports for SQLAlchemy + models + filters + Settings at top):

```python
# --- top-of-file additions ---
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession
# from library.config import Settings
# from library.models import Document, Kind, Sender
# from library.search import DocumentFilters, filter_conditions

MAX_CITED_IDS: int = 25


@dataclass(frozen=True, slots=True)
class _Member:
    document_id: int
    sender: str | None
    kind: str | None
    document_date: date | None
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    status: Literal["ok", "insufficient"]
    sender: str | None
    kind: str | None
    currency: str | None
    other_currencies: list[str]
    cadence: Cadence
    count: int
    distribution: Distribution | None
    reference: ReferenceComparison | None
    trend: Trend | None
    year_over_year: YearOverYear | None
    document_ids: list[int]
    points: list[tuple[date, Decimal]]


async def _load_members(session: AsyncSession, filters: DocumentFilters) -> list[_Member]:
    """All non-deleted documents matching ``filters`` that have an amount."""
    statement = (
        select(
            Document.id, Sender.name, Kind.slug, Document.document_date,
            Document.amount_total, Document.currency, Document.sender_id, Document.kind_id,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(*filter_conditions(filters), Document.amount_total.isnot(None))
    )
    rows = (await session.execute(statement)).all()
    # Restrict to the single most-populous (sender_id, kind_id) group so a
    # loosely-filtered query (kind only) can't mix providers into one series.
    groups: dict[tuple[int | None, int | None], list[_Member]] = {}
    for did, sname, kslug, ddate, amount, currency, sid, kid in rows:
        groups.setdefault((sid, kid), []).append(
            _Member(did, sname, kslug, ddate, amount, currency)
        )
    if not groups:
        return []
    return max(groups.values(), key=len)


def _insufficient(members: list[_Member]) -> SeriesSummary:
    head = members[0] if members else None
    return SeriesSummary(
        status="insufficient", sender=head.sender if head else None,
        kind=head.kind if head else None, currency=None, other_currencies=[],
        cadence="irregular", count=len(members), distribution=None, reference=None,
        trend=None, year_over_year=None, document_ids=[m.document_id for m in members[:MAX_CITED_IDS]],
        points=[],
    )


async def summarize_series(
    session: AsyncSession, *, filters: DocumentFilters, settings: Settings,
    reference: Decimal | Literal["latest"] | None = "latest",
    reference_date: date | None = None, reference_currency: str | None = None,
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

    dated = sorted((m for m in bucket if m.document_date is not None), key=lambda m: m.document_date)
    points = [(m.document_date, m.amount) for m in dated]
    amounts = [m.amount for m in bucket]
    dist = distribution(amounts)
    cadence = classify_cadence([m.document_date for m in dated])
    trend = compute_trend(points, settings.series_flat_pct)

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

    comparison = compare_reference(ref_value, dist, settings.series_typical_pct) if ref_value is not None else None
    yoy = None
    if ref_date is not None:
        yoy_points = [(m.document_date, m.amount, m.document_id) for m in dated]
        if ref_value is not None and ref_date not in {p[0] for p in points}:
            yoy_points.append((ref_date, ref_value, -1))
        yoy = year_over_year(yoy_points, ref_date, cadence)

    head = bucket[0]
    return SeriesSummary(
        status="ok", sender=head.sender, kind=head.kind, currency=currency,
        other_currencies=other_currencies, cadence=cadence, count=len(bucket),
        distribution=dist, reference=comparison, trend=trend, year_over_year=yoy,
        document_ids=sorted(m.document_id for m in bucket)[:MAX_CITED_IDS], points=points,
    )


def _pct(fraction: float) -> str:
    return f"{fraction * 100:+.1f}%"


def serialise_summary(summary: SeriesSummary, *, include_points: bool = False) -> dict[str, object]:
    """A JSON-friendly dict (money→str, dates→ISO, fractions→'+6.4%')."""
    body: dict[str, object] = {
        "status": summary.status,
        "sender": summary.sender,
        "kind": summary.kind,
        "currency": summary.currency,
        "other_currencies": summary.other_currencies,
        "cadence": summary.cadence,
        "count": summary.count,
        "document_ids": summary.document_ids,
    }
    if summary.distribution is not None:
        d = summary.distribution
        body |= {
            "mean": str(d.mean), "median": str(d.median), "stdev": str(d.stdev),
            "min": str(d.minimum), "max": str(d.maximum),
        }
    if summary.reference is not None:
        r = summary.reference
        body["reference"] = {
            "value": str(r.value), "delta": str(r.delta),
            "vs_median_pct": _pct(r.vs_median_pct),
            "z_score": None if r.z_score is None else round(r.z_score, 2),
            "verdict": r.verdict,
        }
    if summary.trend is not None:
        body["trend"] = {"direction": summary.trend.direction, "change_pct": _pct(summary.trend.change_pct)}
    if summary.year_over_year is not None:
        y = summary.year_over_year
        body["year_over_year"] = {
            "prior_value": str(y.prior_value), "change_pct": _pct(y.change_pct),
            "document_id": y.document_id,
        }
    if include_points:
        body["points"] = [{"date": d.isoformat(), "amount": str(a)} for d, a in summary.points]
    return body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_series_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/library/series.py tests/test_series_db.py
uv run ruff check src/library/series.py tests/test_series_db.py
git add src/library/series.py tests/test_series_db.py
git commit -m "feat(series): summarize_series orchestrator + serialisation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Ask — `compare_to_series` tool

**Files:**
- Modify: `src/library/ask/engine.py` (TOOLS list ~71, system prompt ~32, dispatch ~215)
- Test: `tests/test_api_ask.py`

**Interfaces:**
- Consumes: `summarize_series`, `serialise_summary` from `library.series`; existing `_parse_date`, `DocumentFilters`, `cited` set, `settings`.
- Produces: a third tool `compare_to_series` in `TOOLS`; `_run_compare_to_series(session, settings, args, cited)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_ask.py` (follow the existing `_install_anthropic` + two-response pattern, e.g. the `query_documents` test ~235). Seed three utility bills, then:

```python
def test_ask_uses_compare_to_series(
    api_client: TestClient, api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a 3-bill series (reuse the test's existing seeding helper for documents
    # with sender/kind/date/amount — see the query_documents test in this file).
    doc_ids = _seed_utility_series(api_database_url)  # newest = highest amount
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="compare_to_series",
                        input={"kind": "utility-bill", "sender_contains": "vattenfall",
                               "reference": "latest"},
                        id="c1",
                    )
                ],
                usage=_Usage(120, 25),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Yes, higher than usual [#{doc_ids[-1]}].")],
                usage=_Usage(140, 18),
            ),
        ],
    )
    response = api_client.post("/api/ask", json={"question": "is my latest bill higher than usual?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_tools"] == ["compare_to_series"]
    assert any(c["document_id"] == doc_ids[-1] for c in body["citations"])
```

Add a small `_seed_utility_series(database_url) -> list[int]` near the file's other seeding helpers (modeled on `_seed_document_with_pages` / the existing ask-test seeding), inserting three `utility-bill` docs for sender "Vattenfall" with ascending dates and amounts `100/100/130`, returning ids oldest→newest.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_ask.py::test_ask_uses_compare_to_series -v`
Expected: FAIL (tool not offered / `used_tools` mismatch)

- [ ] **Step 3: Add the tool**

In `src/library/ask/engine.py`:

Import at top:
```python
from library.series import serialise_summary, summarize_series
```

Add a tool entry to `TOOLS` (after `query_documents`):
```python
    {
        "name": "compare_to_series",
        "description": (
            "Compare a recurring document (same sender + kind) to its usual "
            "values. Use for 'more/less than usual', 'compared to last year', "
            "'are my bills going up'. Identify the series via kind + sender. "
            "Returns distribution stats, a reference-vs-usual verdict, a trend, "
            "and a year-over-year comparison. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Kind slug, e.g. utility-bill."},
                "sender_contains": {"type": "string", "description": "Substring of sender name."},
                "date_from": {"type": "string", "description": "Inclusive ISO date lower bound."},
                "date_to": {"type": "string", "description": "Inclusive ISO date upper bound."},
                "reference": {
                    "type": "string",
                    "description": "'latest' (default) to compare the newest bill, or a number.",
                },
            },
            "required": [],
        },
    },
```

Add a system-prompt line in `ASK_SYSTEM_PROMPT_TEMPLATE` after the `query_documents` bullet:
```
- compare_to_series: compare a recurring bill to its usual values / last year /
  trend (e.g. "is this electricity bill higher than usual?").
```

Add the dispatch helper:
```python
async def _run_compare_to_series(
    session: AsyncSession, settings: Settings, args: dict[str, Any], cited: set[int]
) -> dict[str, Any]:
    filters = DocumentFilters(
        kind_slug=args.get("kind"),
        sender_contains=args.get("sender_contains"),
        date_from=_parse_date(args.get("date_from")),
        date_to=_parse_date(args.get("date_to")),
    )
    raw_reference = args.get("reference", "latest")
    reference: Decimal | str
    if raw_reference in (None, "latest", ""):
        reference = "latest"
    else:
        try:
            reference = Decimal(str(raw_reference))
        except (InvalidOperation, ValueError):
            reference = "latest"
    summary = await summarize_series(
        session, filters=filters, settings=settings, reference=reference
    )
    cited.update(summary.document_ids)
    return serialise_summary(summary)
```

Wire it into `_dispatch_tool`:
```python
    if name == "compare_to_series":
        return await _run_compare_to_series(session, settings, args, cited)
```

Add imports `from decimal import Decimal, InvalidOperation` at top.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_ask.py::test_ask_uses_compare_to_series -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/library/ask/engine.py tests/test_api_ask.py
uv run ruff check src/library/ask/engine.py tests/test_api_ask.py
git add src/library/ask/engine.py tests/test_api_ask.py
git commit -m "feat(ask): compare_to_series tool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Endpoint — `GET /api/documents/{id}/series`

**Files:**
- Modify: `src/library/schemas.py` (after `MarkdownResponse` ~388)
- Modify: `src/library/api/documents.py` (after the markdown endpoint ~329)
- Test: `tests/test_documents_api_series.py`

**Interfaces:**
- Consumes: `summarize_series`, `serialise_summary`, `_get_document_or_404`, `DocumentFilters`.
- Produces: `GET /api/documents/{id}/series` returning the `serialise_summary(..., include_points=True)` dict (FastAPI returns it as JSON; use `response_model=None` and return a plain dict, mirroring how the engine serialises — or a thin Pydantic model). Returns `404` for unknown/foreign/deleted; `200` with `status:"insufficient"` otherwise.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_documents_api_series.py` (model the seeding on `test_documents_api_markdown.py`'s sync `seed_*` wrapper, seeding documents with sender/kind/date/amount):

```python
"""Integration tests for GET /api/documents/{id}/series."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_series_endpoint_ok(api_client: TestClient, api_database_url: str) -> None:
    ids = seed_series(api_database_url, "Vattenfall", "utility-bill",
                      [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")])
    response = api_client.get(f"/api/documents/{ids[-1]}/series")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["reference"]["verdict"] == "higher"
    assert len(body["points"]) == 3


def test_series_endpoint_insufficient(api_client: TestClient, api_database_url: str) -> None:
    ids = seed_series(api_database_url, "Eneco", "utility-bill", [("2025-01-01", "50.00")])
    response = api_client.get(f"/api/documents/{ids[0]}/series")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient"


def test_series_endpoint_no_sender_or_kind(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_bare_document(api_database_url, "bare")  # no sender/kind
    response = api_client.get(f"/api/documents/{doc_id}/series")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient"


def test_series_endpoint_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/999999999/series").status_code == 404
```

Add sync seeding helpers `seed_series(database_url, sender, kind_slug, rows) -> list[int]` and `seed_bare_document(database_url, marker) -> int` at the top of the file, modeled on `test_documents_api_markdown.py` (`asyncio.run` wrapping an async seeder that upserts the Sender, looks up the Kind by slug, and inserts Documents).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_documents_api_series.py -v`
Expected: FAIL (404 on the route — not yet defined)

- [ ] **Step 3: Implement the endpoint**

In `src/library/schemas.py`, no strict model needed — but for OpenAPI clarity add nothing mandatory; the endpoint returns a `dict`. (Skip a Pydantic model to avoid over-constraining the optional-field shape.)

In `src/library/api/documents.py`, add after `get_document_markdown`:

```python
@router.get(
    "/documents/{document_id}/series",
    summary="Recurring-series stats + comparison for this document",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def get_document_series(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Summarise the (sender, kind) series this document belongs to and where
    this document sits within it. ``status:"insufficient"`` when the document
    has no sender/kind or too few siblings."""
    document = await _get_document_or_404(session, document_id)
    settings = get_settings()
    if document.sender_id is None or document.kind_id is None:
        return {"status": "insufficient", "count": 0, "document_ids": [document_id]}
    filters = DocumentFilters(
        sender_id=document.sender_id,
        kind_slug=document.kind.slug if document.kind else None,
    )
    summary = await summarize_series(
        session,
        filters=filters,
        settings=settings,
        reference=document.amount_total,
        reference_date=document.document_date,
        reference_currency=document.currency,
    )
    return serialise_summary(summary, include_points=True)
```

Add imports to `documents.py`:
```python
from library.config import get_settings
from library.series import serialise_summary, summarize_series
```
(`DocumentFilters` is already imported; confirm and reuse.)

Note: `summarize_series`'s `reference` accepts `Decimal | Literal["latest"] | None`; passing `document.amount_total` (a `Decimal | None`) is valid — `None` simply yields no `reference` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_documents_api_series.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/library/api/documents.py tests/test_documents_api_series.py
uv run ruff check src/library/api/documents.py tests/test_documents_api_series.py
git add src/library/api/documents.py tests/test_documents_api_series.py
git commit -m "feat(api): GET /documents/{id}/series

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend — deps, API client, `DocumentSeriesTrend.vue`

**Files:**
- Modify: `frontend/package.json` (deps)
- Modify: `frontend/src/api/documents.ts`
- Create: `frontend/src/components/DocumentSeriesTrend.vue`
- Test: `frontend/src/components/__tests__/DocumentSeriesTrend.spec.ts`

**Interfaces:**
- Consumes: `apiFetch` from `./client`.
- Produces: `DocumentSeries` type + `fetchDocumentSeries(id): Promise<DocumentSeries>`; a `<DocumentSeriesTrend :document-id="id"/>` component that self-fetches and hides on insufficient/404.

- [ ] **Step 1: Add dependencies**

```bash
cd frontend && npm install chart.js vue-chartjs
```
Confirm `chart.js` and `vue-chartjs` appear under `dependencies` in `frontend/package.json`.

- [ ] **Step 2: Write the API client + types (and a failing component test)**

Append to `frontend/src/api/documents.ts`:

```typescript
/** Body of GET /api/documents/{id}/series (optional blocks omitted when N/A). */
export interface DocumentSeries {
  status: 'ok' | 'insufficient'
  sender: string | null
  kind: string | null
  currency: string | null
  other_currencies: string[]
  cadence: 'monthly' | 'quarterly' | 'yearly' | 'irregular'
  count: number
  document_ids: number[]
  mean?: string
  median?: string
  stdev?: string
  min?: string
  max?: string
  reference?: {
    value: string
    delta: string
    vs_median_pct: string
    z_score: number | null
    verdict: 'higher' | 'typical' | 'lower'
  }
  trend?: { direction: 'rising' | 'falling' | 'flat'; change_pct: string }
  year_over_year?: { prior_value: string; change_pct: string; document_id: number }
  points?: { date: string; amount: string }[]
}

/** GET /api/documents/{id}/series — recurring-series stats + comparison. */
export function fetchDocumentSeries(id: number, signal?: AbortSignal): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/documents/${id}/series`, { signal })
}
```

Create `frontend/src/components/__tests__/DocumentSeriesTrend.spec.ts` (mock `vue-chartjs`'s `Line` to a stub and `fetchDocumentSeries`):

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({ Line: { name: 'Line', template: '<canvas data-testid="chart"/>' } }))
vi.mock('@/api/documents', () => ({ fetchDocumentSeries: vi.fn() }))

import { fetchDocumentSeries } from '@/api/documents'
import DocumentSeriesTrend from '../DocumentSeriesTrend.vue'

describe('DocumentSeriesTrend', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders chart + verdict when status ok', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'ok', sender: 'Vattenfall', kind: 'utility-bill', currency: 'EUR',
      other_currencies: [], cadence: 'monthly', count: 3, document_ids: [1, 2, 3],
      median: '100.00', reference: { value: '130.00', delta: '30.00', vs_median_pct: '+30.0%', z_score: null, verdict: 'higher' },
      trend: { direction: 'rising', change_pct: '+30.0%' },
      points: [{ date: '2025-01-03', amount: '100.00' }, { date: '2025-03-04', amount: '130.00' }],
    } as never)
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="chart"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('above usual')
  })

  it('renders nothing when status insufficient', async () => {
    vi.mocked(fetchDocumentSeries).mockResolvedValue({
      status: 'insufficient', count: 1, document_ids: [3],
    } as never)
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="series-trend"]').exists()).toBe(false)
  })

  it('renders nothing on fetch error (404)', async () => {
    vi.mocked(fetchDocumentSeries).mockRejectedValue(new Error('404'))
    const wrapper = mount(DocumentSeriesTrend, { props: { documentId: 3 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="series-trend"]').exists()).toBe(false)
  })
})
```

- [ ] **Step 3: Run the component test to verify it fails**

Run: `cd frontend && npm run test:unit -- DocumentSeriesTrend`
Expected: FAIL (component does not exist)

- [ ] **Step 4: Create the component**

Create `frontend/src/components/DocumentSeriesTrend.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Tooltip,
} from 'chart.js'
import { fetchDocumentSeries, type DocumentSeries } from '@/api/documents'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip)

const props = defineProps<{ documentId: number }>()
const series = ref<DocumentSeries | null>(null)

onMounted(async () => {
  try {
    const data = await fetchDocumentSeries(props.documentId)
    series.value = data.status === 'ok' ? data : null
  } catch {
    series.value = null
  }
})

const verdictText = computed<string>(() => {
  const s = series.value
  if (!s?.reference) return ''
  const pct = s.reference.vs_median_pct
  if (s.reference.verdict === 'typical') return 'about usual'
  return `${pct.replace('+', '').replace('-', '')} ${s.reference.verdict === 'higher' ? 'above' : 'below'} usual`
})

const trendText = computed<string>(() => (series.value?.trend ? `trend ${series.value.trend.direction}` : ''))

const chartData = computed(() => {
  const pts = series.value?.points ?? []
  return {
    labels: pts.map((p) => p.date),
    datasets: [
      {
        data: pts.map((p) => Number(p.amount)),
        borderColor: '#2563eb',
        pointBackgroundColor: pts.map((p, i) =>
          i === pts.length - 1 ? '#dc2626' : '#2563eb',
        ),
        tension: 0.2,
      },
    ],
  }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
}
</script>

<template>
  <section
    v-if="series"
    data-testid="series-trend"
    class="mt-4 rounded-lg border border-gray-200 p-4 dark:border-gray-700"
  >
    <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">
      {{ series.sender }} · {{ series.cadence }} series
    </h3>
    <p class="text-sm text-gray-600 dark:text-gray-400">
      <span v-if="verdictText">{{ verdictText }}</span>
      <span v-if="verdictText && trendText"> · </span>
      <span v-if="trendText">{{ trendText }}</span>
    </p>
    <div class="mt-3 h-40">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>
```

- [ ] **Step 5: Run the component test to verify it passes**

Run: `cd frontend && npm run test:unit -- DocumentSeriesTrend`
Expected: PASS (3 tests)

- [ ] **Step 6: Lint + commit**

```bash
cd frontend && npm run lint && npx vue-tsc --noEmit
cd .. && git add frontend/package.json frontend/package-lock.json frontend/src/api/documents.ts frontend/src/components/DocumentSeriesTrend.vue frontend/src/components/__tests__/DocumentSeriesTrend.spec.ts
git commit -m "feat(ui): DocumentSeriesTrend widget + series API client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend — mount the widget in `DocumentDetailView`

**Files:**
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/views/__tests__/DocumentDetailView.spec.ts`

**Interfaces:**
- Consumes: `DocumentSeriesTrend` component; the existing `doc` ref.

- [ ] **Step 1: Add a failing assertion**

In `frontend/src/views/__tests__/DocumentDetailView.spec.ts`, add a test that the detail view renders `<DocumentSeriesTrend>` with the document id once `doc` is loaded (stub the child component and assert its presence + `documentId` prop). Mirror the spec's existing mount/stub setup.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test:unit -- DocumentDetailView`
Expected: FAIL (component not mounted)

- [ ] **Step 3: Mount the component**

In `DocumentDetailView.vue`:
- Import: `import DocumentSeriesTrend from '@/components/DocumentSeriesTrend.vue'`
- In the template, near the markdown card (~907), add:
```vue
<DocumentSeriesTrend v-if="doc" :document-id="doc.id" />
```
The widget self-hides when the series is insufficient/unavailable, so no extra guard is needed beyond `v-if="doc"`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test:unit -- DocumentDetailView`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npm run lint && npx vue-tsc --noEmit
cd .. && git add frontend/src/views/DocumentDetailView.vue frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(ui): mount series trend on document detail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs + journal + full verification

**Files:**
- Modify: `docs/ask.md` (new "Document series" subsection + config table rows + tool in §1.2)
- Modify: `docs/api.md` (document `GET /api/documents/{id}/series`)
- Create: `journal/260622-document-series.md`

- [ ] **Step 1: Update `docs/ask.md`**

- In §1.2 add `compare_to_series` to the tool list (third tool: comparative stats over a series).
- Add a new subsection "1.8 Document series + comparative queries" describing: series = (sender, kind) auto-grouping, on-the-fly, the four framings (distribution / reference-vs-usual / YoY / trend), the typical band rule, and the detail-view trend widget.
- Add the three `LIBRARY_SERIES_*` rows to the config table (§1.3).

- [ ] **Step 2: Update `docs/api.md`**

Document `GET /api/documents/{id}/series`: purpose, the `status:"ok"|"insufficient"` shape, the optional blocks (distribution, reference, trend, year_over_year, points), 404 conditions. Keep it consistent with the markdown-endpoint section's style.

- [ ] **Step 3: Write the journal entry**

Create `journal/260622-document-series.md` capturing: the five brainstorming decisions, the `series.py` design (pure helpers + orchestrator), the typical-band rule, the distribution-includes-reference simplification, the dominant-currency bucketing, the Chart.js choice, and any deferred follow-ups.

- [ ] **Step 4: Full verification (evidence before claiming done)**

```bash
uv run pytest -q
uv run ruff format --check src tests
uv run ruff check src tests
cd frontend && npm run test:unit && npm run lint && npx vue-tsc --noEmit && cd ..
```
Expected: backend suite green (note the new test count vs the prior 418), frontend green (vs prior 258), all linters clean. Record the actual numbers.

- [ ] **Step 5: Commit**

```bash
git add docs/ask.md docs/api.md journal/260622-document-series.md
git commit -m "docs(series): document series + comparative queries + journal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## After all tasks

1. Run `superpowers:requesting-code-review` for a final whole-branch review on the most capable model.
2. Then `superpowers:finishing-a-development-branch` to merge `feat/document-series-comparative` into `main`.
3. Update memory `extraction-ask-roadmap.md`: mark sub-project #5 DONE — **roadmap complete** (all 5 delivered).
4. `main` stays local-only; **do not push** without asking (push → live ghcr `:latest` deploy).
