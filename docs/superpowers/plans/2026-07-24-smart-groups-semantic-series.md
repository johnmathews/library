# Smart Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a semantic ("Smart Group") mode to authored series — cross-sender, mixed-currency chart groups whose membership is learned from document embeddings, auto-populated, and pruned by the user.

**Architecture:** Extend the existing `authored_series` tables with a `mode` flag, a per-member `origin`, and a new `authored_series_exclusions` (negative-example) table. A new `semantic_membership.py` module scores a candidate document by nearest-positive-neighbour over member embeddings with a negative veto. Creation runs a staged backfill sweep (results become `pending` suggestions for bulk review); a background job auto-adds future documents; pruning writes a negative. The chart FX-converts mixed-currency members into the group's display currency.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async, `Mapped`/`mapped_column`) + Alembic + pgvector (`Vector`, bge-m3 1024-dim, HNSW cosine) + Procrastinate jobs (`@job_app.task`) + Anthropic Messages API; Vue 3 + TypeScript + Tailwind; pytest + Playwright.

## Global Constraints

- Python 3.13, type annotations on every signature; `uv` for deps; `pytest` + `coverage`.
- Ruff runs over the **whole repo including `migrations/`** in CI — run `ruff format` on new migration files before committing.
- `GET /api/documents` and any list payload **422 on `limit > 100`** — keep every list ≤ 100.
- Backend test DB is **session-scoped**; default list limit is 25 — scope list assertions by a unique tag/name so parallel fixtures don't pollute counts.
- Playwright runs on **mobile-webkit + tablet-webkit** (both < lg 1024px); specs share **one serial backend** — a fixture with a `document_date` pollutes the dashboard sort and breaks `.first()`-tile specs. Flag test fixtures datelessly where possible.
- No new `*_model` setting is introduced (we reuse `settings.extraction_model`), so **no** `MODEL_PRICING_USD_PER_MTOK` row is required.
- Current Alembic head is `0028`; the new migration is `0029` (`down_revision = "0028"`).
- Enum columns use the repo pattern: `enum.StrEnum` + `Enum(EnumCls, name=..., native_enum=False, length=..., values_callable=lambda obj: [m.value for m in obj])`.
- Before `make deploy`, confirm CI's `promote` job is green (`gh run watch` can exit 0 mid-run).

---

## File structure

**Create:**
- `migrations/versions/0029_smart_groups.py` — schema changes (Task 1).
- `src/library/semantic_membership.py` — document-vector helper + scorer + group-evaluation engine (Tasks 3–4, 6, 7).
- `tests/test_semantic_membership.py` — scorer + engine unit tests.
- `tests/test_smart_groups_api.py` — create/backfill/accept/prune API tests.
- `docs/smart-groups.md` — feature doc (Task 11).
- `journal/260724-smart-groups.md` — journal entry (Task 11).

**Modify:**
- `src/library/models.py` — `SeriesMode`/`MemberOrigin` enums, `AuthoredSeries.mode`, `AuthoredSeriesMember.origin`, `AuthoredSeriesSuggestion.score`, new `AuthoredSeriesExclusion` (Task 1).
- `src/library/config.py` — `semantic_group_*` settings (Task 2).
- `src/library/series.py` — FX-aware `_load_authored_members`; expose member `origin` + `mode` in the authored summary (Tasks 5, 8).
- `src/library/api/charts.py` — create with `mode`/`seed_document_ids` + staged backfill; accept sets `origin`; dismiss + member-remove write exclusions (Tasks 6, 8).
- `src/library/jobs.py` — `evaluate_semantic_groups` task + INDEXED queueing (Task 7).
- `src/library/series_insight.py` (or a small helper) — group blurb generation reuse (Task 9).
- `frontend/src/views/ChartsView.vue`, `frontend/src/components/SeriesChartTile.vue`, `frontend/src/api/documents.ts` — Smart Group create toggle, staged-review modal, auto-added affordance (Task 10).
- `docs/roadmap.md` — mark the item shipped (Task 11).

**Separate PR (do last, own branch):**
- `src/library/extraction/apply.py` — `upsert_sender` whitespace normalization (Task 12).

---

### Task 1: Schema — enums, columns, exclusions table

**Files:**
- Modify: `src/library/models.py`
- Create: `migrations/versions/0029_smart_groups.py`
- Test: `tests/test_smart_groups_api.py` (schema smoke via a create call in Task 6; here we verify the migration applies)

**Interfaces:**
- Produces: `SeriesMode` (`MANUAL`/`SEMANTIC`), `MemberOrigin` (`MANUAL`/`AUTO`/`ACCEPTED_SUGGESTION`); `AuthoredSeries.mode`, `AuthoredSeriesMember.origin`, `AuthoredSeriesSuggestion.score`; `AuthoredSeriesExclusion(id, authored_series_id, document_id, created_at)` with unique `(authored_series_id, document_id)`.

- [ ] **Step 1: Add the enums to `models.py`** (next to `SuggestionState`, ~line 128)

```python
class SeriesMode(enum.StrEnum):
    """Whether an authored series is hand-curated or membership-learned (Smart Group)."""

    MANUAL = "manual"
    SEMANTIC = "semantic"


class MemberOrigin(enum.StrEnum):
    """How a document became a member of an authored series.

    ``manual`` — added by hand; ``accepted_suggestion`` — promoted from a staged
    backfill suggestion; ``auto`` — silently added by the semantic auto-add job
    (surfaced with the "added automatically" affordance so the user can prune it).
    """

    MANUAL = "manual"
    ACCEPTED_SUGGESTION = "accepted_suggestion"
    AUTO = "auto"
```

- [ ] **Step 2: Add `mode` to `AuthoredSeries`** (after the `currency` column)

```python
    mode: Mapped[SeriesMode] = mapped_column(
        Enum(
            SeriesMode,
            name="series_mode",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=SeriesMode.MANUAL,
        server_default=SeriesMode.MANUAL.value,
    )
```

- [ ] **Step 3: Add `origin` to `AuthoredSeriesMember`** (after `document_id`) and add the exclusions relationship + `score` to the suggestion

```python
    origin: Mapped[MemberOrigin] = mapped_column(
        Enum(
            MemberOrigin,
            name="member_origin",
            native_enum=False,
            length=24,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=MemberOrigin.MANUAL,
        server_default=MemberOrigin.MANUAL.value,
    )
```

On `AuthoredSeriesSuggestion`, add after `signature_currency`:

```python
    score: Mapped[float | None] = mapped_column(Float)
```

On `AuthoredSeries`, add an exclusions relationship next to `members`:

```python
    exclusions: Mapped[list["AuthoredSeriesExclusion"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", lazy="selectin"
    )
```

- [ ] **Step 4: Add the `AuthoredSeriesExclusion` model** (after `AuthoredSeriesSuggestion`)

```python
class AuthoredSeriesExclusion(Base):
    """A document the user pruned from a semantic authored series — a negative example.

    Written when a member is removed (or a backfill suggestion dismissed). The
    membership scorer treats these as vetoes so the document is neither re-added
    by the auto-add job nor re-proposed by a later sweep. Re-adding the document
    as a member clears its exclusion. One row per ``(series, document)``.
    """

    __tablename__ = "authored_series_exclusions"

    id: Mapped[int] = mapped_column(primary_key=True)
    authored_series_id: Mapped[int] = mapped_column(
        ForeignKey("authored_series.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    series: Mapped[AuthoredSeries] = relationship(back_populates="exclusions")

    __table_args__ = (
        UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_exclusions_series_document",
        ),
    )
```

- [ ] **Step 5: Write the migration** `migrations/versions/0029_smart_groups.py`

```python
"""smart groups: semantic authored series

Adds semantic-mode membership learning to authored series:
- authored_series.mode (manual | semantic)
- authored_series_members.origin (manual | accepted_suggestion | auto)
- authored_series_suggestions.score (float, similarity of a backfill match)
- authored_series_exclusions (negative examples written on prune/dismiss)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "authored_series",
        sa.Column(
            "mode",
            sa.Enum("manual", "semantic", name="series_mode", native_enum=False, length=16),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "authored_series_members",
        sa.Column(
            "origin",
            sa.Enum(
                "manual",
                "accepted_suggestion",
                "auto",
                name="member_origin",
                native_enum=False,
                length=24,
            ),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "authored_series_suggestions",
        sa.Column("score", sa.Float(), nullable=True),
    )
    op.create_table(
        "authored_series_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_exclusions_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_exclusions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_exclusions")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_exclusions_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_authored_series_id"),
        "authored_series_exclusions",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_document_id"),
        "authored_series_exclusions",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_authored_series_exclusions_document_id"),
        table_name="authored_series_exclusions",
    )
    op.drop_index(
        op.f("ix_authored_series_exclusions_authored_series_id"),
        table_name="authored_series_exclusions",
    )
    op.drop_table("authored_series_exclusions")
    op.drop_column("authored_series_suggestions", "score")
    op.drop_column("authored_series_members", "origin")
    op.drop_column("authored_series", "mode")
```

- [ ] **Step 6: Format + apply the migration**

Run: `ruff format migrations/versions/0029_smart_groups.py && ruff check migrations/versions/0029_smart_groups.py`
Run: `uv run alembic upgrade head`
Expected: upgrade runs clean; `uv run alembic current` shows `0029`.

- [ ] **Step 7: Commit**

```bash
git add src/library/models.py migrations/versions/0029_smart_groups.py
git commit -m "feat(charts): schema for Smart Groups (mode, member origin, exclusions)"
```

---

### Task 2: Settings for the scorer

**Files:**
- Modify: `src/library/config.py` (series block, ~lines 120-129)

**Interfaces:**
- Produces: `settings.semantic_group_enabled: bool`, `settings.semantic_group_min_similarity: float`, `settings.semantic_group_neg_margin: float`.

- [ ] **Step 1: Add the settings** (in the `Settings` class, just below `series_suggestion_limit`)

```python
    # Smart Groups (semantic authored series). Membership is learned from bge-m3
    # embeddings: a document belongs when its nearest member (positive) is within
    # `min_similarity` cosine AND closer than any pruned document (negative) by
    # `neg_margin`. See docs/smart-groups.md.
    semantic_group_enabled: bool = True
    semantic_group_min_similarity: float = 0.55  # tau: min cosine to nearest positive
    semantic_group_neg_margin: float = 0.02  # sim_pos must beat sim_neg by this margin
```

- [ ] **Step 2: Verify settings load**

Run: `uv run python -c "from library.config import get_settings; s=get_settings(); print(s.semantic_group_min_similarity, s.semantic_group_neg_margin, s.semantic_group_enabled)"`
Expected: `0.55 0.02 True`

- [ ] **Step 3: Commit**

```bash
git add src/library/config.py
git commit -m "feat(charts): semantic_group_* settings"
```

---

### Task 3: Document-vector helper

**Files:**
- Create: `src/library/semantic_membership.py`
- Test: `tests/test_semantic_membership.py`

**Interfaces:**
- Produces: `async def document_vectors(session: AsyncSession, document_ids: Sequence[int]) -> dict[int, list[float]]` — L2-normalized mean-pooled chunk embedding per document that has chunks (documents with no chunks are absent from the dict).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_semantic_membership.py
import pytest

from library.models import DocumentChunk
from library.semantic_membership import document_vectors


@pytest.mark.asyncio
async def test_document_vectors_mean_pools_and_normalizes(session, make_document):
    doc = await make_document(title="ev-charge-fastned")
    # Two chunks pointing along +x and +y; mean is (0.5, 0.5, 0, ...) -> normalized.
    dim = 1024
    vx = [1.0] + [0.0] * (dim - 1)
    vy = [0.0, 1.0] + [0.0] * (dim - 2)
    session.add(DocumentChunk(document_id=doc.id, chunk_index=1, text="a", embedding=vx))
    session.add(DocumentChunk(document_id=doc.id, chunk_index=2, text="b", embedding=vy))
    await session.commit()

    vectors = await document_vectors(session, [doc.id])
    v = vectors[doc.id]
    norm = sum(c * c for c in v) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)
    assert v[0] == pytest.approx(v[1], abs=1e-6)  # symmetric mean of +x and +y
    assert v[0] == pytest.approx(0.70710678, abs=1e-6)
```

(If no `make_document`/`session` fixtures exist, mirror the fixtures already used in `tests/test_series*.py` — check there first and reuse them.)

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/test_semantic_membership.py::test_document_vectors_mean_pools_and_normalizes -v`
Expected: FAIL — `ModuleNotFoundError: library.semantic_membership`.

- [ ] **Step 3: Implement `document_vectors`**

```python
# src/library/semantic_membership.py
"""Semantic membership for Smart Groups (semantic authored series).

Membership is learned from bge-m3 chunk embeddings. A document is represented by
the L2-normalized mean of its chunk embeddings; it *belongs* to a group when its
nearest member (positive) is within a cosine threshold and closer than any pruned
document (negative). Every decision here is mechanical — the LLM never decides
membership (see docs/smart-groups.md and the odd-one-out precedent in series.py).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import DocumentChunk


async def document_vectors(
    session: AsyncSession, document_ids: Sequence[int]
) -> dict[int, list[float]]:
    """L2-normalized mean chunk-embedding per document (docs without chunks omitted)."""
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentChunk.document_id, DocumentChunk.embedding).where(
                DocumentChunk.document_id.in_(list(document_ids))
            )
        )
    ).all()
    sums: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    for document_id, embedding in rows:
        vec = list(embedding)
        acc = sums.get(document_id)
        if acc is None:
            sums[document_id] = vec
            counts[document_id] = 1
        else:
            for i, value in enumerate(vec):
                acc[i] += value
            counts[document_id] += 1
    result: dict[int, list[float]] = {}
    for document_id, acc in sums.items():
        count = counts[document_id]
        mean = [value / count for value in acc]
        norm = math.sqrt(sum(value * value for value in mean))
        result[document_id] = [value / norm for value in mean] if norm > 0 else mean
    return result
```

- [ ] **Step 4: Run it, verify it passes**

Run: `uv run pytest tests/test_semantic_membership.py::test_document_vectors_mean_pools_and_normalizes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/semantic_membership.py tests/test_semantic_membership.py
git commit -m "feat(charts): document-vector helper for semantic membership"
```

---

### Task 4: The scorer

**Files:**
- Modify: `src/library/semantic_membership.py`
- Test: `tests/test_semantic_membership.py`

**Interfaces:**
- Produces: `MembershipScore(sim_pos: float, sim_neg: float, belongs: bool)` and pure `score_vector(candidate, positives, negatives, *, tau: float, margin: float) -> MembershipScore`.

- [ ] **Step 1: Write failing tests** (use graded, not orthogonal, vectors — orthogonal one-hots are equidistant and give arbitrary ordering; see the equidistant-vectors gotcha)

```python
from library.semantic_membership import MembershipScore, score_vector


def _graded(a: float, b: float) -> list[float]:
    # A 2-D direction embedded in a longer vector; graded so distances are distinct.
    return [a, b] + [0.0] * 1022


def test_belongs_when_near_a_positive_and_no_negatives():
    cand = _graded(0.99, 0.14)
    positives = [_graded(1.0, 0.0), _graded(0.0, 1.0)]
    result = score_vector(cand, positives, [], tau=0.55, margin=0.02)
    assert result.belongs is True
    assert result.sim_pos > 0.9
    assert result.sim_neg == 0.0


def test_rejected_when_below_threshold():
    cand = _graded(0.4, 0.917)  # ~66° from +x, ~24° from +y... still, force distance
    positives = [_graded(1.0, 0.0)]
    result = score_vector(cand, positives, [], tau=0.9, margin=0.02)
    assert result.belongs is False


def test_negative_veto():
    # Candidate is close to a positive but even closer to a pruned negative.
    cand = _graded(0.8, 0.6)
    positives = [_graded(0.7, 0.714)]
    negatives = [_graded(0.8, 0.6)]  # identical to candidate -> sim_neg == 1.0
    result = score_vector(cand, positives, negatives, tau=0.55, margin=0.02)
    assert result.sim_neg == pytest.approx(1.0, abs=1e-6)
    assert result.belongs is False


def test_membership_score_is_frozen_dataclass():
    s = MembershipScore(sim_pos=0.9, sim_neg=0.1, belongs=True)
    with pytest.raises(Exception):
        s.sim_pos = 0.0  # type: ignore[misc]
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_semantic_membership.py -k "belongs or veto or threshold or frozen" -v`
Expected: FAIL — `score_vector`/`MembershipScore` undefined.

- [ ] **Step 3: Implement the scorer** (append to `semantic_membership.py`)

```python
@dataclass(frozen=True, slots=True)
class MembershipScore:
    sim_pos: float  # cosine to nearest positive
    sim_neg: float  # cosine to nearest negative (0.0 if none)
    belongs: bool


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def score_vector(
    candidate: Sequence[float],
    positives: Sequence[Sequence[float]],
    negatives: Sequence[Sequence[float]],
    *,
    tau: float,
    margin: float,
) -> MembershipScore:
    """Nearest-positive-neighbour membership with a negative veto.

    Belongs iff the candidate is within ``tau`` cosine of some positive AND that
    similarity beats its nearest negative by more than ``margin``. Works with a
    handful of positives and zero negatives (cold start): the ``tau`` gate alone
    admits, and ``max`` over positives lets diverse sub-clusters each count.
    """
    if not positives:
        return MembershipScore(0.0, 0.0, False)
    sim_pos = max(_cosine(candidate, p) for p in positives)
    sim_neg = max((_cosine(candidate, n) for n in negatives), default=0.0)
    belongs = sim_pos >= tau and sim_pos > sim_neg + margin
    return MembershipScore(sim_pos=sim_pos, sim_neg=sim_neg, belongs=belongs)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_semantic_membership.py -k "belongs or veto or threshold or frozen" -v`
Expected: PASS (adjust the exact vector components in `test_rejected_when_below_threshold` if needed so `sim_pos < 0.9`).

- [ ] **Step 5: Commit**

```bash
git add src/library/semantic_membership.py tests/test_semantic_membership.py
git commit -m "feat(charts): nearest-positive-neighbour scorer with negative veto"
```

---

### Task 5: FX-aware authored member loading (mixed currency)

**Files:**
- Modify: `src/library/series.py` — `_load_authored_members` (~:688) and its two call sites (~:765, ~:808); `charts.py:459` call site
- Test: `tests/test_series*.py` (add to the existing authored-series test module)

**Interfaces:**
- Produces: `async def _load_authored_members(session, authored_series_id: int, target_currency: str | None) -> list[_Member]` — non-matching currencies FX-converted into `target_currency`; documents with no resolvable rate are logged and dropped (mirrors `_load_pinned_members`).

- [ ] **Step 1: Write the failing test** — a group in EUR with a USD member converts

```python
@pytest.mark.asyncio
async def test_authored_members_fx_converted_to_group_currency(session, make_document, seed_fx):
    await seed_fx("USD", "EUR", rate=0.90)  # mirror existing FX seeding helper
    group = AuthoredSeries(name="ev-mix-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    usd_doc = await make_document(title="ev-mix-uniqtag-usd", amount_total=Decimal("10.00"), currency="USD")
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=usd_doc.id))
    await session.commit()

    members = await _load_authored_members(session, group.id, "EUR")
    assert len(members) == 1
    assert members[0].currency == "EUR"
    assert members[0].amount == Decimal("9.00")
```

(Reuse the existing FX-seeding fixture from `tests/` — grep for `seed_fx`/`convert_amount` tests; the FX design lives in `docs/superpowers/specs/2026-07-02-fx-seeding-and-ui-polish-design.md`.)

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/ -k authored_members_fx -v`
Expected: FAIL — `_load_authored_members()` takes 2 positional args / no conversion.

- [ ] **Step 3: Implement** — replace the list-comprehension return with the `convert_amount` loop from `_load_pinned_members`

```python
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
    for did, sname, kslug, ddate, amount, currency, sid, kid, title in rows:
        converted = await convert_amount(session, amount, currency, target_currency, ddate)
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
            _Member(did, sname, kslug, ddate, _money(converted), target_currency, sid, kid, title)
        )
    return members
```

- [ ] **Step 4: Update the three call sites** to pass the group currency

At `series.py:765` and `:808`, change `await _load_authored_members(session, authored_series_id)` to pass the loaded `AuthoredSeries.currency` (both callers already have the `AuthoredSeries` row in scope — pass `series.currency`). At `charts.py:459` (`_authored_signature_extras`), thread the same currency through. Grep to confirm no other callers: `grep -rn "_load_authored_members" src/`.

- [ ] **Step 5: Run the authored-series test module**

Run: `uv run pytest tests/ -k "authored" -v`
Expected: PASS (existing single-currency authored tests still pass — a same-currency member converts 1:1).

- [ ] **Step 6: Commit**

```bash
git add src/library/series.py src/library/api/charts.py tests/
git commit -m "feat(charts): FX-convert authored-series members into the group currency"
```

---

### Task 6: Membership engine + create-with-backfill API

**Files:**
- Modify: `src/library/semantic_membership.py` (engine), `src/library/api/charts.py` (create endpoint)
- Test: `tests/test_semantic_membership.py`, `tests/test_smart_groups_api.py`

**Interfaces:**
- Consumes: `document_vectors`, `score_vector`, `settings.semantic_group_*` (Tasks 2–4).
- Produces:
  - `async def evaluate_group(session, settings, group_id: int, candidate_ids: Sequence[int]) -> list[tuple[int, MembershipScore]]` — scores each candidate against the group's members (positives) and exclusions (negatives); returns only those that `belong`, sorted by `sim_pos` desc.
  - `async def sweep_backfill(session, settings, group_id: int, anchor_ids: Sequence[int]) -> int` — over all eligible library docs, writes `pending` suggestions (with `score`) for matches; returns the count. `anchor_ids` seed the positive set when the group has no/few members yet.
  - `POST /api/charts/authored` accepts `mode` and `seed_document_ids`; for `semantic` it seeds members (`origin=manual`), runs `sweep_backfill`, and returns the authored body plus `backfill: [{document_id, title, score}]` (≤100).

- [ ] **Step 1: Write the failing engine test**

```python
@pytest.mark.asyncio
async def test_evaluate_group_returns_only_belonging_docs(session, settings, make_document, add_chunk):
    group = AuthoredSeries(name="ev-eval-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-eval-member")
    add_chunk(member, [0.9, 0.1])           # helper: writes a DocumentChunk with a graded vec
    near = await make_document(title="ev-eval-near")
    add_chunk(near, [0.88, 0.12])
    far = await make_document(title="ev-eval-far")
    add_chunk(far, [0.0, 1.0])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    await session.commit()

    hits = await evaluate_group(session, settings, group.id, [near.id, far.id])
    ids = [doc_id for doc_id, _ in hits]
    assert near.id in ids
    assert far.id not in ids
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_semantic_membership.py -k evaluate_group -v`
Expected: FAIL — `evaluate_group` undefined.

- [ ] **Step 3: Implement the engine** (append to `semantic_membership.py`)

```python
from library.config import Settings
from library.models import (
    AuthoredSeries,
    AuthoredSeriesExclusion,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    SuggestionState,
)


async def _member_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = await session.execute(
        select(AuthoredSeriesMember.document_id).where(
            AuthoredSeriesMember.authored_series_id == group_id
        )
    )
    return [r[0] for r in rows]


async def _exclusion_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = await session.execute(
        select(AuthoredSeriesExclusion.document_id).where(
            AuthoredSeriesExclusion.authored_series_id == group_id
        )
    )
    return [r[0] for r in rows]


async def evaluate_group(
    session: AsyncSession,
    settings: Settings,
    group_id: int,
    candidate_ids: Sequence[int],
    *,
    extra_positive_ids: Sequence[int] = (),
) -> list[tuple[int, MembershipScore]]:
    """Score candidates against a group's members (+ optional anchors) and exclusions."""
    if not candidate_ids:
        return []
    positive_ids = list(dict.fromkeys([*await _member_ids(session, group_id), *extra_positive_ids]))
    negative_ids = await _exclusion_ids(session, group_id)
    needed = list(dict.fromkeys([*positive_ids, *negative_ids, *candidate_ids]))
    vectors = await document_vectors(session, needed)
    positives = [vectors[i] for i in positive_ids if i in vectors]
    negatives = [vectors[i] for i in negative_ids if i in vectors]
    if not positives:
        return []
    hits: list[tuple[int, MembershipScore]] = []
    for candidate_id in candidate_ids:
        vec = vectors.get(candidate_id)
        if vec is None:
            continue
        score = score_vector(
            vec,
            positives,
            negatives,
            tau=settings.semantic_group_min_similarity,
            margin=settings.semantic_group_neg_margin,
        )
        if score.belongs:
            hits.append((candidate_id, score))
    hits.sort(key=lambda pair: pair[1].sim_pos, reverse=True)
    return hits


async def _eligible_candidate_ids(session: AsyncSession, group_id: int) -> list[int]:
    """Non-deleted, amount-bearing docs not already a member or exclusion of the group."""
    member_sub = select(AuthoredSeriesMember.document_id).where(
        AuthoredSeriesMember.authored_series_id == group_id
    )
    excl_sub = select(AuthoredSeriesExclusion.document_id).where(
        AuthoredSeriesExclusion.authored_series_id == group_id
    )
    rows = await session.execute(
        select(Document.id).where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.id.notin_(member_sub),
            Document.id.notin_(excl_sub),
        )
    )
    return [r[0] for r in rows]


async def sweep_backfill(
    session: AsyncSession,
    settings: Settings,
    group_id: int,
    anchor_ids: Sequence[int] = (),
) -> list[tuple[int, MembershipScore]]:
    """Score the whole library and write pending suggestions for matches. Returns them."""
    candidate_ids = await _eligible_candidate_ids(session, group_id)
    hits = await evaluate_group(
        session, settings, group_id, candidate_ids, extra_positive_ids=anchor_ids
    )
    hits = hits[: settings.series_suggestion_limit]
    for document_id, score in hits:
        session.add(
            AuthoredSeriesSuggestion(
                authored_series_id=group_id,
                document_id=document_id,
                state=SuggestionState.PENDING,
                score=score.sim_pos,
            )
        )
    await session.commit()
    return hits
```

- [ ] **Step 4: Run engine test, verify pass**

Run: `uv run pytest tests/test_semantic_membership.py -k evaluate_group -v`
Expected: PASS.

- [ ] **Step 5: Write the failing API test**

```python
# tests/test_smart_groups_api.py
@pytest.mark.asyncio
async def test_create_semantic_group_stages_backfill(client, make_document, add_chunk):
    seed = await make_document(title="acc-seed-uniqtag")
    add_chunk(seed, [0.9, 0.1])
    match = await make_document(title="acc-match-uniqtag", amount_total="120.00", currency="EUR")
    add_chunk(match, [0.88, 0.12])
    noise = await make_document(title="acc-noise-uniqtag", amount_total="5.00", currency="EUR")
    add_chunk(noise, [0.0, 1.0])

    resp = await client.post(
        "/api/charts/authored",
        json={
            "name": "my accountant uniqtag",
            "currency": "EUR",
            "mode": "semantic",
            "seed_document_ids": [seed.id],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    backfill_ids = {row["document_id"] for row in body["backfill"]}
    assert match.id in backfill_ids
    assert noise.id not in backfill_ids
```

- [ ] **Step 6: Run, verify fail**

Run: `uv run pytest tests/test_smart_groups_api.py -k stages_backfill -v`
Expected: FAIL — `mode` rejected / no `backfill` key.

- [ ] **Step 7: Extend the create endpoint** in `api/charts.py`

Add to `AuthoredSeriesCreate`:

```python
    mode: SeriesMode = SeriesMode.MANUAL
    # Semantic mode only: documents that anchor the first backfill sweep.
    seed_document_ids: list[int] = Field(default_factory=list)
```

(Import `SeriesMode`, `MemberOrigin` from `library.models`; import `sweep_backfill` from `library.semantic_membership`; import `embed_query` from `library.embedding` and `semantic_search` from `library.search`.)

Replace the body of `create_authored_series` to branch on mode:

```python
    authored = AuthoredSeries(
        name=payload.name.strip(),
        description=payload.description,
        currency=payload.currency,
        owner_id=user.id,
        mode=payload.mode,
    )
    session.add(authored)
    await session.flush()

    seed_ids = await _existing_document_ids(session, payload.seed_document_ids or payload.document_ids)
    for document_id in seed_ids:
        session.add(
            AuthoredSeriesMember(
                authored_series_id=authored.id,
                document_id=document_id,
                origin=MemberOrigin.MANUAL,
            )
        )
    await session.commit()

    body = await _authored_body(session, settings, authored.id)
    if payload.mode is SeriesMode.SEMANTIC and settings.semantic_group_enabled:
        anchor_ids = await _name_anchor_ids(session, settings, payload.name)
        hits = await sweep_backfill(session, settings, authored.id, anchor_ids=anchor_ids)
        body["backfill"] = [
            {"document_id": doc_id, "score": round(score.sim_pos, 4)} for doc_id, score in hits
        ]
    return body
```

Add the name→anchor helper (uses semantic search to widen seeds from the group name):

```python
async def _name_anchor_ids(session: AsyncSession, settings: Settings, name: str) -> list[int]:
    """Turn a Smart Group's name into a few anchor documents via semantic search.

    Best-effort: embedding failures (feature disabled / no service) return [] so
    a group with hand-picked seeds still sweeps on those alone.
    """
    try:
        embedding = await embed_query(name, settings=settings)
    except Exception:
        return []
    hits = await semantic_search(
        session, query=name, query_embedding=embedding, top_k=settings.series_suggestion_limit
    )
    return [hit.document_id for hit in hits]
```

(`SemanticHit` exposes `document_id` — confirm the attribute name in `search.py`; adjust if it is `.doc_id`.)

- [ ] **Step 8: Run the API test + full membership module**

Run: `uv run pytest tests/test_smart_groups_api.py tests/test_semantic_membership.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/library/semantic_membership.py src/library/api/charts.py tests/test_semantic_membership.py tests/test_smart_groups_api.py
git commit -m "feat(charts): semantic membership engine + create-with-staged-backfill"
```

---

### Task 7: Forward auto-add job

**Files:**
- Modify: `src/library/semantic_membership.py` (add `auto_add_document`), `src/library/jobs.py`
- Test: `tests/test_semantic_membership.py`

**Interfaces:**
- Consumes: `evaluate_group`, `MemberOrigin`.
- Produces: `async def auto_add_document(session, settings, document_id: int) -> list[int]` — for every semantic group the doc belongs to (and is not already member/excluded of), inserts a member with `origin=AUTO`; returns the group ids it joined. New job `library.jobs.evaluate_semantic_groups`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_auto_add_joins_matching_group_as_auto(session, settings, make_document, add_chunk):
    group = AuthoredSeries(name="ev-auto-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-auto-member")
    add_chunk(member, [0.9, 0.1])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    await session.commit()

    incoming = await make_document(title="ev-auto-incoming", amount_total="30.00", currency="EUR")
    add_chunk(incoming, [0.89, 0.11])
    await session.commit()

    joined = await auto_add_document(session, settings, incoming.id)
    assert group.id in joined
    row = (await session.execute(
        select(AuthoredSeriesMember).where(
            AuthoredSeriesMember.authored_series_id == group.id,
            AuthoredSeriesMember.document_id == incoming.id,
        )
    )).scalar_one()
    assert row.origin == MemberOrigin.AUTO
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_semantic_membership.py -k auto_add -v`
Expected: FAIL — `auto_add_document` undefined.

- [ ] **Step 3: Implement `auto_add_document`** (append to `semantic_membership.py`)

```python
async def auto_add_document(
    session: AsyncSession, settings: Settings, document_id: int
) -> list[int]:
    """Silently add a newly-indexed document to every semantic group it belongs to."""
    if not settings.semantic_group_enabled:
        return []
    group_ids = [
        r[0]
        for r in await session.execute(
            select(AuthoredSeries.id).where(AuthoredSeries.mode == SeriesMode.SEMANTIC)
        )
    ]
    joined: list[int] = []
    for group_id in group_ids:
        # notin the group already? evaluate_group's candidate list handles member/excl skip.
        existing = set(await _member_ids(session, group_id)) | set(
            await _exclusion_ids(session, group_id)
        )
        if document_id in existing:
            continue
        hits = await evaluate_group(session, settings, group_id, [document_id])
        if hits:
            session.add(
                AuthoredSeriesMember(
                    authored_series_id=group_id,
                    document_id=document_id,
                    origin=MemberOrigin.AUTO,
                )
            )
            joined.append(group_id)
    if joined:
        await session.commit()
    return joined
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_semantic_membership.py -k auto_add -v`
Expected: PASS.

- [ ] **Step 5: Add the job + queue it** in `jobs.py`

Add the task (next to `evaluate_series_autocontinue`, ~line 587):

```python
@job_app.task(name="library.jobs.evaluate_semantic_groups")
async def evaluate_semantic_groups(document_id: int) -> None:
    """Background task: auto-add an indexed document to any Smart Group it matches.

    Deferred when a document reaches ``indexed``. Silent membership by design;
    the tile's "added automatically" affordance keeps it prunable. Best-effort.
    """
    async with get_sessionmaker()() as session:
        await auto_add_document(session, get_settings(), document_id)
```

Import at the top of `jobs.py`: `from library.semantic_membership import auto_add_document`.

Queue it in the INDEXED block (after the `evaluate_series_autocontinue` best-effort try/except, ~line 431):

```python
                try:
                    await evaluate_semantic_groups.defer_async(document_id=document.id)
                except Exception:
                    logger.warning(
                        "could not queue semantic-group eval for document %s; continuing",
                        document.id,
                        exc_info=True,
                    )
```

Note: this is outside the `sender_id/kind_id is not None` guard — Smart Groups don't require a sender or kind. Place it after that `if` block closes.

- [ ] **Step 6: Verify import + task registration**

Run: `uv run python -c "import library.jobs as j; print(j.evaluate_semantic_groups.name)"`
Expected: `library.jobs.evaluate_semantic_groups`

- [ ] **Step 7: Commit**

```bash
git add src/library/semantic_membership.py src/library/jobs.py tests/test_semantic_membership.py
git commit -m "feat(charts): auto-add newly-indexed docs to matching Smart Groups"
```

---

### Task 8: Prune = negative example (accept origin, dismiss + remove write exclusions)

**Files:**
- Modify: `src/library/api/charts.py` — `accept_authored_suggestion` (origin), `dismiss_authored_suggestion` (write exclusion), `remove_authored_member` (write exclusion + clear on re-add in `add_authored_member`)
- Test: `tests/test_smart_groups_api.py`

**Interfaces:**
- Consumes: `AuthoredSeriesExclusion`, `MemberOrigin`.
- Produces: removing a member writes an exclusion; dismissing a suggestion writes an exclusion; accepting sets `origin=ACCEPTED_SUGGESTION` and clears any exclusion; adding a member clears any exclusion.

- [ ] **Step 1: Write the failing test** — prune, then a re-sweep must not re-add

```python
@pytest.mark.asyncio
async def test_pruned_member_is_not_re_added(client, session, settings, make_document, add_chunk):
    group = AuthoredSeries(name="ev-prune-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-prune-member")
    add_chunk(member, [0.9, 0.1])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    wrong = await make_document(title="ev-prune-wrong", amount_total="9.00", currency="EUR")
    add_chunk(wrong, [0.88, 0.12])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=wrong.id, origin=MemberOrigin.AUTO))
    await session.commit()

    resp = await client.delete(f"/api/charts/authored/{group.id}/members/{wrong.id}")
    assert resp.status_code == 200
    # Exclusion written:
    excl = (await session.execute(
        select(AuthoredSeriesExclusion).where(
            AuthoredSeriesExclusion.authored_series_id == group.id,
            AuthoredSeriesExclusion.document_id == wrong.id,
        )
    )).scalar_one_or_none()
    assert excl is not None
    # A fresh auto-add attempt must respect the veto:
    joined = await auto_add_document(session, settings, wrong.id)
    assert group.id not in joined
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_smart_groups_api.py -k re_added -v`
Expected: FAIL — no exclusion row written.

- [ ] **Step 3: Implement the three edits** in `api/charts.py`

In `accept_authored_suggestion`, set origin and clear any exclusion:

```python
    if document_id not in await _authored_member_ids(session, authored_id):
        session.add(
            AuthoredSeriesMember(
                authored_series_id=authored_id,
                document_id=document_id,
                origin=MemberOrigin.ACCEPTED_SUGGESTION,
            )
        )
    await session.execute(
        delete(AuthoredSeriesExclusion).where(
            AuthoredSeriesExclusion.authored_series_id == authored_id,
            AuthoredSeriesExclusion.document_id == document_id,
        )
    )
```

In `dismiss_authored_suggestion`, after the suggestion upsert, also upsert an exclusion:

```python
    await session.execute(
        pg_insert(AuthoredSeriesExclusion)
        .values(authored_series_id=authored_id, document_id=document_id)
        .on_conflict_do_nothing(constraint="authored_series_exclusions_series_document")
    )
```

In `remove_authored_member` (grep for the `DELETE .../members/{document_id}` handler), after deleting the member row, write the exclusion:

```python
    await session.execute(
        pg_insert(AuthoredSeriesExclusion)
        .values(authored_series_id=authored_id, document_id=document_id)
        .on_conflict_do_nothing(constraint="authored_series_exclusions_series_document")
    )
```

In `add_authored_member` (the `POST .../members` handler), clear any exclusion so a hand re-add sticks:

```python
    await session.execute(
        delete(AuthoredSeriesExclusion).where(
            AuthoredSeriesExclusion.authored_series_id == authored_id,
            AuthoredSeriesExclusion.document_id == payload.document_id,
        )
    )
```

Import `AuthoredSeriesExclusion` in `charts.py`.

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_smart_groups_api.py -k re_added -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/api/charts.py tests/test_smart_groups_api.py
git commit -m "feat(charts): prune writes a negative example (dismiss/remove -> exclusion)"
```

---

### Task 9: Group blurb + expose mode/origin/auto-count in the body

**Files:**
- Modify: `src/library/series.py` (`summarize_authored_series` / `_authored_body` in `charts.py`) to include `mode`, per-member `origin`, and `auto_added_count`; `src/library/series_insight.py` reuse for the blurb
- Test: `tests/test_smart_groups_api.py`

**Interfaces:**
- Produces: the authored body carries `mode: "manual"|"semantic"`, `auto_added_count: int`, and each entry in the "documents in this series" list carries `origin`. Best-effort blurb fills `AuthoredSeries.description` **only when null** (never clobbers a user edit — the SeriesMetaOverride precedent).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_authored_body_exposes_mode_and_auto_count(client, session, make_document, add_chunk):
    group = AuthoredSeries(name="ev-body-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    d = await make_document(title="ev-body-doc", amount_total="12.00", currency="EUR")
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=d.id, origin=MemberOrigin.AUTO))
    await session.commit()

    resp = await client.get(f"/api/charts/a-{group.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["auto_added_count"] == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_smart_groups_api.py -k mode_and_auto_count -v`
Expected: FAIL — `mode`/`auto_added_count` absent.

- [ ] **Step 3: Implement** — thread `mode` and an `auto_added_count` (count of `origin=AUTO` members) into the authored body serialization. Find where `summarize_authored_series` builds its dict (grep `def summarize_authored_series` in `series.py`) and where `_authored_body` assembles the response in `charts.py`; add:

```python
    body["mode"] = series.mode.value
    body["auto_added_count"] = sum(1 for m in members_rows if m.origin == MemberOrigin.AUTO)
```

For the blurb, add a best-effort call in the semantic branch of `create_authored_series` and in `auto_add_document`'s caller path (or a small `refresh_group_blurb(session, settings, group_id)` in `series_insight.py` that builds a `SeriesSummary` from the group and calls `generate_description`, writing to `AuthoredSeries.description` only if it is `None`). Keep it best-effort (wrap in try/except, log-and-continue) — a blurb failure must never fail creation or indexing.

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_smart_groups_api.py -k mode_and_auto_count -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check . && uv run ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/library/series.py src/library/api/charts.py src/library/series_insight.py tests/test_smart_groups_api.py
git commit -m "feat(charts): expose mode/origin/auto-count; best-effort Smart Group blurb"
```

---

### Task 10: Frontend — Smart Group create toggle, staged review, auto-added affordance

**Files:**
- Modify: `frontend/src/api/documents.ts`, `frontend/src/views/ChartsView.vue`, `frontend/src/components/SeriesChartTile.vue`
- Test: `frontend/tests/` unit (Vitest) + one Playwright e2e

**Interfaces:**
- Consumes: `POST /api/charts/authored` now takes `mode` + `seed_document_ids` and returns `backfill`; body carries `mode` + `auto_added_count`; suggestion accept/dismiss endpoints reused for the staged review.

- [ ] **Step 1: Extend the API client** in `documents.ts`

```typescript
export interface AuthoredSeriesCreate {
  name: string
  currency?: string | null
  description?: string | null
  document_ids?: number[]
  mode?: 'manual' | 'semantic'
  seed_document_ids?: number[]
}

export interface BackfillMatch {
  document_id: number
  score: number
}

/** DocumentSeries plus the staged backfill returned when mode==='semantic'. */
export type CreateSeriesResult = DocumentSeries & { backfill?: BackfillMatch[] }

export function createAuthoredSeries(body: AuthoredSeriesCreate): Promise<CreateSeriesResult> {
  return apiFetch<CreateSeriesResult>('/api/charts/authored', { method: 'POST', body })
}
```

Add `mode` and `auto_added_count` to the `DocumentSeries` interface (grep `export interface DocumentSeries`), both optional.

- [ ] **Step 2: Add the Smart Group toggle to the create form** in `ChartsView.vue`

Add reactive `newMode = ref<'manual' | 'semantic'>('manual')`; in `submitCreate`, pass `mode: newMode.value` and `seed_document_ids: selectedDocs.value.map(d => d.id)` when semantic. Add a toggle above the search field:

```html
      <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
        <input type="checkbox" data-testid="charts-create-smart"
               :checked="newMode === 'semantic'"
               @change="newMode = ($event.target as HTMLInputElement).checked ? 'semantic' : 'manual'" />
        Smart Group — auto-populate from similar documents (you review the first batch)
      </label>
```

When `submitCreate` returns a result with a non-empty `backfill`, open a staged-review modal (Step 3) instead of only reloading.

- [ ] **Step 3: Staged-review modal** — reuse the existing suggestion accept/dismiss calls (`acceptAuthoredSuggestion`/`dismissAuthoredSuggestion` in `documents.ts`; add them if absent, POSTing to `/api/charts/authored/{id}/suggestions/{doc}/accept|dismiss`). Render the `backfill` list with the document title (fetch titles via `listDocuments({ ids })` or show `Document #id`), each row with **Add** (accept) and **Skip** (dismiss) buttons, plus **Add all**. `data-testid="charts-backfill-modal"`, rows `data-testid="charts-backfill-row"`.

- [ ] **Step 4: Auto-added affordance** in `SeriesChartTile.vue` — when `series.mode === 'semantic'` and `series.auto_added_count > 0`, render a badge near the heading:

```html
      <span v-if="series.mode === 'semantic' && series.auto_added_count"
            data-testid="series-auto-added-badge"
            class="ml-2 rounded-full bg-violet-100 dark:bg-violet-900/40 px-2 py-0.5 text-xs text-violet-700 dark:text-violet-300">
        {{ series.auto_added_count }} added automatically
      </span>
```

In the "documents in this series" list, flag rows whose `origin === 'auto'` with a small dot/label so the existing remove control is the one-click prune.

- [ ] **Step 5: Unit test (Vitest)** — assert the create payload includes `mode: 'semantic'` and `seed_document_ids` when the toggle is on; assert the badge renders for `auto_added_count > 0`. Mock `apiFetch`. Keep `limit ≤ 100` on any list call.

Run: `cd frontend && npm run test:unit -- charts`
Expected: PASS.

- [ ] **Step 6: One Playwright e2e** — create a Smart Group, see the staged-review modal, add one match, confirm the tile shows. Flag any fixture documents **datelessly** (no `document_date`) so the shared serial backend's dashboard sort is not polluted and `.first()`-tile specs elsewhere stay green. Assert visibility with `toBeVisible()` mindful of mobile/tablet-webkit (< lg).

Run: `cd frontend && npm run test:e2e -- smart-groups`
Expected: PASS on both webkit projects.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/documents.ts frontend/src/views/ChartsView.vue frontend/src/components/SeriesChartTile.vue frontend/tests/
git commit -m "feat(charts): Smart Group create toggle, staged review, auto-added badge"
```

---

### Task 11: Docs, journal, roadmap

**Files:**
- Create: `docs/smart-groups.md`, `journal/260724-smart-groups.md`
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Write `docs/smart-groups.md`** — H1 `# Smart Groups` (clean title, no number/date; decimal numbering only below H1 per the doc convention). Cover: what a Smart Group is; the `mode`/`origin`/exclusion model; the scorer (`sim_pos ≥ τ` and `sim_pos > sim_neg + margin`) and the `semantic_group_*` settings; the three flows (staged backfill, silent auto-add, prune-as-negative); mixed-currency FX; and the explicit non-goal that the LLM never decides membership. Link the spec.

- [ ] **Step 2: Write `journal/260724-smart-groups.md`** — decisions: extend authored series vs. new system; nearest-positive-neighbour over centroid/LLM (and why — the odd-one-out hallucination precedent); auto-add + stage-the-backfill choices; the duplicate-sender companion fix split out.

- [ ] **Step 3: Update `docs/roadmap.md`** — mark the Smart Groups item.

- [ ] **Step 4: Commit**

```bash
git add docs/smart-groups.md journal/260724-smart-groups.md docs/roadmap.md
git commit -m "docs(charts): Smart Groups feature doc + journal + roadmap"
```

---

### Task 12: Companion fix — duplicate-sender normalization (SEPARATE PR)

Do this on its own branch off `main` (not the Smart Groups branch) so it can ship independently.

**Files:**
- Modify: `src/library/extraction/apply.py` — `upsert_sender` (~:74-85)
- Test: `tests/` (extraction/taxonomy test module)

- [ ] **Step 1: Write the failing test** — two names differing only by internal whitespace resolve to one sender

```python
@pytest.mark.asyncio
async def test_upsert_sender_collapses_internal_whitespace(session):
    a = await upsert_sender(session, "De Hooge Waerder")
    b = await upsert_sender(session, "De  Hooge   Waerder")  # doubled internal spaces
    assert a.id == b.id
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/ -k collapses_internal_whitespace -v`
Expected: FAIL — two distinct sender rows.

- [ ] **Step 3: Fix `upsert_sender`** — change `cleaned = name.strip()` to match `create_sender`:

```python
    cleaned = " ".join(name.split())
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/ -k collapses_internal_whitespace -v`
Expected: PASS.

- [ ] **Step 5: Commit + open PR**

```bash
git checkout -b fix/sender-whitespace-normalization main
git add src/library/extraction/apply.py tests/
git commit -m "fix(ingestion): collapse internal whitespace in upsert_sender (dedupe senders)"
```

- [ ] **Step 6: Verify the live dupes before merging any sender rows** — this is operational, not code. Confirm whether the current De Hooge Waerder / Anthropic pairs are this whitespace bug vs. two document kinds vs. two currencies (query the live DB), then merge only true duplicates via `rename_sender(merge=True)` / `reassign_and_delete_sender`. Show findings and confirm before any irreversible merge.

---

## Self-review (against the spec)

- **§3 data model** → Task 1 (mode, origin, exclusions, suggestion score). ✔
- **§4 scorer** → Tasks 3–4 (document vector + `score_vector`, graded-vector tests, cold-start/veto/threshold). ✔
- **§4 settings τ/margin** → Task 2. ✔
- **§5.1 create + staged backfill** → Task 6 (`sweep_backfill`, create endpoint, `backfill` payload). ✔
- **§5.2 forward auto-add** → Task 7 (`auto_add_document` + job + INDEXED queueing outside the sender/kind guard). ✔
- **§5.3 prune = negative** → Task 8 (dismiss/remove write exclusions; accept/add clear them). ✔
- **§6 mixed currency FX** → Task 5 (`_load_authored_members` converts). ✔
- **§7 auto-add guardrail** → Task 9 (`auto_added_count`) + Task 10 (badge + origin flag). ✔
- **§8 LLM narrow role** → Task 6 (`_name_anchor_ids` seed query) + Task 9 (blurb, description-null only). ✔
- **§9 companion fix** → Task 12 (separate PR). ✔
- **§11 testing** → graded vectors (Task 4), FX (Task 5), API/limit-cap/unique-tag (Tasks 6, 8, 9), frontend/e2e mobile + dateless fixtures (Task 10). ✔
- **§12 build order** → Tasks are in that order. ✔

**Placeholder scan:** the only deferred specifics are attribute-name confirmations flagged inline (`SemanticHit.document_id` vs `.doc_id`; the exact `summarize_authored_series` dict site; the `add_chunk`/`seed_fx` test fixtures to reuse) — each names the grep to run, not a TODO. No "add error handling"/"write tests"-style gaps.

**Type consistency:** `score_vector`/`MembershipScore`, `document_vectors`, `evaluate_group`, `sweep_backfill`, `auto_add_document` signatures match across tasks; `MemberOrigin`/`SeriesMode` values (`manual`/`semantic`/`auto`/`accepted_suggestion`) are identical in models, migration enum strings, API, and TS.
