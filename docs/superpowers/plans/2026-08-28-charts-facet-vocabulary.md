# Facet Vocabulary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the archive's 771 free-form tags with a controlled, closed-set facet vocabulary that labels every document and is usable by search, filters, and (later) charts.

**Architecture:** Facets are named dimensions; each holds a fixed set of values; a document carries at most one value per facet, guaranteed by a composite primary key. An LLM assigns values from the closed set only — it cannot invent one, and returns `unknown` plus a queued suggestion instead. Values carry aliases so one concept survives spelling, mojibake and identifier variants.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 17, Anthropic SDK, pytest, Vue 3 + TypeScript, Tailwind, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-28-charts-redesign-design.md` — this plan implements **layer A** (§7) only.

## Global Constraints

- Python target is 3.13. Type annotations on every function signature and on non-obvious locals.
- `uv` for all dependency management. `pytest` for tests, `coverage` for coverage.
- **Do not add a new `*_model` setting.** Any `*_model` setting requires a matching row in `MODEL_PRICING_USD_PER_MTOK` (`src/library/extraction/pricing.py`) or the app refuses to boot at startup. This plan reuses `settings.extraction_model`.
- CI runs `ruff check` **and** `ruff format --check` over the **whole repository including `migrations/`**. Format new migration files before committing.
- `GET /api/documents` rejects `limit > 100` with a 422. Any client-side list fetch must keep `limit <= 100`.
- Integration tests share one Postgres and one session-scoped `api_database_url`; list endpoints default to 25 rows. **Scope every list assertion by a unique tag/marker** rather than asserting on absolute counts.
- Frontend e2e runs on three projects: chromium@1280, mobile-webkit@375, tablet-webkit@656. Any UI that collapses below `lg` breaks visibility assertions on the latter two.
- No `except Exception -> pytest.skip` guards. A skipped test reads as a pass.
- **The repository is public.** No real sender names, recipient names, personal names, addresses, vehicle registrations, or real amounts in code, fixtures, comments, docs, or commit messages. The `vehicle`, `property` and `person` facets therefore ship with **zero seeded values** — those are created at runtime by the owner.
- Migration head is `0031`. This plan adds `0032`.

## File Structure

**Create:**
- `migrations/versions/0032_facet_vocabulary.py` — the five new tables and their constraints.
- `src/library/facets/__init__.py` — package marker; re-exports the public dataclasses.
- `src/library/facets/vocabulary.py` — load/resolve/mutate the vocabulary; read/write document labels. No LLM.
- `src/library/facets/seed.py` — the initial vocabulary as data, plus an idempotent seeder.
- `src/library/facets/labeller.py` — prompt construction and the LLM call. Pure with respect to the DB except for reading the vocabulary.
- `src/library/facets/apply.py` — turn labeller proposals into rows: labels, `unknown`, suggestions.
- `src/library/api/facets.py` — REST surface for the vocabulary and for a document's labels.
- `frontend/src/api/facets.ts` — typed client.
- `frontend/src/components/facets/FacetFilterBar.vue` — filter controls for the document list.
- `frontend/src/components/facets/FacetEditor.vue` — per-document label editor.
- `docs/facets.md` — the feature's documentation.
- `journal/260828-facet-vocabulary.md` — journal entry.

**Modify:**
- `src/library/models.py` — five ORM classes.
- `src/library/app.py:258` — register the facets router.
- `src/library/search.py` — `DocumentFilters.facets` and its condition builder.
- `src/library/cli.py` — `library label-archive` command.
- `src/library/extraction/apply.py` — label a document after extraction succeeds.
- `frontend/src/views/DocumentListView.vue` — mount the filter bar.
- `frontend/src/views/DocumentDetailView.vue` — mount the editor.

**Boundaries:** `vocabulary.py` never calls an LLM; `labeller.py` never writes; `apply.py` is the only module that does both. That split is what lets the closed-set guarantee be tested without a model.

---

### Task 1: Schema and models for the facet vocabulary

**Files:**
- Create: `migrations/versions/0032_facet_vocabulary.py`
- Modify: `src/library/models.py`
- Test: `tests/test_facet_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: ORM classes `Facet`, `FacetValue`, `FacetValueAlias`, `DocumentLabel`, `FacetValueSuggestion`. Table names `facets`, `facet_values`, `facet_value_aliases`, `document_labels`, `facet_value_suggestions`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_schema.py`:

```python
"""The facet schema's guarantees, asserted by trying to violate them.

Every constraint here exists because breaking it corrupts a GROUP BY silently
rather than loudly: a document with two values of one facet double-counts, and
a label pointing at another facet's value groups under the wrong heading.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Facet, FacetValue

pytestmark = pytest.mark.integration


async def _seed_two_facets(database_url: str, tag: str) -> tuple[int, int, int]:
    """Create two facets, one value each. Returns (facet_a, value_a, value_b)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            fa = Facet(key=f"a-{tag}", label="A")
            fb = Facet(key=f"b-{tag}", label="B")
            session.add_all([fa, fb])
            await session.flush()
            va = FacetValue(facet_id=fa.id, key="one", label="One")
            vb = FacetValue(facet_id=fb.id, key="two", label="Two")
            session.add_all([va, vb])
            await session.flush()
            await session.commit()
            return fa.id, va.id, vb.id
    finally:
        await engine.dispose()


async def _insert_label(database_url: str, document_id: int, facet_id: int, value_id: int) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.execute(
                text(
                    "INSERT INTO document_labels (document_id, facet_id, facet_value_id) "
                    "VALUES (:d, :f, :v)"
                ),
                {"d": document_id, "f": facet_id, "v": value_id},
            )
            await session.commit()
    finally:
        await engine.dispose()


def test_a_label_cannot_point_at_another_facets_value(
    api_database_url: str, seeded_document_id: int
) -> None:
    tag = uuid.uuid4().hex[:8]
    facet_a, _value_a, value_b = asyncio.run(_seed_two_facets(api_database_url, tag))
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_b))


def test_a_document_cannot_hold_two_values_of_one_facet(
    api_database_url: str, seeded_document_id: int
) -> None:
    tag = uuid.uuid4().hex[:8]
    facet_a, value_a, _value_b = asyncio.run(_seed_two_facets(api_database_url, tag))
    asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_a))
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_a))
```

Add the shared document fixture to `tests/conftest.py` (append at end of file):

```python
@pytest.fixture
def seeded_document_id(api_database_url: str) -> int:
    """One indexed document, for tests that only need a valid documents.id."""
    import asyncio
    import hashlib
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from library.models import Document, DocumentSource, DocumentStatus

    async def _seed() -> int:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                marker = f"facet-fixture:{_uuid.uuid4()}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    title=marker,
                )
                session.add(doc)
                await session.flush()
                await session.commit()
                return doc.id
        finally:
            await engine.dispose()

    return asyncio.run(_seed())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'Facet' from 'library.models'`.

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0032_facet_vocabulary.py`:

```python
"""facet vocabulary

Five tables backing the controlled label vocabulary that replaces free-form
tags (design spec layer A).

Two constraints carry the whole model and are worth naming:

``document_labels`` has a composite primary key ``(document_id, facet_id)``,
so a document holds at most one value per facet. That is what a GROUP BY over
a facet relies on to avoid double-counting a document.

``facet_values`` carries a redundant ``UNIQUE (id, facet_id)`` purely so
``document_labels`` can hold a composite foreign key on
``(facet_value_id, facet_id)``. Without it a label row can claim one facet
while pointing at another facet's value, and every aggregate over that facet is
silently wrong.

``parent_id`` is nullable and unused at ship. It exists so moving a facet to two
levels later is a data change rather than a migration.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "facets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("key", name="facets_key"),
    )
    op.create_table(
        "facet_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("facet_values.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("facet_id", "key", name="facet_values_facet_key"),
        sa.UniqueConstraint("id", "facet_id", name="facet_values_id_facet"),
    )
    op.create_table(
        "facet_value_aliases",
        sa.Column(
            "facet_value_id",
            sa.Integer(),
            sa.ForeignKey("facet_values.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alias", sa.String(255), primary_key=True),
    )
    op.create_table(
        "document_labels",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("facet_value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="document_labels_value_facet",
        ),
    )
    op.create_index("ix_document_labels_value", "document_labels", ["facet_value_id"])
    op.create_table(
        "facet_value_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suggested_label", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "facet_id", "document_id", "suggested_label", name="facet_value_suggestions_unique"
        ),
    )


def downgrade() -> None:
    op.drop_table("facet_value_suggestions")
    op.drop_index("ix_document_labels_value", table_name="document_labels")
    op.drop_table("document_labels")
    op.drop_table("facet_value_aliases")
    op.drop_table("facet_values")
    op.drop_table("facets")
```

- [ ] **Step 4: Write the ORM models**

Append to `src/library/models.py` (after the existing `Tag` definitions, keeping the file's `Mapped`/`mapped_column` style):

```python
class Facet(Base):
    """A named label dimension. A document carries at most one value per facet."""

    __tablename__ = "facets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(255))
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class FacetValue(Base):
    """One allowed value of a facet.

    ``parent_id`` is unused at ship; it exists so a flat facet can gain a second
    level as a data change rather than a migration. The redundant
    ``UNIQUE (id, facet_id)`` is what lets label tables hold a composite foreign
    key and so cannot point at another facet's value.
    """

    __tablename__ = "facet_values"
    __table_args__ = (
        UniqueConstraint("facet_id", "key", name="facet_values_facet_key"),
        UniqueConstraint("id", "facet_id", name="facet_values_id_facet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facet_id: Mapped[int] = mapped_column(ForeignKey("facets.id", ondelete="RESTRICT"))
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("facet_values.id", ondelete="RESTRICT"), nullable=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class FacetValueAlias(Base):
    """A surface form that resolves to a value: a plate, a marque, a misspelling."""

    __tablename__ = "facet_value_aliases"

    facet_value_id: Mapped[int] = mapped_column(
        ForeignKey("facet_values.id", ondelete="CASCADE"), primary_key=True
    )
    alias: Mapped[str] = mapped_column(String(255), primary_key=True)


class DocumentLabel(Base):
    """One document's value for one facet. The PK enforces at-most-one."""

    __tablename__ = "document_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="document_labels_value_facet",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    facet_id: Mapped[int] = mapped_column(
        ForeignKey("facets.id", ondelete="RESTRICT"), primary_key=True
    )
    facet_value_id: Mapped[int] = mapped_column(Integer)


class FacetValueSuggestion(Base):
    """A value the labeller wanted but the closed vocabulary does not contain.

    Queued for approval rather than created. This is the mechanism that keeps
    the vocabulary closed while still letting it grow deliberately.
    """

    __tablename__ = "facet_value_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "facet_id", "document_id", "suggested_label", name="facet_value_suggestions_unique"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facet_id: Mapped[int] = mapped_column(ForeignKey("facets.id", ondelete="CASCADE"))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    suggested_label: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

If `ForeignKeyConstraint`, `UniqueConstraint`, `func` or `DateTime` are not already imported at the top of `models.py`, add them to the existing `from sqlalchemy import ...` line.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_schema.py -v`
Expected: 2 passed. Both assertions raise `IntegrityError`, proving the constraints are enforced by the database rather than by application code.

- [ ] **Step 6: Verify the migration round-trips**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: no errors. A failure here means `downgrade()` drops tables in an order that violates a foreign key.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format migrations/versions/0032_facet_vocabulary.py src/library/models.py tests/test_facet_schema.py tests/conftest.py
uv run ruff check src/ tests/ migrations/
git add migrations/versions/0032_facet_vocabulary.py src/library/models.py tests/test_facet_schema.py tests/conftest.py
git commit -m "feat(facets): schema for the controlled label vocabulary"
```

---

### Task 2: Vocabulary service — load, resolve, read and write labels

**Files:**
- Create: `src/library/facets/__init__.py`, `src/library/facets/vocabulary.py`
- Test: `tests/test_facet_vocabulary.py`

**Interfaces:**
- Consumes: `Facet`, `FacetValue`, `FacetValueAlias`, `DocumentLabel` from Task 1.
- Produces:
  - `VocabularyValue(id: int, key: str, label: str, parent_id: int | None, aliases: tuple[str, ...])`
  - `VocabularyFacet(id: int, key: str, label: str, ordinal: int, values: tuple[VocabularyValue, ...])` with method `value(key: str) -> VocabularyValue | None`
  - `async load_vocabulary(session: AsyncSession) -> tuple[VocabularyFacet, ...]`
  - `async document_labels(session: AsyncSession, document_id: int) -> dict[str, str]` — `{facet_key: value_key}`
  - `async set_document_label(session, document_id: int, facet_key: str, value_key: str | None) -> None` — `None` clears; raises `UnknownFacetError` / `UnknownValueError`
  - Exceptions `UnknownFacetError`, `UnknownValueError` (both subclass `ValueError`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_vocabulary.py`:

```python
"""Vocabulary reads and writes. No LLM in this module, by design."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    document_labels,
    load_vocabulary,
    set_document_label,
)
from library.models import Facet, FacetValue, FacetValueAlias

pytestmark = pytest.mark.integration


async def _seed_vocab(database_url: str, facet_key: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            facet = Facet(key=facet_key, label="Scope")
            session.add(facet)
            await session.flush()
            business = FacetValue(facet_id=facet.id, key="business", label="Business")
            personal = FacetValue(facet_id=facet.id, key="personal", label="Personal")
            session.add_all([business, personal])
            await session.flush()
            session.add(FacetValueAlias(facet_value_id=business.id, alias="work"))
            await session.commit()
    finally:
        await engine.dispose()


async def _with_session[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def test_load_vocabulary_carries_values_and_aliases(api_database_url: str) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))
    facets = asyncio.run(_with_session(api_database_url, load_vocabulary))
    facet = next(f for f in facets if f.key == key)
    assert {v.key for v in facet.values} == {"business", "personal"}
    assert facet.value("business").aliases == ("work",)
    assert facet.value("nope") is None


def test_set_and_read_a_label(api_database_url: str, seeded_document_id: int) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")

    asyncio.run(_with_session(api_database_url, _set))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert labels[key] == "business"


def test_setting_a_second_value_replaces_rather_than_duplicates(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set_twice(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")
        await set_document_label(session, seeded_document_id, key, "personal")

    asyncio.run(_with_session(api_database_url, _set_twice))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert labels[key] == "personal"


def test_clearing_a_label_removes_it(api_database_url: str, seeded_document_id: int) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set_then_clear(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")
        await set_document_label(session, seeded_document_id, key, None)

    asyncio.run(_with_session(api_database_url, _set_then_clear))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert key not in labels


def test_unknown_facet_and_unknown_value_raise(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _bad_facet(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, "no-such-facet", "business")

    async def _bad_value(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "no-such-value")

    with pytest.raises(UnknownFacetError):
        asyncio.run(_with_session(api_database_url, _bad_facet))
    with pytest.raises(UnknownValueError):
        asyncio.run(_with_session(api_database_url, _bad_value))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_vocabulary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets'`.

- [ ] **Step 3: Write the implementation**

Create `src/library/facets/__init__.py`:

```python
"""Controlled label vocabulary: facets, values, aliases, and document labels.

Three modules with deliberately separate jobs. ``vocabulary`` reads and writes
the vocabulary and never calls an LLM. ``labeller`` builds a prompt and calls
the model and never writes. ``apply`` is the only module that does both. That
split is what lets the closed-set guarantee be tested without a model.
"""

from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    VocabularyFacet,
    VocabularyValue,
    document_labels,
    load_vocabulary,
    set_document_label,
)

__all__ = [
    "UnknownFacetError",
    "UnknownValueError",
    "VocabularyFacet",
    "VocabularyValue",
    "document_labels",
    "load_vocabulary",
    "set_document_label",
]
```

Create `src/library/facets/vocabulary.py`:

```python
"""Reads and writes over the facet vocabulary. No LLM, no network."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import DocumentLabel, Facet, FacetValue, FacetValueAlias


class UnknownFacetError(ValueError):
    """Raised when a facet key is not in the vocabulary."""


class UnknownValueError(ValueError):
    """Raised when a value key is not in the named facet.

    This is the closed-set guarantee's enforcement point: callers cannot create
    a value by naming one, only by going through ``create_value``.
    """


@dataclass(frozen=True, slots=True)
class VocabularyValue:
    id: int
    key: str
    label: str
    parent_id: int | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VocabularyFacet:
    id: int
    key: str
    label: str
    ordinal: int
    values: tuple[VocabularyValue, ...]

    def value(self, key: str) -> VocabularyValue | None:
        return next((v for v in self.values if v.key == key), None)


async def load_vocabulary(session: AsyncSession) -> tuple[VocabularyFacet, ...]:
    """The whole vocabulary, ordered by facet then value ordinal then key.

    Loaded whole rather than per-facet: it is a few dozen rows, it is needed in
    full by both the labelling prompt and the API, and one query beats N.
    """
    facet_rows = (await session.execute(select(Facet).order_by(Facet.ordinal, Facet.key))).scalars()
    value_rows = (
        await session.execute(select(FacetValue).order_by(FacetValue.ordinal, FacetValue.key))
    ).scalars()
    alias_rows = (await session.execute(select(FacetValueAlias))).scalars()

    aliases: dict[int, list[str]] = {}
    for row in alias_rows:
        aliases.setdefault(row.facet_value_id, []).append(row.alias)

    by_facet: dict[int, list[VocabularyValue]] = {}
    for row in value_rows:
        by_facet.setdefault(row.facet_id, []).append(
            VocabularyValue(
                id=row.id,
                key=row.key,
                label=row.label,
                parent_id=row.parent_id,
                aliases=tuple(sorted(aliases.get(row.id, ()))),
            )
        )
    return tuple(
        VocabularyFacet(
            id=facet.id,
            key=facet.key,
            label=facet.label,
            ordinal=facet.ordinal,
            values=tuple(by_facet.get(facet.id, ())),
        )
        for facet in facet_rows
    )


async def _resolve(
    session: AsyncSession, facet_key: str, value_key: str | None
) -> tuple[int, int | None]:
    """``(facet_id, value_id)``; ``value_id`` is None when ``value_key`` is None."""
    facet_id = (
        await session.execute(select(Facet.id).where(Facet.key == facet_key))
    ).scalar_one_or_none()
    if facet_id is None:
        raise UnknownFacetError(facet_key)
    if value_key is None:
        return facet_id, None
    value_id = (
        await session.execute(
            select(FacetValue.id).where(
                FacetValue.facet_id == facet_id, FacetValue.key == value_key
            )
        )
    ).scalar_one_or_none()
    if value_id is None:
        raise UnknownValueError(f"{facet_key}={value_key}")
    return facet_id, value_id


async def document_labels(session: AsyncSession, document_id: int) -> dict[str, str]:
    """``{facet_key: value_key}`` for one document. Absent facets are absent keys."""
    rows = await session.execute(
        select(Facet.key, FacetValue.key)
        .join(DocumentLabel, DocumentLabel.facet_id == Facet.id)
        .join(FacetValue, FacetValue.id == DocumentLabel.facet_value_id)
        .where(DocumentLabel.document_id == document_id)
    )
    return {facet_key: value_key for facet_key, value_key in rows.all()}


async def set_document_label(
    session: AsyncSession, document_id: int, facet_key: str, value_key: str | None
) -> None:
    """Set (or, with ``value_key=None``, clear) one document's value for one facet.

    An upsert rather than delete-then-insert: the composite primary key means a
    second value for the same facet is a conflict, not a second row, so the
    at-most-one rule is upheld by the database whichever path callers take.
    """
    facet_id, value_id = await _resolve(session, facet_key, value_key)
    if value_id is None:
        await session.execute(
            delete(DocumentLabel).where(
                DocumentLabel.document_id == document_id, DocumentLabel.facet_id == facet_id
            )
        )
        return
    statement = (
        pg_insert(DocumentLabel)
        .values(document_id=document_id, facet_id=facet_id, facet_value_id=value_id)
        .on_conflict_do_update(
            index_elements=["document_id", "facet_id"], set_={"facet_value_id": value_id}
        )
    )
    await session.execute(statement)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_vocabulary.py -v`
Expected: 5 passed.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/library/facets/ tests/test_facet_vocabulary.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/ tests/test_facet_vocabulary.py
git commit -m "feat(facets): vocabulary reads and document label writes"
```

---

### Task 3: Seed the initial vocabulary

**Files:**
- Create: `src/library/facets/seed.py`
- Test: `tests/test_facet_seed.py`

**Interfaces:**
- Consumes: `load_vocabulary` from Task 2.
- Produces: `SEED_VOCABULARY: tuple[SeedFacet, ...]`, `async seed_vocabulary(session: AsyncSession) -> int` returning the number of values created.

`vehicle`, `property` and `person` are seeded as **facets with no values**. Their values name real vehicles, addresses and people, which must not enter a public repository; they are created at runtime through Task 4's `create_value`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_seed.py`:

```python
"""The seed vocabulary: what ships, and that seeding twice changes nothing."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.seed import SEED_VOCABULARY, seed_vocabulary
from library.facets.vocabulary import load_vocabulary

pytestmark = pytest.mark.integration


async def _seed_twice(database_url: str) -> tuple[int, int]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            first = await seed_vocabulary(session)
            await session.commit()
            second = await seed_vocabulary(session)
            await session.commit()
            return first, second
    finally:
        await engine.dispose()


async def _load(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await load_vocabulary(session)
    finally:
        await engine.dispose()


def test_seeding_is_idempotent(api_database_url: str) -> None:
    first, second = asyncio.run(_seed_twice(api_database_url))
    assert first > 0
    assert second == 0


def test_seeded_facets_and_values_are_present(api_database_url: str) -> None:
    asyncio.run(_seed_twice(api_database_url))
    facets = {f.key: f for f in asyncio.run(_load(api_database_url))}
    assert {"category", "scope", "cost_type", "vehicle", "property", "person"} <= facets.keys()
    assert {"business", "personal"} == {v.key for v in facets["scope"].values}
    assert "accountancy" in {v.key for v in facets["category"].values}
    assert "accounting" in facets["category"].value("accountancy").aliases


def test_personal_facets_ship_with_no_values(api_database_url: str) -> None:
    """vehicle/property/person values name real people and things; they are
    created at runtime, never committed to a public repository."""
    asyncio.run(_seed_twice(api_database_url))
    facets = {f.key: f for f in asyncio.run(_load(api_database_url))}
    for key in ("vehicle", "property", "person"):
        assert facets[key].values == ()


def test_no_seed_value_key_repeats_within_a_facet() -> None:
    for facet in SEED_VOCABULARY:
        keys = [value.key for value in facet.values]
        assert len(keys) == len(set(keys)), f"duplicate value key in {facet.key}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets.seed'`.

- [ ] **Step 3: Write the implementation**

Create `src/library/facets/seed.py`:

```python
"""The vocabulary this feature ships with, and an idempotent seeder.

Derived from the shape of the existing free-form tags, not migrated from them:
the tags are the drift this vocabulary replaces, so they inform which
dimensions exist and never which value a document gets.

``vehicle``, ``property`` and ``person`` are declared as facets with **no
values**. Their values name real vehicles, addresses and people; this
repository is public, so they are created at runtime through
``vocabulary.create_value`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Facet, FacetValue, FacetValueAlias


@dataclass(frozen=True, slots=True)
class SeedValue:
    key: str
    label: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedFacet:
    key: str
    label: str
    ordinal: int
    values: tuple[SeedValue, ...]


SEED_VOCABULARY: tuple[SeedFacet, ...] = (
    SeedFacet(
        key="category",
        label="Category",
        ordinal=0,
        values=(
            SeedValue(
                "accountancy",
                "Accountancy",
                ("accounting", "bookkeeping", "fiscal services", "tax advice", "tax preparation"),
            ),
            SeedValue("tax", "Tax", ("tax assessment", "tax return", "vat", "corporation tax")),
            SeedValue(
                "vehicle-service",
                "Vehicle service",
                ("auto repair", "car service", "vehicle maintenance", "oil change", "roadworthiness"),
            ),
            SeedValue("ev-charging", "EV charging", ("charging", "electric vehicle charging")),
            SeedValue("insurance", "Insurance", ("premium", "policy", "cover")),
            SeedValue("healthcare", "Healthcare", ("medical", "dental", "dentist", "treatment")),
            SeedValue(
                "software", "Software", ("saas", "subscription software", "cloud services", "api")
            ),
            SeedValue("energy", "Energy", ("electricity", "gas", "utilities", "utility bill")),
            SeedValue("housing", "Housing", ("property maintenance", "real estate", "installation")),
            SeedValue("parking", "Parking", ("parking session",)),
            SeedValue("fines", "Fines", ("traffic fine", "penalty", "parking violation")),
            SeedValue("pension", "Pension", ("retirement", "portfolio")),
            SeedValue("banking", "Banking", ("bank charges", "money transfer")),
            SeedValue("travel", "Travel", ("accommodation", "booking", "camping")),
        ),
    ),
    SeedFacet(
        key="scope",
        label="Scope",
        ordinal=1,
        values=(
            SeedValue("business", "Business", ("company", "work")),
            SeedValue("personal", "Personal", ("private", "household", "family")),
        ),
    ),
    SeedFacet(
        key="cost_type",
        label="Cost type",
        ordinal=2,
        values=(
            SeedValue("subscription", "Subscription", ("recurring plan", "monthly plan")),
            SeedValue("usage", "Usage", ("metered", "credits", "pay as you go")),
            SeedValue("one-off", "One-off", ("one time", "single purchase")),
        ),
    ),
    SeedFacet(key="vehicle", label="Vehicle", ordinal=3, values=()),
    SeedFacet(key="property", label="Property", ordinal=4, values=()),
    SeedFacet(key="person", label="Person", ordinal=5, values=()),
)


async def seed_vocabulary(session: AsyncSession) -> int:
    """Create any missing seed facets, values and aliases. Returns values created.

    Additive and idempotent: it never updates or deletes, so a value the owner
    has renamed or a facet they have extended survives a re-seed untouched.
    """
    created = 0
    for seed_facet in SEED_VOCABULARY:
        facet_id = (
            await session.execute(select(Facet.id).where(Facet.key == seed_facet.key))
        ).scalar_one_or_none()
        if facet_id is None:
            facet = Facet(key=seed_facet.key, label=seed_facet.label, ordinal=seed_facet.ordinal)
            session.add(facet)
            await session.flush()
            facet_id = facet.id
        for ordinal, seed_value in enumerate(seed_facet.values):
            value_id = (
                await session.execute(
                    select(FacetValue.id).where(
                        FacetValue.facet_id == facet_id, FacetValue.key == seed_value.key
                    )
                )
            ).scalar_one_or_none()
            if value_id is not None:
                continue
            value = FacetValue(
                facet_id=facet_id, key=seed_value.key, label=seed_value.label, ordinal=ordinal
            )
            session.add(value)
            await session.flush()
            created += 1
            for alias in seed_value.aliases:
                session.add(FacetValueAlias(facet_value_id=value.id, alias=alias))
    return created
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_seed.py -v`
Expected: 4 passed.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/library/facets/seed.py tests/test_facet_seed.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/seed.py tests/test_facet_seed.py
git commit -m "feat(facets): the shipped seed vocabulary"
```

---

### Task 4: Vocabulary CRUD — create, rename, alias, merge, delete

**Files:**
- Modify: `src/library/facets/vocabulary.py`
- Test: `tests/test_facet_crud.py`

**Interfaces:**
- Consumes: Task 2's `_resolve`, `UnknownFacetError`, `UnknownValueError`.
- Produces:
  - `async create_facet(session, key: str, label: str, ordinal: int = 0) -> int`
  - `async create_value(session, facet_key: str, key: str, label: str) -> int`
  - `async rename_value(session, facet_key: str, key: str, label: str) -> None`
  - `async add_alias(session, facet_key: str, key: str, alias: str) -> None`
  - `async merge_values(session, facet_key: str, from_key: str, into_key: str) -> int` — returns documents relabelled
  - `async delete_value(session, facet_key: str, key: str) -> None` — raises `ValueInUseError`
  - `ValueInUseError(ValueError)`

Merging repoints every `document_labels` row from the old value to the new one.
No primary-key conflict is possible: the key is `(document_id, facet_id)` and a
merge changes only `facet_value_id`, which is not part of it. Do **not** add
collision handling here — it would be unreachable code.

**Splitting a value is deliberately not an operation here.** Spec §7.5 lists it,
and it is achieved by composing what this task provides: `create_value` for the
new value, then `library label-archive --relabel` (Task 7) to re-decide the
affected documents. A dedicated `split_value` would have to guess which
documents move, which is exactly the judgement the labeller exists to make.
Record this in `docs/facets.md` (Task 14) so the composition is discoverable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_crud.py`:

```python
"""Vocabulary edits. Renaming is free; merging must survive a label collision."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.vocabulary import (
    ValueInUseError,
    add_alias,
    create_facet,
    create_value,
    delete_value,
    document_labels,
    load_vocabulary,
    merge_values,
    rename_value,
    set_document_label,
)

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _facet_key() -> str:
    return f"crud-{uuid.uuid4().hex[:8]}"


def test_rename_changes_the_label_and_keeps_the_key(api_database_url: str) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await rename_value(session, key, "alpha", "Renamed")

    asyncio.run(_run(api_database_url, _work))
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert facets[key].value("alpha").label == "Renamed"


def test_merge_repoints_labels_and_keeps_the_old_key_as_an_alias(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> int:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await create_value(session, key, "beta", "Beta")
        await set_document_label(session, seeded_document_id, key, "alpha")
        return await merge_values(session, key, "alpha", "beta")

    moved = asyncio.run(_run(api_database_url, _work))
    assert moved == 1
    labels = asyncio.run(
        _run(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert labels[key] == "beta"
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert facets[key].value("alpha") is None
    assert "alpha" in facets[key].value("beta").aliases


def test_deleting_a_value_in_use_is_refused(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await set_document_label(session, seeded_document_id, key, "alpha")

    asyncio.run(_run(api_database_url, _work))
    with pytest.raises(ValueInUseError):
        asyncio.run(_run(api_database_url, lambda s: delete_value(s, key, "alpha")))


def test_an_alias_is_visible_on_the_value(api_database_url: str) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await add_alias(session, key, "alpha", "a-plate-or-misspelling")

    asyncio.run(_run(api_database_url, _work))
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert "a-plate-or-misspelling" in facets[key].value("alpha").aliases
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_crud.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_facet'`.

- [ ] **Step 3: Write the implementation**

Append to `src/library/facets/vocabulary.py`:

```python
class ValueInUseError(ValueError):
    """Raised when deleting a facet value that documents still carry."""


async def create_facet(session: AsyncSession, key: str, label: str, ordinal: int = 0) -> int:
    facet = Facet(key=key, label=label, ordinal=ordinal)
    session.add(facet)
    await session.flush()
    return facet.id


async def create_value(session: AsyncSession, facet_key: str, key: str, label: str) -> int:
    facet_id, _ = await _resolve(session, facet_key, None)
    ordinal = (
        await session.execute(
            select(func.coalesce(func.max(FacetValue.ordinal), -1) + 1).where(
                FacetValue.facet_id == facet_id
            )
        )
    ).scalar_one()
    value = FacetValue(facet_id=facet_id, key=key, label=label, ordinal=ordinal)
    session.add(value)
    await session.flush()
    return value.id


async def rename_value(session: AsyncSession, facet_key: str, key: str, label: str) -> None:
    """Change a value's display label. Free: labels reference the id, not the text."""
    _, value_id = await _resolve(session, facet_key, key)
    await session.execute(
        update(FacetValue).where(FacetValue.id == value_id).values(label=label)
    )


async def add_alias(session: AsyncSession, facet_key: str, key: str, alias: str) -> None:
    _, value_id = await _resolve(session, facet_key, key)
    await session.execute(
        pg_insert(FacetValueAlias)
        .values(facet_value_id=value_id, alias=alias)
        .on_conflict_do_nothing()
    )


async def merge_values(session: AsyncSession, facet_key: str, from_key: str, into_key: str) -> int:
    """Fold ``from_key`` into ``into_key``. Returns the number of labels moved.

    No primary-key conflict is possible: the key is ``(document_id, facet_id)``
    and this changes only ``facet_value_id``, which is not part of it.
    ``from_key`` survives as an alias of the target so a future labelling pass
    still recognises the old surface form.
    """
    facet_id, from_id = await _resolve(session, facet_key, from_key)
    _, into_id = await _resolve(session, facet_key, into_key)

    moved = (
        await session.execute(
            update(DocumentLabel)
            .where(DocumentLabel.facet_id == facet_id, DocumentLabel.facet_value_id == from_id)
            .values(facet_value_id=into_id)
        )
    ).rowcount

    await session.execute(
        pg_insert(FacetValueAlias)
        .values(facet_value_id=into_id, alias=from_key)
        .on_conflict_do_nothing()
    )
    await session.execute(
        update(FacetValueAlias)
        .where(FacetValueAlias.facet_value_id == from_id)
        .values(facet_value_id=into_id)
    )
    await session.execute(delete(FacetValue).where(FacetValue.id == from_id))
    return int(moved)


async def delete_value(session: AsyncSession, facet_key: str, key: str) -> None:
    """Remove an unused value. Refuses while any document still carries it."""
    facet_id, value_id = await _resolve(session, facet_key, key)
    in_use = (
        await session.execute(
            select(func.count())
            .select_from(DocumentLabel)
            .where(DocumentLabel.facet_id == facet_id, DocumentLabel.facet_value_id == value_id)
        )
    ).scalar_one()
    if in_use:
        raise ValueInUseError(f"{facet_key}={key} is on {in_use} documents")
    await session.execute(delete(FacetValueAlias).where(FacetValueAlias.facet_value_id == value_id))
    await session.execute(delete(FacetValue).where(FacetValue.id == value_id))
```

Extend the module's imports to `from sqlalchemy import delete, func, select, update`.

Add the new names to `src/library/facets/__init__.py`'s import list and `__all__`:
`ValueInUseError`, `add_alias`, `create_facet`, `create_value`, `delete_value`, `merge_values`, `rename_value`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_crud.py -v`
Expected: 4 passed.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/library/facets/ tests/test_facet_crud.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/ tests/test_facet_crud.py
git commit -m "feat(facets): vocabulary CRUD with merge-collision handling"
```

---

### Task 5: The labeller — prompt and closed-set parsing

**Files:**
- Create: `src/library/facets/labeller.py`
- Modify: `src/library/config.py`
- Test: `tests/test_facet_labeller.py`

**Interfaces:**
- Consumes: `VocabularyFacet`, `VocabularyValue` from Task 2.
- Produces:
  - `LabelProposal(facet_key: str, value_key: str | None, confidence: float, reason: str, suggested_label: str | None)`
  - `DocumentFields(title: str | None, summary: str | None, sender: str | None, kind: str | None, amount: str | None, currency: str | None, excerpt: str | None)`
  - `LABELLER_SYSTEM_PROMPT: str`
  - `build_labelling_prompt(vocabulary: Sequence[VocabularyFacet], fields: DocumentFields) -> str`
  - `parse_label_response(payload: str, vocabulary: Sequence[VocabularyFacet]) -> list[LabelProposal]`
  - `async label_document(settings, vocabulary, fields, *, client=None, backend="api") -> tuple[list[LabelProposal], int, int] | None`
- Adds setting `facet_label_min_confidence: float = 0.6`. **Not** a `*_model` setting, so no `MODEL_PRICING_USD_PER_MTOK` row is required; the model used is the existing `settings.extraction_model`.

`parse_label_response` is pure and is where the closed set is enforced — it is
the unit that must be tested hardest, and it needs no model to test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_labeller.py`:

```python
"""Prompt construction and closed-set parsing. No network, no database."""

import json

from library.facets.labeller import (
    DocumentFields,
    build_labelling_prompt,
    parse_label_response,
)
from library.facets.vocabulary import VocabularyFacet, VocabularyValue

VOCAB: tuple[VocabularyFacet, ...] = (
    VocabularyFacet(
        id=1,
        key="category",
        label="Category",
        ordinal=0,
        values=(
            VocabularyValue(id=10, key="software", label="Software", parent_id=None,
                            aliases=("saas",)),
            VocabularyValue(id=11, key="energy", label="Energy", parent_id=None, aliases=()),
        ),
    ),
    VocabularyFacet(
        id=2,
        key="scope",
        label="Scope",
        ordinal=1,
        values=(
            VocabularyValue(id=20, key="business", label="Business", parent_id=None, aliases=()),
        ),
    ),
)

FIELDS = DocumentFields(
    title="Monthly plan invoice",
    summary="A recurring charge for a hosted tool.",
    sender="Vendor",
    kind="invoice",
    amount="48.00",
    currency="EUR",
    excerpt="Plan renewal. Amount due 48.00 EUR.",
)


def test_prompt_lists_every_allowed_value_and_its_aliases() -> None:
    prompt = build_labelling_prompt(VOCAB, FIELDS)
    assert "category" in prompt and "software" in prompt and "energy" in prompt
    assert "saas" in prompt
    assert "scope" in prompt and "business" in prompt
    assert "Monthly plan invoice" in prompt


def test_a_value_inside_the_vocabulary_is_accepted() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "category", "value": "software", "confidence": 0.9,
                     "reason": "a hosted tool subscription"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert len(proposals) == 1
    assert proposals[0].facet_key == "category"
    assert proposals[0].value_key == "software"
    assert proposals[0].suggested_label is None


def test_a_value_outside_the_vocabulary_becomes_unknown_plus_a_suggestion() -> None:
    """The closed-set guarantee. The model cannot widen the vocabulary by naming."""
    payload = json.dumps(
        {"labels": [{"facet": "category", "value": "telecoms", "confidence": 0.95,
                     "reason": "a phone bill"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key is None
    assert proposals[0].suggested_label == "telecoms"


def test_an_explicit_unknown_carries_its_suggestion() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "category", "value": None, "confidence": 0.2,
                     "reason": "cannot tell", "suggest": "telecoms"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key is None
    assert proposals[0].suggested_label == "telecoms"


def test_an_unknown_facet_is_discarded_entirely() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "not_a_facet", "value": "x", "confidence": 1.0, "reason": "no"}]}
    )
    assert parse_label_response(payload, VOCAB) == []


def test_an_alias_resolves_to_its_value() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "category", "value": "saas", "confidence": 0.8, "reason": "alias"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key == "software"


def test_malformed_json_yields_no_proposals_rather_than_raising() -> None:
    assert parse_label_response("not json at all", VOCAB) == []


def test_confidence_is_clamped_into_zero_one() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "scope", "value": "business", "confidence": 4.2, "reason": "x"}]}
    )
    assert parse_label_response(payload, VOCAB)[0].confidence == 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_labeller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets.labeller'`.

- [ ] **Step 3: Add the setting**

In `src/library/config.py`, beside the other feature settings (near `series_min_documents`):

```python
    # Below this, a label is stored as `unknown` and queued for review rather
    # than applied. A confidently wrong label silently moves money between
    # charts, so the default is deliberately cautious.
    facet_label_min_confidence: float = 0.6
```

- [ ] **Step 4: Write the implementation**

Create `src/library/facets/labeller.py`:

```python
"""Assign facet values to one document, choosing only from the closed vocabulary.

The model never widens the vocabulary. ``parse_label_response`` maps anything it
returns that is not an allowed value (or an alias of one) onto ``unknown`` plus a
*suggestion*, which Task 6 queues for approval. That mapping is pure, so the
guarantee is tested without a model.

Uses ``settings.extraction_model`` rather than a setting of its own: every
``*_model`` setting needs a matching row in ``MODEL_PRICING_USD_PER_MTOK`` or the
app refuses to boot.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from library.config import LLMBackend, Settings
from library.facets.vocabulary import VocabularyFacet
from library.llm import subscription

logger = logging.getLogger(__name__)

MAX_LABEL_TOKENS: int = 600
MAX_EXCERPT_CHARS: int = 2000

LABELLER_SYSTEM_PROMPT: str = """\
You assign labels to a household document for "Library", a self-hosted family
document archive.

You are given a CLOSED vocabulary of facets. Each facet is one dimension, and a
document takes AT MOST ONE value per facet. You may only choose values that
appear in the vocabulary; aliases listed beside a value also identify it.

If no listed value fits a facet, return "value": null for that facet and put the
label you WOULD have wanted in "suggest". Never invent a value in the "value"
field. Omit a facet entirely when it does not apply to this document.

"confidence" is your confidence in that single value, from 0 to 1. Be honest:
a low confidence sends the document to a human, which is the correct outcome
when you are unsure.

Return ONLY a JSON object of this shape, with no prose or code fences:
{"labels": [{"facet": "...", "value": "..."|null, "confidence": 0.0,
             "reason": "one short clause", "suggest": "..."|null}]}"""


@dataclass(frozen=True, slots=True)
class DocumentFields:
    """The document facts the labeller is allowed to see."""

    title: str | None
    summary: str | None
    sender: str | None
    kind: str | None
    amount: str | None
    currency: str | None
    excerpt: str | None


@dataclass(frozen=True, slots=True)
class LabelProposal:
    """One facet's proposed value. ``value_key is None`` means unknown."""

    facet_key: str
    value_key: str | None
    confidence: float
    reason: str
    suggested_label: str | None


def build_labelling_prompt(
    vocabulary: Sequence[VocabularyFacet], fields: DocumentFields
) -> str:
    lines: list[str] = ["VOCABULARY (choose only from these):"]
    for facet in vocabulary:
        lines.append(f"- {facet.key} ({facet.label}):")
        if not facet.values:
            lines.append("    (no values yet — return null and suggest one if it applies)")
        for value in facet.values:
            alias_note = f"  [also: {', '.join(value.aliases)}]" if value.aliases else ""
            lines.append(f"    {value.key} — {value.label}{alias_note}")
    excerpt = (fields.excerpt or "")[:MAX_EXCERPT_CHARS]
    lines += [
        "",
        "DOCUMENT:",
        f"Sender: {fields.sender}",
        f"Kind: {fields.kind}",
        f"Title: {fields.title}",
        f"Summary: {fields.summary}",
        f"Amount: {fields.amount} {fields.currency}",
        f"Text excerpt: {excerpt}",
    ]
    return "\n".join(lines)


def parse_label_response(
    payload: str, vocabulary: Sequence[VocabularyFacet]
) -> list[LabelProposal]:
    """Map a model response onto proposals, enforcing the closed set.

    Never raises: a malformed response yields no proposals, which leaves the
    document unlabelled and visible in the review queue rather than failing the
    whole labelling run.
    """
    try:
        parsed = json.loads(payload)
        entries = parsed["labels"]
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("facet labeller returned an unparseable payload")
        return []
    if not isinstance(entries, list):
        return []

    by_key = {facet.key: facet for facet in vocabulary}
    proposals: list[LabelProposal] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        facet_key = entry.get("facet")
        if not isinstance(facet_key, str):
            # A non-string facet cannot name a facet, and would raise
            # TypeError as an unhashable dict key. Discard the entry.
            continue
        facet = by_key.get(facet_key)
        if facet is None:
            continue  # an invented facet is discarded outright
        raw_value = entry.get("value")
        resolved: str | None = None
        if isinstance(raw_value, str):
            match = facet.value(raw_value)
            if match is None:
                match = next(
                    (v for v in facet.values if raw_value in v.aliases), None
                )
            resolved = match.key if match is not None else None
        suggested = entry.get("suggest")
        if resolved is None and suggested is None and isinstance(raw_value, str):
            # The model named a value outside the vocabulary: keep it as the
            # suggestion rather than discarding what it was trying to say.
            suggested = raw_value
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        reason = entry.get("reason")
        proposals.append(
            LabelProposal(
                facet_key=facet.key,
                value_key=resolved,
                confidence=min(1.0, max(0.0, confidence)),
                reason=reason if isinstance(reason, str) else "",
                suggested_label=suggested if isinstance(suggested, str) else None,
            )
        )
    return proposals


async def label_document(
    settings: Settings,
    vocabulary: Sequence[VocabularyFacet],
    fields: DocumentFields,
    *,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> tuple[list[LabelProposal], int, int] | None:
    """``(proposals, input_tokens, output_tokens)``, or None when unrunnable.

    Mirrors ``series_insight.describe_series``: a missing API key is a quiet
    ``None`` (the caller skips the document) rather than an error.
    """
    prompt = build_labelling_prompt(vocabulary, fields)
    if backend == "subscription":
        result = await subscription.text_call(
            config_dir=settings.claude_config_dir,
            model=settings.extraction_model,
            system_prompt=LABELLER_SYSTEM_PROMPT,
            prompt=prompt,
        )
        return (
            parse_label_response(result.text, vocabulary),
            result.usage.input_tokens,
            result.usage.output_tokens,
        )

    async def _call(anthropic: AsyncAnthropic) -> tuple[list[LabelProposal], int, int]:
        response = await anthropic.messages.create(
            model=settings.extraction_model,
            max_tokens=MAX_LABEL_TOKENS,
            system=LABELLER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        return (
            parse_label_response(text, vocabulary),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    if client is not None:
        return await _call(client)
    if settings.anthropic_api_key is None:
        return None
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as owned:
        return await _call(owned)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_labeller.py -v`
Expected: 8 passed.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/library/facets/labeller.py src/library/config.py tests/test_facet_labeller.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/labeller.py src/library/config.py tests/test_facet_labeller.py
git commit -m "feat(facets): closed-set labeller with suggestion fallback"
```

---

### Task 6: Apply proposals — labels, unknowns, and the suggestion queue

**Files:**
- Create: `src/library/facets/apply.py`
- Test: `tests/test_facet_apply.py`

**Interfaces:**
- Consumes: `set_document_label`, `load_vocabulary` (Task 2); `LabelProposal`, `DocumentFields`, `label_document` (Task 5); `settings.facet_label_min_confidence`.
- Produces:
  - `LabellingOutcome(document_id: int, applied: dict[str, str], unknown: tuple[str, ...], suggested: tuple[tuple[str, str], ...])`
  - `async apply_proposals(session, document_id: int, proposals: Sequence[LabelProposal], *, min_confidence: float) -> LabellingOutcome`
  - `async document_fields(session, document_id: int) -> DocumentFields | None`
  - `async label_and_apply(session, settings, document_id: int, *, client=None, backend="api") -> LabellingOutcome | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_apply.py`:

```python
"""Turning proposals into rows: what is applied, what is withheld, what is queued."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.apply import apply_proposals
from library.facets.labeller import LabelProposal
from library.facets.vocabulary import create_facet, create_value, document_labels
from library.models import FacetValueSuggestion

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _setup(database_url: str) -> str:
    key = f"apply-{uuid.uuid4().hex[:8]}"

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Apply")
        await create_value(session, key, "alpha", "Alpha")

    asyncio.run(_run(database_url, _work))
    return key


def test_a_confident_proposal_is_applied(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "alpha", 0.9, "clear", None)]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {key: "alpha"}
    labels = asyncio.run(_run(api_database_url, lambda s: document_labels(s, seeded_document_id)))
    assert labels[key] == "alpha"


def test_a_low_confidence_proposal_is_withheld_not_guessed(
    api_database_url: str, seeded_document_id: int
) -> None:
    """A confidently wrong label silently moves money between charts. Withhold."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "alpha", 0.2, "unsure", None)]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {}
    assert key in outcome.unknown
    labels = asyncio.run(_run(api_database_url, lambda s: document_labels(s, seeded_document_id)))
    assert key not in labels


def test_a_suggestion_is_queued_and_no_value_is_created(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, None, 0.95, "a new idea", "telecoms")]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.suggested == ((key, "telecoms"),)
    rows = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(
                select(FacetValueSuggestion).where(
                    FacetValueSuggestion.document_id == seeded_document_id
                )
            ),
        )
    )
    assert [r.suggested_label for r in rows.scalars()] == ["telecoms"]


def test_applying_twice_is_idempotent(
    api_database_url: str, seeded_document_id: int
) -> None:
    """Re-labelling must not raise on the suggestion unique constraint."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, None, 0.9, "again", "telecoms")]
    for _ in range(2):
        asyncio.run(
            _run(
                api_database_url,
                lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
            )
        )
    rows = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(
                select(FacetValueSuggestion).where(
                    FacetValueSuggestion.document_id == seeded_document_id
                )
            ),
        )
    )
    assert len(list(rows.scalars())) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_apply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets.apply'`.

- [ ] **Step 3: Write the implementation**

Create `src/library/facets/apply.py`:

```python
"""The only module that both calls the model and writes to the database.

Three outcomes per facet, and the split between them is the point: a confident
in-vocabulary value is applied; anything below the confidence floor is withheld
and reported as unknown; a value the model wanted but the vocabulary lacks is
queued as a suggestion. Nothing here can create a facet value.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.facets.labeller import DocumentFields, LabelProposal, label_document
from library.facets.vocabulary import (
    UnknownValueError,
    load_vocabulary,
    set_document_label,
)
from library.models import Document, Facet, FacetValueSuggestion, Kind, Sender

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabellingOutcome:
    document_id: int
    applied: dict[str, str]
    unknown: tuple[str, ...]
    suggested: tuple[tuple[str, str], ...]


async def apply_proposals(
    session: AsyncSession,
    document_id: int,
    proposals: Sequence[LabelProposal],
    *,
    min_confidence: float,
) -> LabellingOutcome:
    applied: dict[str, str] = {}
    unknown: list[str] = []
    suggested: list[tuple[str, str]] = []

    for proposal in proposals:
        if proposal.suggested_label:
            facet_id = (
                await session.execute(select(Facet.id).where(Facet.key == proposal.facet_key))
            ).scalar_one_or_none()
            if facet_id is not None:
                await session.execute(
                    pg_insert(FacetValueSuggestion)
                    .values(
                        facet_id=facet_id,
                        document_id=document_id,
                        suggested_label=proposal.suggested_label,
                        reason=proposal.reason,
                        state="pending",
                    )
                    .on_conflict_do_nothing(constraint="facet_value_suggestions_unique")
                )
                suggested.append((proposal.facet_key, proposal.suggested_label))

        if proposal.value_key is None or proposal.confidence < min_confidence:
            unknown.append(proposal.facet_key)
            continue
        try:
            await set_document_label(
                session, document_id, proposal.facet_key, proposal.value_key
            )
        except UnknownValueError:
            # The vocabulary changed between parsing and writing. Treat as
            # unknown rather than failing the run.
            unknown.append(proposal.facet_key)
            continue
        applied[proposal.facet_key] = proposal.value_key

    return LabellingOutcome(
        document_id=document_id,
        applied=applied,
        unknown=tuple(unknown),
        suggested=tuple(suggested),
    )


async def document_fields(session: AsyncSession, document_id: int) -> DocumentFields | None:
    """The facts the labeller may see. None when the document does not exist."""
    row = (
        await session.execute(
            select(
                Document.title,
                Document.summary,
                Sender.name,
                Kind.slug,
                Document.amount_total,
                Document.currency,
                Document.ocr_text,
            )
            .outerjoin(Sender, Sender.id == Document.sender_id)
            .outerjoin(Kind, Kind.id == Document.kind_id)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        return None
    title, summary, sender, kind, amount, currency, ocr_text = row
    return DocumentFields(
        title=title,
        summary=summary,
        sender=sender,
        kind=kind,
        amount=str(amount) if amount is not None else None,
        currency=currency,
        excerpt=ocr_text,
    )


async def label_and_apply(
    session: AsyncSession,
    settings: Settings,
    document_id: int,
    *,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> LabellingOutcome | None:
    """Label one document end to end. None when the document or the model is absent."""
    fields = await document_fields(session, document_id)
    if fields is None:
        return None
    vocabulary = await load_vocabulary(session)
    result = await label_document(
        settings, vocabulary, fields, client=client, backend=backend
    )
    if result is None:
        return None
    proposals, _input_tokens, _output_tokens = result
    return await apply_proposals(
        session,
        document_id,
        proposals,
        min_confidence=settings.facet_label_min_confidence,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_apply.py -v`
Expected: 4 passed.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/library/facets/apply.py tests/test_facet_apply.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/apply.py tests/test_facet_apply.py
git commit -m "feat(facets): apply proposals, withhold unsure ones, queue suggestions"
```

---

### Task 7: Backfill CLI and the ingest hook

**Files:**
- Modify: `src/library/cli.py`, `src/library/extraction/apply.py`
- Test: `tests/test_facet_backfill.py`

**Interfaces:**
- Consumes: `label_and_apply`, `seed_vocabulary`.
- Produces: CLI command `library label-archive [--limit N] [--only ID] [--relabel]`; a call to `label_and_apply` inside `extraction.apply`'s success path.

`--relabel` re-labels documents that already carry labels; without it, documents
with at least one label are skipped, which is what makes the command safe to
re-run after adding a facet.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_backfill.py`:

```python
"""The backfill selects the right documents. The model itself is stubbed."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.backfill import documents_needing_labels
from library.facets.vocabulary import create_facet, create_value, set_document_label

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def test_an_unlabelled_document_is_selected(
    api_database_url: str, seeded_document_id: int
) -> None:
    ids = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    assert seeded_document_id in ids


def test_a_labelled_document_is_skipped_unless_relabelling(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"backfill-{uuid.uuid4().hex[:8]}"

    async def _label(session: AsyncSession) -> None:
        await create_facet(session, key, "Backfill")
        await create_value(session, key, "alpha", "Alpha")
        await set_document_label(session, seeded_document_id, key, "alpha")

    asyncio.run(_run(api_database_url, _label))

    skipped = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    included = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=True, limit=None))
    )
    assert seeded_document_id not in skipped
    assert seeded_document_id in included
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets.backfill'`.

- [ ] **Step 3: Write the selection helper**

Create `src/library/facets/backfill.py`:

```python
"""Which documents a labelling run should touch, and the run itself."""

from __future__ import annotations

import logging

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.facets.apply import label_and_apply
from library.models import Document, DocumentLabel

logger = logging.getLogger(__name__)


async def documents_needing_labels(
    session: AsyncSession, *, relabel: bool, limit: int | None
) -> list[int]:
    """Non-deleted document ids to label, oldest first.

    Without ``relabel``, a document carrying ANY label is skipped, which is what
    makes the command safe to re-run after adding a facet: it picks up only what
    has never been labelled.
    """
    has_label = exists().where(DocumentLabel.document_id == Document.id)
    statement = select(Document.id).where(Document.deleted_at.is_(None)).order_by(Document.id)
    if not relabel:
        statement = statement.where(~has_label)
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.execute(statement)).scalars())


async def run_backfill(
    session: AsyncSession, settings: Settings, *, relabel: bool, limit: int | None
) -> tuple[int, int]:
    """Label each selected document. Returns ``(labelled, skipped)``.

    Each document commits on its own so a failure part-way leaves the work
    already done in place; the command is re-runnable by design.
    """
    ids = await documents_needing_labels(session, relabel=relabel, limit=limit)
    labelled = skipped = 0
    for document_id in ids:
        outcome = await label_and_apply(session, settings, document_id)
        if outcome is None:
            skipped += 1
            continue
        await session.commit()
        labelled += 1
    return labelled, skipped
```

- [ ] **Step 4: Add the CLI command**

In `src/library/cli.py`, beside the other top-level commands:

```python
@app.command("label-archive")
def label_archive(
    limit: int = typer.Option(0, "--limit", help="Stop after this many documents (0 = all)."),
    only: int = typer.Option(0, "--only", help="Label just this document id."),
    relabel: bool = typer.Option(
        False, "--relabel", help="Also re-label documents that already carry labels."
    ),
) -> None:
    """Seed the facet vocabulary if needed, then label documents that lack labels."""
    from library.config import get_settings
    from library.facets.apply import label_and_apply
    from library.facets.backfill import run_backfill
    from library.facets.seed import seed_vocabulary

    settings = get_settings()

    async def _operation(session: AsyncSession) -> tuple[int, int]:
        created = await seed_vocabulary(session)
        await session.commit()
        if created:
            typer.echo(f"seeded {created} facet values")
        if only:
            outcome = await label_and_apply(session, settings, only)
            if outcome is None:
                return 0, 1
            await session.commit()
            typer.echo(f"document {only}: {outcome.applied}")
            return 1, 0
        return await run_backfill(
            session, settings, relabel=relabel, limit=limit or None
        )

    labelled, skipped = _run(_operation)
    typer.echo(f"labelled {labelled}, skipped {skipped}")
```

- [ ] **Step 5: Hook new documents at ingest**

In `src/library/extraction/apply.py`, at the end of `_apply_outcome`'s success path
(after `_apply_validation` is called), add:

```python
    # Label the document against the controlled vocabulary. Best-effort and
    # self-contained, exactly like the validation step above: a missing API key
    # or an unparseable response leaves the document unlabelled and visible in
    # the review queue, never fails the ingest.
    try:
        from library.facets.apply import label_and_apply

        await label_and_apply(session, settings, document.id)
    except Exception:  # noqa: BLE001 - ingest must not fail on a labelling error
        logger.exception("facet labelling failed for document %s", document.id)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_backfill.py -v`
Expected: 2 passed.

- [ ] **Step 7: Verify the CLI renders**

Run: `uv run library label-archive --help`
Expected: the command's options are listed. Note that `--help` is rendered by
`rich` and carries ANSI styling; if you assert on this output in a test, strip
escape codes first — `NO_COLOR` suppresses colour but not bold or dim.

- [ ] **Step 8: Format and commit**

```bash
uv run ruff format src/library/facets/ src/library/cli.py src/library/extraction/apply.py tests/test_facet_backfill.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/backfill.py src/library/cli.py src/library/extraction/apply.py tests/test_facet_backfill.py
git commit -m "feat(facets): label-archive backfill command and ingest hook"
```

---

### Task 8: REST surface for the vocabulary, labels and suggestions

**Files:**
- Create: `src/library/api/facets.py`
- Modify: `src/library/app.py` (register the router beside the others, around line 258)
- Test: `tests/test_api_facets.py`

**Interfaces:**
- Consumes: everything from Tasks 2 and 4; `FacetValueSuggestion` from Task 1.
- Produces these routes (authentication is enforced at include level in `app.py`, like every other router):

```
GET    /api/facets                                     the whole vocabulary
POST   /api/facets/{facet_key}/values                  {key,label} -> 201
PATCH  /api/facets/{facet_key}/values/{value_key}      {label}
POST   /api/facets/{facet_key}/values/{value_key}/aliases   {alias}
POST   /api/facets/{facet_key}/values/{value_key}/merge     {into} -> {moved}
DELETE /api/facets/{facet_key}/values/{value_key}      409 when in use
GET    /api/documents/{document_id}/labels             {facet_key: value_key}
PUT    /api/documents/{document_id}/labels             {facet_key: value_key|null}
GET    /api/facet-suggestions                          pending suggestions
POST   /api/facet-suggestions/{id}/accept              creates the value, labels the doc
POST   /api/facet-suggestions/{id}/dismiss
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_facets.py`:

```python
"""The facet REST surface, exercised through the app."""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _make_facet(api_client: TestClient) -> str:
    """Seed the shipped vocabulary, then return a fresh facet key to work in."""
    key = f"api-{uuid.uuid4().hex[:8]}"
    response = api_client.post("/api/facets", json={"key": key, "label": "Api"})
    assert response.status_code == 201, response.text
    return key


def test_the_vocabulary_lists_facets_and_values(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    assert api_client.post(
        f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"}
    ).status_code == 201
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert [v["key"] for v in facet["values"]] == ["alpha"]


def test_setting_and_reading_a_documents_labels(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    put = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}}
    )
    assert put.status_code == 200, put.text
    assert api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key] == "alpha"


def test_a_null_clears_a_label(api_client: TestClient, seeded_document_id: int) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: None}})
    assert key not in api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"]


def test_an_unknown_value_is_rejected_with_422_not_created(
    api_client: TestClient, seeded_document_id: int
) -> None:
    """The closed set holds at the API boundary too, not only in the labeller."""
    key = _make_facet(api_client)
    response = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "invented"}}
    )
    assert response.status_code == 422
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert facet["values"] == []


def test_deleting_a_value_in_use_returns_409(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    assert api_client.delete(f"/api/facets/{key}/values/alpha").status_code == 409


def test_merge_moves_labels_and_reports_the_count(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(f"/api/facets/{key}/values/alpha/merge", json={"into": "beta"})
    assert response.status_code == 200
    assert response.json()["moved"] == 1
    assert api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key] == "beta"


def test_anonymous_access_is_refused(anon_client: TestClient) -> None:
    assert anon_client.get("/api/facets").status_code in (401, 403)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_facets.py -v`
Expected: FAIL — every route 404s.

- [ ] **Step 3: Write the router**

Create `src/library/api/facets.py`:

```python
"""Facet vocabulary and document-label endpoints.

The closed-set rule is enforced here as well as in the labeller: a PUT naming a
value that does not exist is a 422, never an implicit create. Authentication is
enforced at include level in app.py, like every other router.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.facets import vocabulary
from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    ValueInUseError,
)
from library.models import Facet, FacetValueSuggestion

router: APIRouter = APIRouter(tags=["facets"])

Key = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class ValueOut(BaseModel):
    key: str
    label: str
    parent_id: int | None
    aliases: list[str]


class FacetOut(BaseModel):
    key: str
    label: str
    ordinal: int
    values: list[ValueOut]


class VocabularyOut(BaseModel):
    facets: list[FacetOut]


class FacetCreate(BaseModel):
    key: Key
    label: Label
    ordinal: int = 0


class ValueCreate(BaseModel):
    key: Key
    label: Label


class ValueRename(BaseModel):
    label: Label


class AliasCreate(BaseModel):
    alias: Annotated[str, StringConstraints(min_length=1, max_length=255)]


class MergeRequest(BaseModel):
    into: Key


class LabelsBody(BaseModel):
    labels: dict[str, str | None] = Field(
        description="facet key -> value key; null clears that facet's label."
    )


@router.get("/facets", response_model=VocabularyOut, summary="The whole facet vocabulary")
async def list_facets(session: Annotated[AsyncSession, Depends(get_session)]) -> VocabularyOut:
    facets = await vocabulary.load_vocabulary(session)
    return VocabularyOut(
        facets=[
            FacetOut(
                key=facet.key,
                label=facet.label,
                ordinal=facet.ordinal,
                values=[
                    ValueOut(
                        key=value.key,
                        label=value.label,
                        parent_id=value.parent_id,
                        aliases=list(value.aliases),
                    )
                    for value in facet.values
                ],
            )
            for facet in facets
        ]
    )


@router.post("/facets", status_code=status.HTTP_201_CREATED, summary="Create a facet")
async def create_facet(
    body: FacetCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    await vocabulary.create_facet(session, body.key, body.label, body.ordinal)
    await session.commit()
    return {"key": body.key}


@router.post(
    "/facets/{facet_key}/values",
    status_code=status.HTTP_201_CREATED,
    summary="Add a value to a facet",
)
async def create_value(
    facet_key: str, body: ValueCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    try:
        await vocabulary.create_value(session, facet_key, body.key, body.label)
    except UnknownFacetError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet") from exc
    await session.commit()
    return {"key": body.key}


@router.patch("/facets/{facet_key}/values/{value_key}", summary="Rename a value")
async def rename_value(
    facet_key: str,
    value_key: str,
    body: ValueRename,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    try:
        await vocabulary.rename_value(session, facet_key, value_key, body.label)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
    await session.commit()
    return {"label": body.label}


@router.post("/facets/{facet_key}/values/{value_key}/aliases", summary="Add an alias")
async def add_alias(
    facet_key: str,
    value_key: str,
    body: AliasCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    try:
        await vocabulary.add_alias(session, facet_key, value_key, body.alias)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
    await session.commit()
    return {"alias": body.alias}


@router.post("/facets/{facet_key}/values/{value_key}/merge", summary="Fold one value into another")
async def merge_value(
    facet_key: str,
    value_key: str,
    body: MergeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    try:
        moved = await vocabulary.merge_values(session, facet_key, value_key, body.into)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
    await session.commit()
    return {"moved": moved}


@router.delete(
    "/facets/{facet_key}/values/{value_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused value",
)
async def delete_value(
    facet_key: str, value_key: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    try:
        await vocabulary.delete_value(session, facet_key, value_key)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
    except ValueInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/labels", summary="One document's facet labels")
async def get_labels(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, dict[str, str]]:
    return {"labels": await vocabulary.document_labels(session, document_id)}


@router.put("/documents/{document_id}/labels", summary="Set or clear facet labels")
async def put_labels(
    document_id: int,
    body: LabelsBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, dict[str, str]]:
    for facet_key, value_key in body.labels.items():
        try:
            await vocabulary.set_document_label(session, document_id, facet_key, value_key)
        except UnknownFacetError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown facet: {facet_key}") from exc
        except UnknownValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{facet_key}={value_key} is not in the vocabulary",
            ) from exc
    await session.commit()
    return {"labels": await vocabulary.document_labels(session, document_id)}


@router.get("/facet-suggestions", summary="Values the labeller wanted but could not use")
async def list_suggestions(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict[str, object]]]:
    rows = await session.execute(
        select(FacetValueSuggestion, Facet.key)
        .join(Facet, Facet.id == FacetValueSuggestion.facet_id)
        .where(FacetValueSuggestion.state == "pending")
        .order_by(FacetValueSuggestion.created_at)
        .limit(100)
    )
    return {
        "suggestions": [
            {
                "id": suggestion.id,
                "facet": facet_key,
                "suggested_label": suggestion.suggested_label,
                "reason": suggestion.reason,
                "document_id": suggestion.document_id,
            }
            for suggestion, facet_key in rows.all()
        ]
    }


@router.post("/facet-suggestions/{suggestion_id}/accept", summary="Create the suggested value")
async def accept_suggestion(
    suggestion_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    row = (
        await session.execute(
            select(FacetValueSuggestion, Facet.key)
            .join(Facet, Facet.id == FacetValueSuggestion.facet_id)
            .where(FacetValueSuggestion.id == suggestion_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown suggestion")
    suggestion, facet_key = row
    value_key = suggestion.suggested_label.strip().lower().replace(" ", "-")
    vocab = {f.key: f for f in await vocabulary.load_vocabulary(session)}
    if vocab[facet_key].value(value_key) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{facet_key}={value_key} already exists"
        )
    await vocabulary.create_value(session, facet_key, value_key, suggestion.suggested_label)
    await vocabulary.set_document_label(session, suggestion.document_id, facet_key, value_key)
    suggestion.state = "accepted"
    await session.commit()
    return {"facet": facet_key, "value": value_key}


@router.post("/facet-suggestions/{suggestion_id}/dismiss", summary="Reject a suggestion")
async def dismiss_suggestion(
    suggestion_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    suggestion = await session.get(FacetValueSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown suggestion")
    suggestion.state = "dismissed"
    await session.commit()
    return {"state": "dismissed"}
```

- [ ] **Step 4: Register the router**

In `src/library/app.py`, beside the existing includes (around line 258):

```python
    api_router.include_router(facets.router)
```

and add `facets` to the `from library.api import (...)` import list.

- [ ] **Step 5: Add a dry run to merge**

Spec §7.5 requires a vocabulary edit to show a diff before it is applied. Merge
is the only irreversible one here (delete is blocked while in use; rename and
alias are additive), so it takes a `dry_run` flag:

```python
class MergeRequest(BaseModel):
    into: Key
    dry_run: bool = False
```

and in `merge_value`, before mutating:

```python
    if body.dry_run:
        moved = await vocabulary.count_labels(session, facet_key, value_key)
        return {"moved": moved}
```

Add the counter to `src/library/facets/vocabulary.py`:

```python
async def count_labels(session: AsyncSession, facet_key: str, value_key: str) -> int:
    """How many documents carry this value. The 'diff' a dry-run merge reports."""
    facet_id, value_id = await _resolve(session, facet_key, value_key)
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(DocumentLabel)
                .where(
                    DocumentLabel.facet_id == facet_id,
                    DocumentLabel.facet_value_id == value_id,
                )
            )
        ).scalar_one()
    )
```

Add this test to `tests/test_api_facets.py`:

```python
def test_a_dry_run_merge_reports_the_count_without_moving_anything(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(
        f"/api/facets/{key}/values/alpha/merge", json={"into": "beta", "dry_run": True}
    )
    assert response.json()["moved"] == 1
    # nothing moved: the label and the source value both survive
    assert api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key] == "alpha"
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert "alpha" in {v["key"] for v in facet["values"]}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_facets.py -v`
Expected: 8 passed.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format src/library/api/facets.py src/library/app.py tests/test_api_facets.py
uv run ruff check src/ tests/ migrations/
git add src/library/api/facets.py src/library/app.py tests/test_api_facets.py
git commit -m "feat(facets): REST surface for vocabulary, labels and suggestions"
```

---

### Task 9: Filter documents by facet

**Files:**
- Modify: `src/library/search.py`, `src/library/api/documents.py`
- Test: `tests/test_facet_search.py`

**Interfaces:**
- Consumes: `DocumentLabel`, `Facet`, `FacetValue`.
- Produces: `DocumentFilters.facets: Mapping[str, str]` (default empty), AND-composing with each other and with every existing filter; query parameter `facet` repeated as `facet=category:software&facet=scope:business` on `GET /api/documents`.

Facets AND-compose, unlike projects and matters: a document has one value per
facet, so intersecting two values of the *same* facet returns nothing by
construction, while intersecting *different* facets is the narrowing a user
means by selecting both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facet_search.py`:

```python
"""Facet filtering on the document list. Scoped by a unique facet per test."""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_filtering_by_one_facet_narrows_to_labelled_documents(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = f"srch-{uuid.uuid4().hex[:8]}"
    api_client.post("/api/facets", json={"key": key, "label": "Search"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})

    hit = api_client.get("/api/documents", params={"facet": f"{key}:alpha"}).json()
    miss = api_client.get("/api/documents", params={"facet": f"{key}:beta"}).json()
    assert [d["id"] for d in hit["items"]] == [seeded_document_id]
    assert seeded_document_id not in [d["id"] for d in miss["items"]]


def test_two_facets_and_compose(api_client: TestClient, seeded_document_id: int) -> None:
    a = f"srcha-{uuid.uuid4().hex[:8]}"
    b = f"srchb-{uuid.uuid4().hex[:8]}"
    for key in (a, b):
        api_client.post("/api/facets", json={"key": key, "label": key})
        api_client.post(f"/api/facets/{key}/values", json={"key": "one", "label": "One"})
    api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {a: "one", b: "one"}}
    )
    both = api_client.get(
        "/api/documents", params=[("facet", f"{a}:one"), ("facet", f"{b}:one")]
    ).json()
    assert [d["id"] for d in both["items"]] == [seeded_document_id]


def test_a_malformed_facet_parameter_is_a_422(api_client: TestClient) -> None:
    assert api_client.get("/api/documents", params={"facet": "no-colon"}).status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_facet_search.py -v`
Expected: FAIL — the `facet` parameter is ignored, so the "miss" query returns the document.

- [ ] **Step 3: Extend `DocumentFilters`**

In `src/library/search.py`, add to the `DocumentFilters` dataclass:

```python
    # Facet labels AND-compose: a document holds one value per facet, so two
    # values of the SAME facet intersect to nothing by construction, while two
    # DIFFERENT facets are the narrowing a user means by selecting both.
    facets: Mapping[str, str] = field(default_factory=dict)
```

and in `filter_conditions`, append one `EXISTS` per requested facet:

```python
    for facet_key, value_key in filters.facets.items():
        # Aliased per iteration: two facet filters produce two EXISTS clauses
        # over the same three tables, and unaliased references would collide.
        label = aliased(DocumentLabel)
        facet = aliased(Facet)
        value = aliased(FacetValue)
        conditions.append(
            select(literal(1))
            .select_from(label)
            .join(facet, facet.id == label.facet_id)
            .join(value, value.id == label.facet_value_id)
            .where(
                label.document_id == Document.id,
                facet.key == facet_key,
                value.key == value_key,
            )
            .exists()
        )
```

Add `Mapping` to the `collections.abc` imports, `aliased` (from `sqlalchemy.orm`) and `literal` to the SQLAlchemy imports, and `DocumentLabel`, `Facet`, `FacetValue` to the models import.

**This filter shape is verified.** It was executed against PostgreSQL 17 before this
plan step was finalised, confirming all four behaviours: a single facet narrows to
the documents carrying that value; two different facets AND-compose to their
intersection; two values of the SAME facet return nothing (structurally, since a
document holds at most one value per facet); and an unknown facet key returns
nothing rather than erroring.

- [ ] **Step 4: Accept the query parameter**

In `src/library/api/documents.py`, on the list endpoint, add:

```python
    facet: Annotated[
        list[str] | None,
        Query(description="Repeatable `facet=key:value` filter; AND-composes."),
    ] = None,
```

and, where `DocumentFilters` is constructed:

```python
    parsed_facets: dict[str, str] = {}
    for pair in facet or []:
        key, separator, value = pair.partition(":")
        if not separator or not key or not value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"facet must be 'key:value', got {pair!r}",
            )
        parsed_facets[key] = value
```

then pass `facets=parsed_facets` into the `DocumentFilters(...)` call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facet_search.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full backend suite**

Run: `uv run pytest -q`
Expected: all pass. `DocumentFilters` is consumed by Ask's tools and the MCP
server as well as the list endpoint, so a signature change shows up here.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format src/library/search.py src/library/api/documents.py tests/test_facet_search.py
uv run ruff check src/ tests/ migrations/
git add src/library/search.py src/library/api/documents.py tests/test_facet_search.py
git commit -m "feat(facets): filter the document list by facet"
```

---

### Task 10: Typed client and the facet filter bar

**Files:**
- Create: `frontend/src/api/facets.ts`, `frontend/src/components/facets/FacetFilterBar.vue`
- Modify: `frontend/src/views/DocumentListView.vue`
- Test: `frontend/src/components/facets/__tests__/FacetFilterBar.spec.ts`

**Interfaces:**
- Consumes: `GET /api/facets`, `GET /api/documents?facet=key:value` (Tasks 8, 9); `apiFetch` from `@/api/client`.
- Produces:
  - `interface FacetValueRef { key: string; label: string; parent_id: number | null; aliases: string[] }`
  - `interface FacetRef { key: string; label: string; ordinal: number; values: FacetValueRef[] }`
  - `fetchFacets(): Promise<FacetRef[]>`
  - `fetchDocumentLabels(id: number): Promise<Record<string, string>>`
  - `updateDocumentLabels(id: number, labels: Record<string, string | null>): Promise<Record<string, string>>`
  - `FacetFilterBar` props `{ facets: FacetRef[]; modelValue: Record<string, string> }`, emit `update:modelValue`.

Facets with **no values** render nothing — `vehicle`, `property` and `person`
ship empty, and an empty select is worse than an absent one.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/facets/__tests__/FacetFilterBar.spec.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FacetFilterBar from '../FacetFilterBar.vue'
import type { FacetRef } from '@/api/facets'

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'software', label: 'Software', parent_id: null, aliases: [] },
      { key: 'energy', label: 'Energy', parent_id: null, aliases: [] },
    ],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
]

describe('FacetFilterBar', () => {
  it('renders one select per facet that has values', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(wrapper.findAll('[data-facet-select]')).toHaveLength(1)
  })

  it('omits a facet with no values rather than rendering an empty select', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(wrapper.find('[data-testid="facet-select-vehicle"]').exists()).toBe(false)
  })

  it('emits the chosen value keyed by facet', async () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    await wrapper.find('[data-testid="facet-select-category"]').setValue('software')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([{ category: 'software' }])
  })

  it('choosing the blank option removes that facet from the selection', async () => {
    const wrapper = mount(FacetFilterBar, {
      props: { facets: FACETS, modelValue: { category: 'software' } },
    })
    await wrapper.find('[data-testid="facet-select-category"]').setValue('')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([{}])
  })

  it('shows a clear control only when something is selected', async () => {
    const empty = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(empty.find('[data-testid="facet-clear"]').exists()).toBe(false)
    const chosen = mount(FacetFilterBar, {
      props: { facets: FACETS, modelValue: { category: 'energy' } },
    })
    expect(chosen.find('[data-testid="facet-clear"]').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/facets/__tests__/FacetFilterBar.spec.ts`
Expected: FAIL — the component does not exist.

- [ ] **Step 3: Write the client**

Create `frontend/src/api/facets.ts`:

```ts
/**
 * Typed API for the controlled facet vocabulary (docs/facets.md).
 *
 * A facet is a named dimension; a document carries at most one value per facet.
 * Values are a closed set: the API rejects a label naming a value that does not
 * exist rather than creating it.
 */

import { apiFetch } from './client'

export interface FacetValueRef {
  key: string
  label: string
  parent_id: number | null
  aliases: string[]
}

export interface FacetRef {
  key: string
  label: string
  ordinal: number
  values: FacetValueRef[]
}

export async function fetchFacets(): Promise<FacetRef[]> {
  const body = await apiFetch<{ facets: FacetRef[] }>('/api/facets')
  return body.facets
}

export async function fetchDocumentLabels(id: number): Promise<Record<string, string>> {
  const body = await apiFetch<{ labels: Record<string, string> }>(`/api/documents/${id}/labels`)
  return body.labels
}

export async function updateDocumentLabels(
  id: number,
  labels: Record<string, string | null>,
): Promise<Record<string, string>> {
  const body = await apiFetch<{ labels: Record<string, string> }>(
    `/api/documents/${id}/labels`,
    { method: 'PUT', body: JSON.stringify({ labels }) },
  )
  return body.labels
}

/** `facet=key:value` pairs for a document-list query. */
export function facetQueryParams(selection: Record<string, string>): [string, string][] {
  return Object.entries(selection).map(([key, value]) => ['facet', `${key}:${value}`])
}
```

If `apiFetch` in `./client` does not already set the CSRF header on non-GET
requests, follow the pattern `frontend/src/api/documents.ts` uses for its own
mutations (`getCookie(CSRF_COOKIE)` into `CSRF_HEADER`).

- [ ] **Step 4: Write the component**

Create `frontend/src/components/facets/FacetFilterBar.vue`:

```vue
<script setup lang="ts">
/**
 * One select per facet, AND-composing, for the document list.
 *
 * Facets with no values render nothing: `vehicle`, `property` and `person` ship
 * empty, and an empty select is worse than an absent one.
 */
import { computed } from 'vue'
import type { FacetRef } from '@/api/facets'

const props = defineProps<{
  facets: FacetRef[]
  modelValue: Record<string, string>
}>()

const emit = defineEmits<{ 'update:modelValue': [Record<string, string>] }>()

const usable = computed<FacetRef[]>(() => props.facets.filter((f) => f.values.length > 0))
const hasSelection = computed<boolean>(() => Object.keys(props.modelValue).length > 0)

function onSelect(facetKey: string, event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  const next = { ...props.modelValue }
  if (value) next[facetKey] = value
  else delete next[facetKey]
  emit('update:modelValue', next)
}

function clearAll(): void {
  emit('update:modelValue', {})
}
</script>

<template>
  <div class="@container">
    <div class="flex flex-wrap items-end gap-3" data-testid="facet-filter-bar">
      <label v-for="facet in usable" :key="facet.key" class="flex flex-col gap-1">
        <span class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          {{ facet.label }}
        </span>
        <select
          class="form-select text-sm"
          data-facet-select="true"
          :data-testid="`facet-select-${facet.key}`"
          :value="modelValue[facet.key] ?? ''"
          @change="onSelect(facet.key, $event)"
        >
          <option value="">Any</option>
          <option v-for="value in facet.values" :key="value.key" :value="value.key">
            {{ value.label }}
          </option>
        </select>
      </label>

      <button
        v-if="hasSelection"
        type="button"
        class="btn-sm text-violet-600 hover:text-violet-700 dark:text-violet-400"
        data-testid="facet-clear"
        @click="clearAll"
      >
        Clear
      </button>
    </div>
  </div>
</template>
```

- [ ] **Step 5: Mount it in the documents list**

The existing filter row is `frontend/src/components/DocumentFilterBar.vue`
(mounted by `frontend/src/views/DocumentListView.vue`), so the facet bar belongs
inside it rather than beside it. In `DocumentFilterBar.vue`: load facets with
`fetchFacets()` on mount, hold `const facetSelection = ref<Record<string, string>>({})`,
render `<FacetFilterBar :facets="facets" v-model="facetSelection" />` in the
existing `items-end gap-3` row, and emit the selection upward with the other
filters. In `DocumentListView.vue`, append `facetQueryParams(selection)` to the
document list request. Keep any `limit` at or below 100 — the endpoint 422s
above it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/facets/`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
cd frontend && npx prettier --write src/api/facets.ts src/components/facets/ && npx vue-tsc --noEmit
cd .. && git add frontend/src/api/facets.ts frontend/src/components/facets/ frontend/src/views/DocumentListView.vue
git commit -m "feat(facets): facet filter bar on the document list"
```

---

### Task 11: Per-document facet editor

**Files:**
- Create: `frontend/src/components/facets/FacetEditor.vue`
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/components/facets/__tests__/FacetEditor.spec.ts`

**Interfaces:**
- Consumes: `FacetRef`, `updateDocumentLabels` (Task 10).
- Produces: `FacetEditor` props `{ documentId: number; facets: FacetRef[]; labels: Record<string, string> }`, emit `saved: [Record<string, string>]`.

Unlike the filter bar this **does** render facets with no values, as a disabled
select with an explanatory hint — the owner needs to see that `vehicle` exists
before they can ask for a value to be added to it.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/facets/__tests__/FacetEditor.spec.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import FacetEditor from '../FacetEditor.vue'
import type { FacetRef } from '@/api/facets'

const updateDocumentLabels = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  updateDocumentLabels: (...args: unknown[]) => updateDocumentLabels(...args),
}))

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [{ key: 'software', label: 'Software', parent_id: null, aliases: [] }],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
]

beforeEach(() => {
  updateDocumentLabels.mockReset()
  updateDocumentLabels.mockResolvedValue({ category: 'software' })
})

describe('FacetEditor', () => {
  it('renders an empty facet as a disabled select rather than hiding it', () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    const empty = wrapper.get('[data-testid="facet-edit-vehicle"]')
    expect(empty.attributes('disabled')).toBeDefined()
  })

  it('saves the changed label and emits what the server returned', async () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: 'software' })
    expect(wrapper.emitted('saved')?.at(-1)).toEqual([{ category: 'software' }])
  })

  it('sends null for a cleared facet so the label is removed', async () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: { category: 'software' } },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: null })
  })

  it('surfaces a save failure instead of silently discarding the edit', async () => {
    updateDocumentLabels.mockRejectedValue(new Error('nope'))
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="facet-error"]').text()).toContain('Could not save')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/facets/__tests__/FacetEditor.spec.ts`
Expected: FAIL — the component does not exist.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/facets/FacetEditor.vue`:

```vue
<script setup lang="ts">
/**
 * Edit one document's facet labels.
 *
 * Renders every facet, including those with no values yet — as a disabled
 * select with a hint. The owner needs to see that a facet exists before they
 * can ask for a value to be added to it, which is the opposite of the filter
 * bar's rule.
 *
 * Only changed facets are sent, and a cleared one is sent as null so the
 * backend removes the label rather than ignoring the key.
 */
import { computed, ref, watch } from 'vue'
import { updateDocumentLabels, type FacetRef } from '@/api/facets'

const props = defineProps<{
  documentId: number
  facets: FacetRef[]
  labels: Record<string, string>
}>()

const emit = defineEmits<{ saved: [Record<string, string>] }>()

const draft = ref<Record<string, string>>({ ...props.labels })
const saving = ref(false)
const error = ref<string | null>(null)

watch(
  () => props.labels,
  (next) => {
    draft.value = { ...next }
  },
)

const dirty = computed<Record<string, string | null>>(() => {
  const changes: Record<string, string | null> = {}
  for (const facet of props.facets) {
    const before = props.labels[facet.key] ?? ''
    const after = draft.value[facet.key] ?? ''
    if (before !== after) changes[facet.key] = after === '' ? null : after
  }
  return changes
})

function onSelect(facetKey: string, event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  draft.value = { ...draft.value, [facetKey]: value }
}

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  error.value = null
  try {
    const saved = await updateDocumentLabels(props.documentId, dirty.value)
    emit('saved', saved)
  } catch {
    error.value = 'Could not save these labels. Try again.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="@container" data-testid="facet-editor">
    <div class="flex flex-wrap items-end gap-3">
      <label v-for="facet in facets" :key="facet.key" class="flex flex-col gap-1">
        <span class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          {{ facet.label }}
        </span>
        <select
          class="form-select text-sm disabled:opacity-50"
          :data-testid="`facet-edit-${facet.key}`"
          :disabled="facet.values.length === 0"
          :value="draft[facet.key] ?? ''"
          @change="onSelect(facet.key, $event)"
        >
          <option value="">—</option>
          <option v-for="value in facet.values" :key="value.key" :value="value.key">
            {{ value.label }}
          </option>
        </select>
        <span
          v-if="facet.values.length === 0"
          class="text-xs text-gray-400 dark:text-gray-500"
        >
          No values yet
        </span>
      </label>

      <button
        type="button"
        class="btn-sm bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
        data-testid="facet-save"
        :disabled="saving"
        @click="save"
      >
        Save labels
      </button>
    </div>

    <p v-if="error" role="alert" class="mt-2 text-sm text-red-600 dark:text-red-400" data-testid="facet-error">
      {{ error }}
    </p>
  </section>
</template>
```

- [ ] **Step 4: Mount it on the document detail view**

In `frontend/src/views/DocumentDetailView.vue`, load facets and the document's
labels alongside the existing detail fetch, and render
`<FacetEditor :document-id="document.id" :facets="facets" :labels="labels" @saved="labels = $event" />`
in the metadata column.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/facets/`
Expected: 9 passed (5 from Task 10, 4 here).

- [ ] **Step 6: Commit**

```bash
cd frontend && npx prettier --write src/components/facets/ && npx vue-tsc --noEmit
cd .. && git add frontend/src/components/facets/ frontend/src/views/DocumentDetailView.vue
git commit -m "feat(facets): per-document facet editor"
```

---

### Task 12: Consolidate duplicate recipients

**Files:**
- Create: `src/library/facets/recipients.py`
- Modify: `src/library/cli.py`
- Test: `tests/test_recipient_merge.py`

**Interfaces:**
- Consumes: `Recipient`, `Document`.
- Produces:
  - `async duplicate_recipient_groups(session) -> list[tuple[str, list[tuple[int, str, int]]]]` — `(normalised_name, [(id, name, document_count), ...])` for groups of size > 1, largest first
  - `async merge_recipients(session, keep_id: int, drop_ids: Sequence[int]) -> int` — documents repointed
  - CLI `library recipients --list` / `library recipients --merge KEEP_ID DROP_ID[,DROP_ID...]`

The live `recipients` table carries the same drift as the tags: several rows
spelling one name several ways. Normalisation for *grouping* strips punctuation,
collapses whitespace and lowercases, so initials-and-surname variants of one
name land together. Grouping only proposes; merging is always an explicit
command, because two genuinely different people can normalise alike.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recipient_merge.py`:

```python
"""Proposing recipient duplicates, and merging them without losing documents."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.recipients import duplicate_recipient_groups, merge_recipients
from library.models import Document, DocumentSource, DocumentStatus, Recipient

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _seed(database_url: str, names: list[str]) -> list[int]:
    async def _work(session: AsyncSession) -> list[int]:
        ids: list[int] = []
        for name in names:
            recipient = Recipient(name=name)
            session.add(recipient)
            await session.flush()
            marker = f"recipient:{name}:{uuid.uuid4()}"
            session.add(
                Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    recipient_id=recipient.id,
                    title=marker,
                )
            )
            ids.append(recipient.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def test_spelling_variants_group_together(api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:6].upper()
    _seed(api_database_url, [f"{tag} Smith", f"{tag}. Smith", f"{tag}  smith"])
    groups = asyncio.run(_run(api_database_url, duplicate_recipient_groups))
    matching = [g for key, g in groups if tag.lower() in key]
    assert matching and len(matching[0]) == 3


def test_merging_repoints_documents_and_removes_the_duplicates(
    api_database_url: str,
) -> None:
    tag = uuid.uuid4().hex[:6].upper()
    keep, drop_a, drop_b = _seed(api_database_url, [f"{tag} A", f"{tag} B", f"{tag} C"])

    moved = asyncio.run(
        _run(api_database_url, lambda s: merge_recipients(s, keep, [drop_a, drop_b]))
    )
    assert moved == 2

    remaining = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Recipient.id).where(Recipient.id.in_([drop_a, drop_b]))),
        )
    )
    assert list(remaining.scalars()) == []

    counted = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document.id).where(Document.recipient_id == keep)),
        )
    )
    assert len(list(counted.scalars())) == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_recipient_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.facets.recipients'`.

- [ ] **Step 3: Write the implementation**

Create `src/library/facets/recipients.py`:

```python
"""Propose and merge duplicate recipient rows.

The recipients table has the same drift as the free-form tags: several rows
spelling one name several ways, splitting one person's documents. Normalisation
groups them; merging is always an explicit command, because two genuinely
different people can normalise alike.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Recipient

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Grouping only."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", name)).strip().lower()


async def duplicate_recipient_groups(
    session: AsyncSession,
) -> list[tuple[str, list[tuple[int, str, int]]]]:
    """Groups of recipients whose names normalise alike, largest group first."""
    rows = (
        await session.execute(
            select(Recipient.id, Recipient.name, func.count(Document.id))
            .outerjoin(Document, Document.recipient_id == Recipient.id)
            .group_by(Recipient.id, Recipient.name)
            .order_by(Recipient.id)
        )
    ).all()

    grouped: dict[str, list[tuple[int, str, int]]] = {}
    for recipient_id, name, document_count in rows:
        grouped.setdefault(normalise_name(name), []).append(
            (recipient_id, name, int(document_count))
        )
    duplicates = [(key, members) for key, members in grouped.items() if len(members) > 1]
    duplicates.sort(key=lambda pair: len(pair[1]), reverse=True)
    return duplicates


async def merge_recipients(
    session: AsyncSession, keep_id: int, drop_ids: Sequence[int]
) -> int:
    """Repoint every document from ``drop_ids`` onto ``keep_id`` and delete them.

    Returns the number of documents moved. ``keep_id`` is filtered out of
    ``drop_ids`` so a caller passing the survivor cannot delete it.
    """
    targets = [rid for rid in drop_ids if rid != keep_id]
    if not targets:
        return 0
    moved = (
        await session.execute(
            update(Document)
            .where(Document.recipient_id.in_(targets))
            .values(recipient_id=keep_id)
        )
    ).rowcount
    await session.execute(delete(Recipient).where(Recipient.id.in_(targets)))
    return int(moved)
```

- [ ] **Step 4: Add the CLI command**

In `src/library/cli.py`:

```python
@app.command("recipients")
def recipients_command(
    list_duplicates: bool = typer.Option(False, "--list", help="Show duplicate groups."),
    merge: str = typer.Option(
        "", "--merge", help="KEEP_ID:DROP_ID[,DROP_ID...] — repoint and delete."
    ),
) -> None:
    """Inspect and consolidate duplicate recipient rows."""
    from library.facets.recipients import duplicate_recipient_groups, merge_recipients

    async def _operation(session: AsyncSession) -> str:
        if merge:
            keep_raw, _, drops_raw = merge.partition(":")
            if not drops_raw:
                return "error: expected KEEP_ID:DROP_ID[,DROP_ID...]"
            moved = await merge_recipients(
                session, int(keep_raw), [int(part) for part in drops_raw.split(",")]
            )
            await session.commit()
            return f"moved {moved} documents"
        groups = await duplicate_recipient_groups(session)
        if not groups:
            return "no duplicate recipients"
        lines = []
        for key, members in groups:
            rendered = ", ".join(f"{rid}={name!r} ({n} docs)" for rid, name, n in members)
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines)

    typer.echo(_run(_operation))
    if not list_duplicates and not merge:
        typer.echo("(pass --list or --merge)")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_recipient_merge.py -v`
Expected: 2 passed.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/library/facets/recipients.py src/library/cli.py tests/test_recipient_merge.py
uv run ruff check src/ tests/ migrations/
git add src/library/facets/recipients.py src/library/cli.py tests/test_recipient_merge.py
git commit -m "feat(facets): propose and merge duplicate recipients"
```

---

### Task 13: End-to-end journey

**Files:**
- Create: `frontend/e2e/facets.spec.ts`

**Interfaces:**
- Consumes: everything above, through a real stack.

This suite runs on **three projects** — chromium@1280, mobile-webkit@375 and
tablet-webkit@656. Assert on presence and text, not on layout: the filter bar
wraps at narrow widths and a visibility assertion written for 1280 will fail on
the other two. The specs share one backend serially, so scope every assertion by
a facet key unique to the run rather than by absolute counts.

- [ ] **Step 1: Write the spec**

Create `frontend/e2e/facets.spec.ts`:

```ts
import { test, expect } from '@playwright/test'

/**
 * Create a facet and a value, label a document, filter by it.
 *
 * Runs on all three viewport projects, so nothing here asserts on layout —
 * the filter bar wraps at 375 and 656, and a visibility assertion written for
 * 1280 fails on the other two.
 */
test('a facet can be created, applied to a document, and filtered on', async ({
  page,
}) => {
  const key = `e2e${Date.now().toString(36)}`

  // page.request, not the standalone `request` fixture: only the page context
  // carries the authenticated session cookie. Every existing spec does this.
  await page.goto('/documents')
  const facet = await page.request.post('/api/facets', { data: { key, label: 'E2E' } })
  expect(facet.ok()).toBeTruthy()
  const value = await page.request.post(`/api/facets/${key}/values`, {
    data: { key: 'alpha', label: 'Alpha' },
  })
  expect(value.ok()).toBeTruthy()

  await page.reload()
  const firstCard = page.locator('[data-testid="doc-card"]').first()
  await expect(firstCard).toBeVisible()
  await firstCard.click()

  const editor = page.getByTestId('facet-editor')
  await expect(editor).toBeAttached()
  await page.getByTestId(`facet-edit-${key}`).selectOption('alpha')
  await page.getByTestId('facet-save').click()
  await expect(page.getByTestId('facet-error')).toHaveCount(0)

  await page.goto('/documents')
  await page.getByTestId(`facet-select-${key}`).selectOption('alpha')
  await expect(page.locator('[data-testid="doc-card"]')).toHaveCount(1)
})
```

`doc-card` is the existing document-card test id, already used by
`frontend/e2e/`; do not introduce a new one.

- [ ] **Step 2: Run it on every project**

Run: `cd frontend && npx playwright test e2e/facets.spec.ts`
Expected: 3 passes — one per project. A failure only on mobile-webkit or
tablet-webkit is a layout assertion that leaked in, not a real regression.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/facets.spec.ts
git commit -m "test(facets): end-to-end label and filter journey"
```

---

### Task 14: Documentation and journal

**Files:**
- Create: `docs/facets.md`, `journal/260828-facet-vocabulary.md`
- Modify: `docs/README.md`, `docs/api.md`, `docs/architecture.md`

- [ ] **Step 1: Write `docs/facets.md`**

H1 is a clean title with no number or date (the repo's convention; viewer titles
come from the H1). Cover, in this order: what a facet is and why it is not a
tag; the shipped vocabulary and why `vehicle`/`property`/`person` ship empty;
the closed-set rule and the suggestion queue; the CRUD operations and their
costs (rename free, merge cheap, split needs a re-label); `library
label-archive`; the REST surface; and the `parent_id` column reserved for a
future second level.

**No real sender names, personal names, addresses, vehicle registrations, or
amounts.** Illustrate with invented values.

- [ ] **Step 2: Add the API section**

In `docs/api.md`, document the routes from Task 8 in the file's existing style,
including the 422-on-unknown-value behaviour and the repeatable `facet=key:value`
parameter on `GET /api/documents`.

- [ ] **Step 3: Add the architecture note**

In `docs/architecture.md`, a short subsection: labels live on documents; the
composite primary key is what guarantees one value per facet; the composite
foreign key is what stops a label pointing at another facet's value.

- [ ] **Step 4: Link it**

Add `docs/facets.md` to `docs/README.md`'s document list.

- [ ] **Step 5: Write the journal entry**

Create `journal/260828-facet-vocabulary.md` recording: why the free-form tags
were replaced (counts only — 771 distinct, 454 used once), the four drift modes,
the decision that tags inform the vocabulary while documents determine the
labels, and the closed-set enforcement. Counts are fine; names are not.

- [ ] **Step 6: Run the docs check and commit**

```bash
make check-docs
uv run ruff check src/ tests/ migrations/
git add docs/ journal/
git commit -m "docs(facets): document the controlled label vocabulary"
```

---

## Done when

- [ ] `uv run pytest -q` passes in full.
- [ ] `uv run ruff check src/ tests/ migrations/` and `uv run ruff format --check .` are clean.
- [ ] `cd frontend && npx vitest run && npx vue-tsc --noEmit` passes.
- [ ] `npx playwright test` passes on all three viewport projects.
- [ ] `uv run library label-archive` labels the archive, and `GET /api/facets` returns the seeded vocabulary.
- [ ] No real names, addresses, registrations or amounts anywhere in the diff.
