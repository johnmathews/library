# Money Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every amount in the archive a declared meaning, and make one real-world payment count once no matter how many documents describe it.

**Architecture:** Two new document columns — `amount_kind` (what the number *is*) and `reference` (the invoice/order number) — plus a derived payment identity computed by four rules in a SQL view, with a small overrides table for the corrections a human makes. No amount whose kind is not a payment ever enters a total.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 17, Anthropic SDK, pytest, Vue 3 + TypeScript, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-28-charts-redesign-design.md` — this plan implements **layer B** (§8.1–8.3) only. Spend lines (§8.4) belong to plan 3, because `line_labels` depends on the facet vocabulary and would otherwise couple this plan to plan 1.

**Independent of plan 1.** Nothing here touches facets. The two plans can run in parallel sessions.

**The rules in Task 4 are already verified.** They were built and executed against PostgreSQL 17 with fixtures mirroring every ambiguous case in the live archive before this plan was written (spec §5.2). Do not redesign them; the SQL below is the SQL that passed.

## Global Constraints

- Python target is 3.13. Type annotations on every function signature.
- `uv` for all dependency management; `pytest` for tests.
- **Do not add a new `*_model` setting.** Every `*_model` setting requires a matching row in `MODEL_PRICING_USD_PER_MTOK` (`src/library/extraction/pricing.py`) or the app refuses to boot. This plan reuses `settings.extraction_model`.
- CI runs `ruff check` **and** `ruff format --check` over the **whole repository including `migrations/`**.
- `GET /api/documents` rejects `limit > 100` with a 422.
- Integration tests share one session-scoped Postgres and list endpoints default to 25 rows. Scope every list assertion by a unique marker, never by absolute counts.
- Frontend e2e runs on chromium@1280, mobile-webkit@375, tablet-webkit@656.
- E2E specs must use `page.request`, not the standalone `request` fixture — only the page context carries the session cookie.
- No `except Exception -> pytest.skip` guards.
- **The repository is public.** No real sender names, personal names, addresses, registrations, or real amounts in code, fixtures, comments, docs, or commit messages.
- Migration numbering: this plan's migration follows plan 1's `0032`. If plan 1 has not landed, use `0032` and coordinate; otherwise `0033`.

## File Structure

**Create:**
- `migrations/versions/00XX_money_facts.py` — the enum, two columns, the overrides table, and the two views.
- `src/library/money/__init__.py` — package marker.
- `src/library/money/payments.py` — payment identity: rule predicates and the queries over the views.
- `src/library/money/backfill.py` — re-extract amount semantics for existing documents.
- `src/library/api/payments.py` — payment group and override endpoints.
- `frontend/src/api/payments.ts` — typed client.
- `frontend/src/components/payments/PaymentGroup.vue` — the collapse, with split/merge controls.
- `docs/money-facts.md`, `journal/260828-money-facts.md`.

**Modify:**
- `src/library/models.py` — `AmountKind` enum, two `Document` columns, `PaymentOverride`.
- `src/library/extraction/schema.py` — two extracted fields with their descriptions.
- `src/library/extraction/apply.py` — persist the two fields.
- `src/library/cli.py` — `library backfill-amounts`.
- `src/library/app.py` — register the payments router.
- `frontend/src/views/DocumentDetailView.vue` — mount `PaymentGroup`.

**Boundaries:** `payments.py` only reads — every mutation is an override row. The rules live in SQL (a view) rather than Python so plan 3's `spend_facts` can join them without reimplementing the logic.

---

### Task 1: Schema for amount semantics and payment overrides

**Files:**
- Create: `migrations/versions/00XX_money_facts.py`
- Modify: `src/library/models.py`
- Test: `tests/test_money_schema.py`

**Interfaces:**
- Produces: `AmountKind` (str enum: `payment_due`, `payment_made`, `assessment`, `coverage_limit`, `balance`, `estimate`, `none`); `Document.amount_kind: Mapped[AmountKind | None]`; `Document.reference: Mapped[str | None]`; `PaymentOverride` on table `payment_overrides` with `kind` (`MERGE`/`SPLIT`), `doc_a`, `doc_b`.

`amount_kind` is nullable: an unlabelled document is not the same as one
declared to carry no money, and only the former should appear in a backfill
queue. Task 4 treats `NULL` as "not summable", so an un-backfilled archive
under-reports rather than over-reports.

- [ ] **Step 1: Write the failing test**

Create `tests/test_money_schema.py`:

```python
"""The money-facts columns and the overrides table."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import (
    AmountKind,
    Document,
    DocumentSource,
    DocumentStatus,
    PaymentOverride,
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


def _new_document(**kwargs: object) -> Document:
    marker = f"money:{uuid.uuid4()}"
    return Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        status=DocumentStatus.INDEXED,
        title=marker,
        **kwargs,
    )


def test_amount_kind_and_reference_round_trip(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> int:
        doc = _new_document(amount_kind=AmountKind.PAYMENT_MADE, reference="ABC-123")
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(api_database_url, lambda s: s.execute(select(Document).where(Document.id == document_id)))
    ).scalar_one()
    assert stored.amount_kind is AmountKind.PAYMENT_MADE
    assert stored.reference == "ABC-123"


def test_amount_kind_defaults_to_null_not_to_a_payment(api_database_url: str) -> None:
    """NULL means 'not yet decided'. Task 4 treats it as not summable, so an
    un-backfilled archive under-reports rather than over-reports."""

    async def _work(session: AsyncSession) -> int:
        doc = _new_document()
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(api_database_url, lambda s: s.execute(select(Document).where(Document.id == document_id)))
    ).scalar_one()
    assert stored.amount_kind is None


def test_an_override_kind_outside_merge_or_split_is_rejected(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="NONSENSE", doc_a=1, doc_b=2))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_money_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'AmountKind' from 'library.models'`.

- [ ] **Step 3: Write the migration**

Create `migrations/versions/00XX_money_facts.py` (replace `00XX`/`down_revision` with the real numbers per Global Constraints):

```python
"""money facts: amount semantics, reference numbers, payment overrides

``amount_total`` alone says nothing about what a number MEANS. The live archive
carries insurance coverage ceilings, nil-return confirmations and quotes in the
same column as real payments, and summing them together is how a coverage
ceiling was once charted as spending.

``amount_kind`` declares the meaning; only the payment kinds are ever summed.
It is NULLABLE on purpose: NULL is "not yet decided", which is not the same as
"carries no money", and only the former belongs in a backfill queue. Consumers
treat NULL as not summable, so an un-backfilled archive under-reports rather
than over-reports.

``reference`` is the document's own invoice/order/booking number. It is the
strongest evidence that two documents describe one payment, and the only such
evidence that works across an arbitrary gap between an invoice's date and its
receipt's.

Revision ID: 00XX
Revises: 0032

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "00XX"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_KINDS = (
    "payment_due",
    "payment_made",
    "assessment",
    "coverage_limit",
    "balance",
    "estimate",
    "none",
)


def upgrade() -> None:
    amount_kind = sa.Enum(*_AMOUNT_KINDS, name="amount_kind", native_enum=False, length=16)
    op.add_column("documents", sa.Column("amount_kind", amount_kind, nullable=True))
    op.add_column("documents", sa.Column("reference", sa.String(128), nullable=True))
    op.create_index("ix_documents_reference", "documents", ["sender_id", "reference"])
    op.create_table(
        "payment_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "doc_a", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "doc_b", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('MERGE','SPLIT')", name="payment_overrides_kind"),
        sa.CheckConstraint("doc_a < doc_b", name="payment_overrides_ordered"),
        sa.UniqueConstraint("kind", "doc_a", "doc_b", name="payment_overrides_unique"),
    )


def downgrade() -> None:
    op.drop_table("payment_overrides")
    op.drop_index("ix_documents_reference", table_name="documents")
    op.drop_column("documents", "reference")
    op.drop_column("documents", "amount_kind")
    sa.Enum(name="amount_kind").drop(op.get_bind(), checkfirst=True)
```

The `doc_a < doc_b` check is what makes an override pair canonical, so
`(5, 9)` and `(9, 5)` cannot both exist and the unique constraint means
something. Callers must order the pair before inserting.

- [ ] **Step 4: Write the models**

In `src/library/models.py`, beside the other enums:

```python
class AmountKind(StrEnum):
    """What a document's ``amount_total`` actually is.

    Only ``PAYMENT_DUE``, ``PAYMENT_MADE`` and ``ASSESSMENT`` are ever summed
    into a spending total. The rest exist so that a coverage ceiling, an opening
    balance, a quote or a nil-return confirmation can be recorded faithfully
    without contaminating one.
    """

    PAYMENT_DUE = "payment_due"
    PAYMENT_MADE = "payment_made"
    ASSESSMENT = "assessment"
    COVERAGE_LIMIT = "coverage_limit"
    BALANCE = "balance"
    ESTIMATE = "estimate"
    NONE = "none"


SUMMABLE_AMOUNT_KINDS: frozenset[AmountKind] = frozenset(
    {AmountKind.PAYMENT_DUE, AmountKind.PAYMENT_MADE, AmountKind.ASSESSMENT}
)
```

On `Document`, beside `amount_total`:

```python
    # What amount_total means. NULL = not yet decided; consumers treat NULL as
    # not summable, so an un-backfilled archive under-reports rather than over-.
    amount_kind: Mapped[AmountKind | None] = mapped_column(
        Enum(
            AmountKind,
            name="amount_kind",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        nullable=True,
    )
    # The document's own invoice / order / booking number. The only evidence
    # that pairs an invoice with its receipt across an arbitrary date gap.
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

And a new model:

```python
class PaymentOverride(Base):
    """A human correction to the derived payment identity.

    ``MERGE`` joins two documents the rules kept apart; ``SPLIT`` separates two
    the rules joined. ``doc_a < doc_b`` is enforced by a check constraint so a
    pair has one canonical representation.
    """

    __tablename__ = "payment_overrides"
    __table_args__ = (
        CheckConstraint("kind IN ('MERGE','SPLIT')", name="payment_overrides_kind"),
        CheckConstraint("doc_a < doc_b", name="payment_overrides_ordered"),
        UniqueConstraint("kind", "doc_a", "doc_b", name="payment_overrides_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    doc_a: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    doc_b: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Add `CheckConstraint` to the SQLAlchemy imports if it is not already there.

- [ ] **Step 5: Run the tests and the migration round-trip**

Run: `uv run pytest tests/test_money_schema.py -v` → 3 passed.
Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` → no error.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format migrations/ src/library/models.py tests/test_money_schema.py
uv run ruff check src/ tests/ migrations/
git add migrations/ src/library/models.py tests/test_money_schema.py
git commit -m "feat(money): amount_kind, reference, and payment overrides"
```

---

### Task 2: Extract amount semantics and the reference number

**Files:**
- Modify: `src/library/extraction/schema.py`, `src/library/extraction/apply.py`
- Test: `tests/test_extraction_amount_kind.py`

**Interfaces:**
- Consumes: `AmountKind` from Task 1.
- Produces: two fields on the extraction output — `amount_kind: str | None` and `reference: str | None` — persisted onto `Document` by `apply`.

`amount_kind` is now load-bearing for every spending total, so its field
description carries the whole discrimination explicitly. It is a simpler
judgement than `kind_slug` (which is known to misclassify receipts as invoices)
because it asks one question: is this number a demand for payment, evidence of
payment, or neither?

- [ ] **Step 1: Write the failing test**

Create `tests/test_extraction_amount_kind.py`:

```python
"""The extractor's two new money fields, and how apply persists them."""

import pytest

from library.extraction.schema import ExtractionResult, normalize_amount_kind


def test_the_seven_kinds_are_accepted() -> None:
    for kind in (
        "payment_due",
        "payment_made",
        "assessment",
        "coverage_limit",
        "balance",
        "estimate",
        "none",
    ):
        assert normalize_amount_kind(kind) == kind


def test_an_unknown_kind_becomes_none_rather_than_a_payment() -> None:
    """A kind we cannot read must never default into a summable one."""
    assert normalize_amount_kind("invoice_total") is None
    assert normalize_amount_kind("") is None
    assert normalize_amount_kind(None) is None


def test_kind_matching_is_case_and_space_insensitive() -> None:
    assert normalize_amount_kind("  Payment_Made ") == "payment_made"
    assert normalize_amount_kind("payment made") == "payment_made"


def test_a_blank_reference_normalises_to_none() -> None:
    result = ExtractionResult.model_validate(_minimal_payload(reference="   "))
    assert result.reference is None


def test_a_reference_is_kept_verbatim_apart_from_trimming() -> None:
    result = ExtractionResult.model_validate(_minimal_payload(reference=" INV-77/A "))
    assert result.reference == "INV-77/A"


def _minimal_payload(**overrides: object) -> dict[str, object]:
    """The smallest payload ExtractionResult validates, plus overrides.

    Built from the model's own required fields so this test does not drift when
    unrelated fields are added.
    """
    payload: dict[str, object] = {
        "kind_slug": "invoice",
        "sender_name": "Vendor",
        "recipient_name": None,
        "title": "A title",
        "summary": "A summary.",
        "document_date": None,
        "amount_total": None,
        "currency": None,
        "amount_kind": None,
        "reference": None,
        "tags": [],
        "reasoning_note": None,
        "addressee_raw": None,
        "signer_raw": None,
    }
    payload.update(overrides)
    return payload
```

If `ExtractionResult` has required fields not listed above, add them to
`_minimal_payload` rather than changing the model.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_extraction_amount_kind.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_amount_kind'`.

- [ ] **Step 3: Add the fields to the extraction schema**

In `src/library/extraction/schema.py`:

```python
AMOUNT_KINDS: tuple[str, ...] = (
    "payment_due",
    "payment_made",
    "assessment",
    "coverage_limit",
    "balance",
    "estimate",
    "none",
)


def normalize_amount_kind(value: str | None) -> str | None:
    """Map a model's answer onto one of ``AMOUNT_KINDS``, or None.

    Anything unrecognised becomes None rather than a guess. None is treated as
    not-summable downstream, so a misread can only ever cause a document to be
    left out of a total — never wrongly added to one.
    """
    if value is None:
        return None
    candidate = value.strip().lower().replace(" ", "_").replace("-", "_")
    return candidate if candidate in AMOUNT_KINDS else None
```

On the `ExtractionResult` model, beside `amount_total` and `currency`:

```python
    amount_kind: str | None = Field(
        default=None,
        description=(
            "What amount_total IS, not how much it is. One of: "
            "payment_due (an invoice or bill the reader owes); "
            "payment_made (a receipt or confirmation that money was paid); "
            "assessment (a tax or levy demand); "
            "coverage_limit (an insurance sum insured or maximum payout — NOT "
            "money anyone paid); "
            "balance (an account or statement position); "
            "estimate (a quote or indicative price, not yet owed); "
            "none (the amount is incidental, or is zero because nothing is due). "
            "Choose coverage_limit whenever the figure is a ceiling the insurer "
            "would pay rather than a premium the reader paid. null if unsure — "
            "an unsure answer leaves the amount out of totals, which is safe; a "
            "confident wrong answer corrupts them."
        ),
    )
    reference: str | None = Field(
        default=None,
        description=(
            "The document's own invoice, order, booking or assessment number, "
            "exactly as printed. This is what links an invoice to the receipt "
            "that settles it, so copy it verbatim including any prefix or "
            "punctuation. null when the document shows no such number."
        ),
    )
```

Add the validators, following the file's existing `@field_validator` style:

```python
    @field_validator("amount_kind", mode="after")
    @classmethod
    def _normalize_amount_kind(cls, value: str | None) -> str | None:
        return normalize_amount_kind(value)

    @field_validator("reference", mode="after")
    @classmethod
    def _trim_reference(cls, value: str | None) -> str | None:
        trimmed = (value or "").strip()
        return trimmed or None
```

If `reference` is already covered by the file's existing blank-to-none validator
list, add `"reference"` to that list instead of writing a second validator.

- [ ] **Step 4: Persist the fields**

In `src/library/extraction/apply.py`, wherever `amount_total` and `currency` are
written onto the `Document`, write the two new fields alongside:

```python
    document.amount_kind = AmountKind(result.amount_kind) if result.amount_kind else None
    document.reference = result.reference
```

Import `AmountKind` from `library.models`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_extraction_amount_kind.py -v` → 5 passed.
Run: `uv run pytest tests/ -k extraction -q` → the existing extraction suite still passes.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/library/extraction/ tests/test_extraction_amount_kind.py
uv run ruff check src/ tests/ migrations/
git add src/library/extraction/ tests/test_extraction_amount_kind.py
git commit -m "feat(money): extract amount_kind and reference"
```

---

### Task 3: Backfill amount semantics over the existing archive

**Files:**
- Create: `src/library/money/__init__.py`, `src/library/money/backfill.py`
- Modify: `src/library/cli.py`
- Test: `tests/test_money_backfill.py`

**Interfaces:**
- Consumes: `AmountKind`, `normalize_amount_kind`, `settings.extraction_model`.
- Produces:
  - `AMOUNT_SYSTEM_PROMPT: str`
  - `async documents_needing_amount_kind(session, *, limit: int | None) -> list[int]`
  - `async classify_amount(settings, fields, *, client=None, backend="api") -> tuple[str | None, str | None, int, int] | None` returning `(amount_kind, reference, in_tokens, out_tokens)`
  - `async run_amount_backfill(session, settings, *, limit) -> tuple[int, int]`
  - CLI `library backfill-amounts [--limit N]`

Only documents that **have an amount** and **lack an `amount_kind`** are
selected — a document with no `amount_total` has no semantics to decide, and
re-deciding a document that already has one would overwrite a human correction.

- [ ] **Step 1: Write the failing test**

Create `tests/test_money_backfill.py`:

```python
"""Selection for the amount backfill. The model call itself is not exercised."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import AmountKind, Document, DocumentSource, DocumentStatus
from library.money.backfill import documents_needing_amount_kind

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


def _seed(database_url: str, rows: list[tuple[Decimal | None, AmountKind | None]]) -> list[int]:
    async def _work(session: AsyncSession) -> list[int]:
        ids: list[int] = []
        for amount, kind in rows:
            marker = f"backfill:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                amount_total=amount,
                amount_kind=kind,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def test_only_amount_bearing_documents_without_a_kind_are_selected(
    api_database_url: str,
) -> None:
    needs, has_kind, no_amount = _seed(
        api_database_url,
        [
            (Decimal("10.00"), None),
            (Decimal("20.00"), AmountKind.PAYMENT_MADE),
            (None, None),
        ],
    )
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=None))
    )
    assert needs in selected
    assert has_kind not in selected
    assert no_amount not in selected


def test_the_limit_is_respected(api_database_url: str) -> None:
    _seed(api_database_url, [(Decimal("1.00"), None), (Decimal("2.00"), None)])
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=1))
    )
    assert len(selected) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_money_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.money'`.

- [ ] **Step 3: Write the module**

Create `src/library/money/__init__.py`:

```python
"""Money facts: what an amount means, and which documents share one payment."""
```

Create `src/library/money/backfill.py`:

```python
"""Decide amount semantics for documents extracted before the field existed.

A separate, cheap call rather than a full re-extraction: only two fields are
missing, and re-running extraction would also overwrite titles, summaries and
senders that a human may since have corrected.
"""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.extraction.schema import normalize_amount_kind
from library.llm import subscription
from library.models import AmountKind, Document, Kind, Sender

logger = logging.getLogger(__name__)

MAX_AMOUNT_TOKENS: int = 200
MAX_EXCERPT_CHARS: int = 2000

AMOUNT_SYSTEM_PROMPT: str = """\
You decide what a single number on a household document MEANS. You are not
asked how much it is.

Answer with one of exactly these values:
  payment_due     an invoice or bill the reader owes
  payment_made    a receipt or confirmation that money was paid
  assessment      a tax or levy demand
  coverage_limit  an insurance sum insured or maximum payout — NOT money paid
  balance         an account or statement position
  estimate        a quote or indicative price, not yet owed
  none            the amount is incidental, or zero because nothing is due

Also return the document's own invoice / order / booking / assessment number
exactly as printed, or null if it shows none.

If you are unsure of the kind, return null. An unsure answer leaves the amount
out of every total, which is safe; a confident wrong answer corrupts them.

Return ONLY this JSON, no prose or code fences:
{"amount_kind": "..."|null, "reference": "..."|null}"""


async def documents_needing_amount_kind(
    session: AsyncSession, *, limit: int | None
) -> list[int]:
    """Amount-bearing, non-deleted documents with no ``amount_kind`` yet.

    A document with no amount has no semantics to decide, and one that already
    has a kind may have been corrected by hand — neither is re-decided.
    """
    statement = (
        select(Document.id)
        .where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.amount_kind.is_(None),
        )
        .order_by(Document.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.execute(statement)).scalars())


async def classify_amount(
    settings: Settings,
    *,
    title: str | None,
    sender: str | None,
    kind: str | None,
    amount: str | None,
    currency: str | None,
    excerpt: str | None,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> tuple[str | None, str | None, int, int] | None:
    """``(amount_kind, reference, in_tokens, out_tokens)`` or None if unrunnable."""
    import json

    prompt = "\n".join(
        [
            f"Sender: {sender}",
            f"Document kind: {kind}",
            f"Title: {title}",
            f"Amount: {amount} {currency}",
            f"Text excerpt: {(excerpt or '')[:MAX_EXCERPT_CHARS]}",
        ]
    )

    def _parse(payload: str) -> tuple[str | None, str | None]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("amount classifier returned unparseable JSON")
            return None, None
        reference = parsed.get("reference")
        return (
            normalize_amount_kind(parsed.get("amount_kind")),
            str(reference).strip() or None if reference else None,
        )

    if backend == "subscription":
        result = await subscription.text_call(
            config_dir=settings.claude_config_dir,
            model=settings.extraction_model,
            system_prompt=AMOUNT_SYSTEM_PROMPT,
            prompt=prompt,
        )
        kind_value, reference = _parse(result.text)
        return kind_value, reference, result.usage.input_tokens, result.usage.output_tokens

    async def _call(anthropic: AsyncAnthropic) -> tuple[str | None, str | None, int, int]:
        response = await anthropic.messages.create(
            model=settings.extraction_model,
            max_tokens=MAX_AMOUNT_TOKENS,
            system=AMOUNT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        kind_value, reference = _parse(text)
        return kind_value, reference, response.usage.input_tokens, response.usage.output_tokens

    if client is not None:
        return await _call(client)
    if settings.anthropic_api_key is None:
        return None
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as owned:
        return await _call(owned)


async def run_amount_backfill(
    session: AsyncSession, settings: Settings, *, limit: int | None
) -> tuple[int, int]:
    """Classify each selected document. Returns ``(classified, skipped)``.

    Commits per document so a part-way failure keeps the work already done.
    """
    ids = await documents_needing_amount_kind(session, limit=limit)
    classified = skipped = 0
    for document_id in ids:
        row = (
            await session.execute(
                select(
                    Document.title,
                    Sender.name,
                    Kind.slug,
                    Document.amount_total,
                    Document.currency,
                    Document.ocr_text,
                )
                .outerjoin(Sender, Sender.id == Document.sender_id)
                .outerjoin(Kind, Kind.id == Document.kind_id)
                .where(Document.id == document_id)
            )
        ).one_or_none()
        if row is None:
            skipped += 1
            continue
        title, sender, kind, amount, currency, excerpt = row
        result = await classify_amount(
            settings,
            title=title,
            sender=sender,
            kind=kind,
            amount=str(amount) if amount is not None else None,
            currency=currency,
            excerpt=excerpt,
        )
        if result is None:
            skipped += 1
            continue
        kind_value, reference, _in_tokens, _out_tokens = result
        if kind_value is None:
            # Left NULL deliberately: not summable, and still in the queue.
            skipped += 1
            continue
        document = await session.get(Document, document_id)
        if document is None:
            skipped += 1
            continue
        document.amount_kind = AmountKind(kind_value)
        if reference and not document.reference:
            document.reference = reference
        await session.commit()
        classified += 1
    return classified, skipped
```

- [ ] **Step 4: Add the CLI command**

In `src/library/cli.py`:

```python
@app.command("backfill-amounts")
def backfill_amounts(
    limit: int = typer.Option(0, "--limit", help="Stop after this many documents (0 = all)."),
) -> None:
    """Decide amount_kind (and capture reference) for documents that lack it."""
    from library.money.backfill import run_amount_backfill

    settings = get_settings()

    async def _operation(session: AsyncSession) -> tuple[int, int]:
        return await run_amount_backfill(session, settings, limit=limit or None)

    classified, skipped = _run(_operation)
    typer.echo(f"classified {classified}, skipped {skipped}")
```

`get_settings`, `AsyncSession` and `_run` are already imported in `cli.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_money_backfill.py -v` → 2 passed.
Run: `uv run library backfill-amounts --help` → the option is listed. (`--help` is
rendered by `rich`; strip ANSI before asserting on it in any test.)

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/library/money/ src/library/cli.py tests/test_money_backfill.py
uv run ruff check src/ tests/ migrations/
git add src/library/money/ src/library/cli.py tests/test_money_backfill.py
git commit -m "feat(money): backfill amount semantics over the archive"
```

---

### Task 4: Payment identity

**Files:**
- Modify: `migrations/versions/00XX_money_facts.py` (add the two views to `upgrade`/`downgrade`)
- Create: `src/library/money/payments.py`
- Test: `tests/test_payment_identity.py`

**Interfaces:**
- Consumes: `documents.amount_kind`, `documents.reference`, `payment_overrides` (Task 1).
- Produces: SQL views `payment_edges(a, b, rule)` and `payments(document_id, payment_id)`; and
  - `async payment_group(session, document_id: int) -> list[int]` — every document sharing this one's payment, ascending
  - `async payment_id_for(session, document_id: int) -> int | None`
  - `async add_override(session, kind: Literal["MERGE", "SPLIT"], doc_a: int, doc_b: int) -> None` — orders the pair
  - `async collapse_counts(session, document_ids: Sequence[int]) -> tuple[int, int]` — `(payments, documents)` for a chart footer

**This SQL is verified — do not redesign it.** It was executed against
PostgreSQL 17 against fixtures covering every ambiguous shape in the live
archive, plus the null-safety cases. Confirmed green: R1/R2/R3 each firing only
where intended; the VETO; SPLIT and MERGE overrides; deleted partners excluded;
dateless documents still pairing on `reference`; `NULL` currency pairing;
`NULL` `amount_kind` refusing to fire R3; and transitivity (a three-document
chain collapsing to one payment).

Three null-safety details are load-bearing and were each verified:
`currency IS NOT DISTINCT FROM` (so two currency-less documents can pair, which
`=` would prevent); the explicit `amount_total IS NOT NULL`; and the fact that
`gap <= 60` is `NULL` — hence false — for a dateless document, so R2 is the only
rule that can pair one.

- [ ] **Step 1: Write the failing test**

Create `tests/test_payment_identity.py`:

```python
"""Payment identity: which documents describe one payment.

Every case here mirrors a real ambiguous shape in the archive, with invented
senders and amounts. The two that matter most are the pair four days apart that
must stay SEPARATE (two real purchases) and the pair months apart that must
MERGE (an invoice and the receipt that settled it).
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import AmountKind, Document, DocumentSource, DocumentStatus, Sender
from library.money.payments import add_override, payment_group

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


def _pair(
    database_url: str,
    rows: list[tuple[str | None, str, AmountKind | None, str | None]],
    currency: str | None = "EUR",
) -> list[int]:
    """Seed documents for ONE fresh sender. Rows are (date|None, amount, kind, ref)."""

    async def _work(session: AsyncSession) -> list[int]:
        sender = Sender(name=f"Vendor-{uuid.uuid4().hex[:8]}")
        session.add(sender)
        await session.flush()
        ids: list[int] = []
        for when, amount, kind, reference in rows:
            marker = f"pay:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                sender_id=sender.id,
                document_date=date.fromisoformat(when) if when else None,
                amount_total=Decimal(amount),
                currency=currency,
                amount_kind=kind,
                reference=reference,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def _group(database_url: str, document_id: int) -> list[int]:
    return asyncio.run(_run(database_url, lambda s: payment_group(s, document_id)))


def test_r1_same_day_same_amount_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
         ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None)],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r2_a_reference_match_merges_across_any_gap(api_database_url: str) -> None:
    """The case a date window cannot reach: a receipt issued months later."""
    a, b = _pair(
        api_database_url,
        [("2026-01-05", "900.00", AmountKind.PAYMENT_DUE, "K-100"),
         ("2026-03-20", "900.00", AmountKind.PAYMENT_MADE, "K-100")],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r3_complementary_kinds_within_sixty_days_merge(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-08-18", "13.25", AmountKind.PAYMENT_DUE, None),
         ("2026-08-24", "13.25", AmountKind.PAYMENT_MADE, None)],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_two_real_purchases_four_days_apart_stay_separate(api_database_url: str) -> None:
    """Both are payment_made, so R3 cannot fire. This is why complementarity,
    not a date window, is what makes date-tolerant merging safe."""
    a, b = _pair(
        api_database_url,
        [("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
         ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None)],
    )
    assert _group(api_database_url, a) == [a]
    assert _group(api_database_url, b) == [b]


def test_four_same_amount_invoices_merge_only_the_same_day_pair(
    api_database_url: str,
) -> None:
    a, b, c, d = _pair(
        api_database_url,
        [("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
         ("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
         ("2026-11-22", "689.40", AmountKind.PAYMENT_DUE, None),
         ("2027-01-05", "689.40", AmountKind.PAYMENT_DUE, None)],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, c) == [c]
    assert _group(api_database_url, d) == [d]


def test_differing_references_veto_a_same_day_merge(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-1"),
         ("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-2")],
    )
    assert _group(api_database_url, a) == [a]


def test_dateless_documents_still_pair_on_reference(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [(None, "75.00", AmountKind.PAYMENT_DUE, "Z-9"),
         (None, "75.00", AmountKind.PAYMENT_MADE, "Z-9")],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_currency_less_documents_can_pair(api_database_url: str) -> None:
    """`currency = currency` is NULL for two NULL currencies; IS NOT DISTINCT FROM is not."""
    a, b = _pair(
        api_database_url,
        [("2026-05-01", "60.00", AmountKind.PAYMENT_DUE, None),
         ("2026-05-01", "60.00", AmountKind.PAYMENT_MADE, None)],
        currency=None,
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_unbackfilled_amount_kinds_do_not_merge_on_r3(api_database_url: str) -> None:
    """NULL amount_kind must not satisfy complementarity, or an un-backfilled
    archive would silently collapse unrelated same-amount documents."""
    a, b = _pair(
        api_database_url,
        [("2026-04-01", "99.00", None, None), ("2026-04-20", "99.00", None, None)],
    )
    assert _group(api_database_url, a) == [a]


def test_a_chain_of_three_collapses_to_one_payment(api_database_url: str) -> None:
    a, b, c = _pair(
        api_database_url,
        [("2026-09-01", "30.00", AmountKind.PAYMENT_DUE, "T-1"),
         ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1"),
         ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1")],
    )
    assert _group(api_database_url, a) == sorted([a, b, c])


def test_a_split_override_unmerges_an_automatic_pair(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
         ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None)],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "SPLIT", a, b)))
    assert _group(api_database_url, a) == [a]


def test_a_merge_override_joins_a_pair_no_rule_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
         ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None)],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_an_override_pair_is_ordered_regardless_of_argument_order(
    api_database_url: str,
) -> None:
    """doc_a < doc_b is a check constraint; add_override must order the pair."""
    a, b = _pair(
        api_database_url,
        [("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
         ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None)],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", b, a)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_a_deleted_partner_leaves_the_survivor_alone(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [("2026-06-01", "55.00", AmountKind.PAYMENT_DUE, None),
         ("2026-06-01", "55.00", AmountKind.PAYMENT_MADE, None)],
    )

    async def _delete(session: AsyncSession) -> None:
        from datetime import UTC, datetime

        document = await session.get(Document, b)
        assert document is not None
        document.deleted_at = datetime.now(UTC)

    asyncio.run(_run(api_database_url, _delete))
    assert _group(api_database_url, a) == [a]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_payment_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.money.payments'`.

- [ ] **Step 3: Add the views to the migration**

Append to `upgrade()` in `migrations/versions/00XX_money_facts.py`, verbatim:

```python
    op.execute("""
CREATE VIEW payment_edges AS
WITH pairs AS (
  SELECT a.id AS a, b.id AS b, a.reference ra, b.reference rb,
         a.amount_kind ka, b.amount_kind kb,
         (a.document_date = b.document_date) AS same_day,
         abs(a.document_date - b.document_date) AS gap
  FROM documents a JOIN documents b
    ON a.id < b.id
   AND a.sender_id = b.sender_id
   AND a.currency IS NOT DISTINCT FROM b.currency
   AND a.amount_total = b.amount_total
  WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL
    AND a.amount_total IS NOT NULL AND a.sender_id IS NOT NULL
), ruled AS (
  SELECT a, b, CASE
    WHEN ra IS NOT NULL AND rb IS NOT NULL AND ra <> rb THEN NULL
    WHEN ra IS NOT NULL AND ra = rb                     THEN 'R2'
    WHEN same_day                                       THEN 'R1'
    WHEN gap <= 60 AND ((ka='payment_due' AND kb='payment_made')
                     OR (ka='payment_made' AND kb='payment_due')) THEN 'R3'
    ELSE NULL END AS rule
  FROM pairs)
SELECT a, b, rule FROM ruled
WHERE rule IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM payment_overrides o
    WHERE o.kind='SPLIT' AND o.doc_a=ruled.a AND o.doc_b=ruled.b)
UNION
SELECT doc_a, doc_b, 'OVERRIDE' FROM payment_overrides WHERE kind='MERGE'
""")
    op.execute("""
CREATE VIEW payments AS
WITH RECURSIVE bidir AS (
  SELECT a, b FROM payment_edges UNION SELECT b, a FROM payment_edges),
reach(doc, member) AS (
  SELECT id, id FROM documents WHERE deleted_at IS NULL
  UNION
  SELECT r.doc, e.b FROM reach r JOIN bidir e ON e.a = r.member)
SELECT doc AS document_id, min(member) AS payment_id FROM reach GROUP BY doc
""")
```

and at the TOP of `downgrade()`, before the table drops:

```python
    op.execute("DROP VIEW IF EXISTS payments")
    op.execute("DROP VIEW IF EXISTS payment_edges")
```

Order matters in both directions: `payments` reads `payment_edges`, which reads
`payment_overrides`.

- [ ] **Step 4: Write the module**

Create `src/library/money/payments.py`:

```python
"""Which documents describe one payment.

The rules live in the ``payment_edges`` SQL view rather than in Python, so a
later chart query can join payment identity without reimplementing it. This
module is the read API over those views, plus the one write: an override row.

The rules, in the order the view applies them:

  VETO  both documents carry a reference and they differ -> never merge
  R2    same sender, same non-null reference             -> merge at any date gap
  R1    same sender, date, amount, currency              -> merge
  R3    same sender, amount, currency; complementary
        amount_kind (due <-> made); gap <= 60 days       -> merge

R3's complementarity requirement is what makes date-tolerant merging safe: an
invoice and its receipt are never the same kind of amount, while two genuinely
separate purchases of the same value always are. No date tolerance alone can
separate those cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import PaymentOverride

OverrideKind = Literal["MERGE", "SPLIT"]

_PAYMENTS = text("SELECT document_id, payment_id FROM payments").columns(
    document_id=None, payment_id=None
)


async def payment_id_for(session: AsyncSession, document_id: int) -> int | None:
    """The payment this document belongs to, or None if it is deleted/absent."""
    result = await session.execute(
        text("SELECT payment_id FROM payments WHERE document_id = :d"), {"d": document_id}
    )
    row = result.one_or_none()
    return int(row[0]) if row is not None else None


async def payment_group(session: AsyncSession, document_id: int) -> list[int]:
    """Every document sharing this one's payment, ascending. Includes itself.

    Returns ``[]`` for a deleted or unknown document.
    """
    result = await session.execute(
        text(
            "SELECT document_id FROM payments "
            "WHERE payment_id = (SELECT payment_id FROM payments WHERE document_id = :d) "
            "ORDER BY document_id"
        ),
        {"d": document_id},
    )
    return [int(row[0]) for row in result.all()]


async def add_override(
    session: AsyncSession, kind: OverrideKind, doc_a: int, doc_b: int
) -> None:
    """Record a human correction. Orders the pair — ``doc_a < doc_b`` is a check
    constraint, so an unordered insert would be rejected."""
    if doc_a == doc_b:
        raise ValueError("an override needs two distinct documents")
    low, high = (doc_a, doc_b) if doc_a < doc_b else (doc_b, doc_a)
    await session.execute(
        pg_insert(PaymentOverride)
        .values(kind=kind, doc_a=low, doc_b=high)
        .on_conflict_do_nothing(constraint="payment_overrides_unique")
    )


async def collapse_counts(
    session: AsyncSession, document_ids: Sequence[int]
) -> tuple[int, int]:
    """``(payments, documents)`` across a set of documents.

    This is the "12 payments from 15 documents" line a chart footer shows, which
    is what makes a residual double-count visible rather than merely suspected.
    """
    if not document_ids:
        return 0, 0
    result = await session.execute(
        text(
            "SELECT count(DISTINCT payment_id), count(*) FROM payments "
            "WHERE document_id = ANY(:ids)"
        ),
        {"ids": list(document_ids)},
    )
    payments, documents = result.one()
    return int(payments), int(documents)
```

Remove the unused `_PAYMENTS`, `func` and `select` bindings if the final file
does not use them — `ruff check` will flag them.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_payment_identity.py -v`
Expected: 14 passed.

- [ ] **Step 6: Verify the migration round-trips with the views**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: no error. A failure here means the views are dropped in the wrong
order relative to `payment_overrides`.

- [ ] **Step 7: Format and commit**

```bash
uv run ruff format src/library/money/ migrations/ tests/test_payment_identity.py
uv run ruff check src/ tests/ migrations/
git add src/library/money/payments.py migrations/ tests/test_payment_identity.py
git commit -m "feat(money): derive payment identity from four rules"
```

---

### Task 5: Payment API

**Files:**
- Create: `src/library/api/payments.py`
- Modify: `src/library/app.py`
- Test: `tests/test_api_payments.py`

**Interfaces:**
- Consumes: `payment_group`, `add_override` (Task 4).
- Produces:

```
GET    /api/documents/{document_id}/payment    -> {payment_id, documents:[{id,title,amount_kind,reference,document_date}]}
POST   /api/payments/merge                     {doc_a, doc_b} -> {payment_id, documents:[...]}
POST   /api/payments/split                     {doc_a, doc_b} -> {payment_id, documents:[...]}
GET    /api/payments/duplicates                -> groups of >1 document, largest first, capped at 100
```

`/api/payments/duplicates` is the review surface: it is what makes the archive's
double-counting visible before any chart exists, which is this layer's
standalone value.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_payments.py`:

```python
"""The payment endpoints, exercised through the app."""

import pytest
from fastapi.testclient import TestClient

from library.models import AmountKind

pytestmark = pytest.mark.integration


def test_a_documents_payment_group_lists_its_partners(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    body = api_client.get(f"/api/documents/{a}/payment").json()
    assert sorted(d["id"] for d in body["documents"]) == sorted([a, b])


def test_split_then_merge_round_trips(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    split = api_client.post("/api/payments/split", json={"doc_a": a, "doc_b": b})
    assert split.status_code == 200
    assert [d["id"] for d in split.json()["documents"]] == [a]

    merge = api_client.post("/api/payments/merge", json={"doc_a": a, "doc_b": b})
    assert merge.status_code == 200
    assert sorted(d["id"] for d in merge.json()["documents"]) == sorted([a, b])


def test_an_override_on_one_document_is_rejected(api_client: TestClient) -> None:
    assert api_client.post("/api/payments/merge", json={"doc_a": 5, "doc_b": 5}).status_code == 422


def test_an_unknown_document_is_a_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/99999999/payment").status_code == 404


def test_duplicates_lists_the_collapsed_group(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    groups = api_client.get("/api/payments/duplicates").json()["groups"]
    assert any(sorted(g["document_ids"]) == sorted([a, b]) for g in groups)


def test_anonymous_access_is_refused(anon_client: TestClient) -> None:
    assert anon_client.get("/api/payments/duplicates").status_code in (401, 403)
```

Add this fixture to `tests/conftest.py`:

```python
@pytest.fixture
def payment_pair(api_database_url: str) -> tuple[int, int]:
    """Two documents the R1 rule merges into one payment: same sender, date,
    amount and currency, with complementary amount kinds."""
    import asyncio
    import hashlib
    import uuid as _uuid
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from library.models import (
        AmountKind,
        Document,
        DocumentSource,
        DocumentStatus,
        Sender,
    )

    async def _seed() -> tuple[int, int]:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                sender = Sender(name=f"PayVendor-{_uuid.uuid4().hex[:8]}")
                session.add(sender)
                await session.flush()
                ids: list[int] = []
                for kind in (AmountKind.PAYMENT_DUE, AmountKind.PAYMENT_MADE):
                    marker = f"paypair:{_uuid.uuid4()}"
                    doc = Document(
                        sha256=hashlib.sha256(marker.encode()).hexdigest(),
                        mime_type="application/pdf",
                        source=DocumentSource.UPLOAD,
                        status=DocumentStatus.INDEXED,
                        title=marker,
                        sender_id=sender.id,
                        document_date=date(2026, 8, 4),
                        amount_total=Decimal("48.00"),
                        currency="EUR",
                        amount_kind=kind,
                    )
                    session.add(doc)
                    await session.flush()
                    ids.append(doc.id)
                await session.commit()
                return ids[0], ids[1]
        finally:
            await engine.dispose()

    return asyncio.run(_seed())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_payments.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Write the router**

Create `src/library/api/payments.py`:

```python
"""Payment identity endpoints.

`/api/payments/duplicates` is the review surface this layer exists for: it makes
the archive's double-counted documents visible and correctable before any chart
is built on top of them. Authentication is enforced at include level in app.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.models import Document
from library.money.payments import add_override, payment_group, payment_id_for

router: APIRouter = APIRouter(tags=["payments"])


class OverrideRequest(BaseModel):
    doc_a: int
    doc_b: int

    @model_validator(mode="after")
    def _distinct(self) -> "OverrideRequest":
        if self.doc_a == self.doc_b:
            raise ValueError("doc_a and doc_b must be different documents")
        return self


class PaymentDocument(BaseModel):
    id: int
    title: str | None
    document_date: str | None
    amount_kind: str | None
    reference: str | None


class PaymentOut(BaseModel):
    payment_id: int
    documents: list[PaymentDocument]


async def _payment_body(session: AsyncSession, document_id: int) -> PaymentOut:
    payment_id = await payment_id_for(session, document_id)
    if payment_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown document")
    ids = await payment_group(session, document_id)
    rows = (
        await session.execute(
            select(
                Document.id,
                Document.title,
                Document.document_date,
                Document.amount_kind,
                Document.reference,
            )
            .where(Document.id.in_(ids))
            .order_by(Document.id)
        )
    ).all()
    return PaymentOut(
        payment_id=payment_id,
        documents=[
            PaymentDocument(
                id=row.id,
                title=row.title,
                document_date=row.document_date.isoformat() if row.document_date else None,
                amount_kind=row.amount_kind.value if row.amount_kind else None,
                reference=row.reference,
            )
            for row in rows
        ],
    )


@router.get("/documents/{document_id}/payment", summary="The payment this document belongs to")
async def get_payment(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    return await _payment_body(session, document_id)


@router.post("/payments/merge", summary="Record that two documents are one payment")
async def merge_payment(
    body: OverrideRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    await add_override(session, "MERGE", body.doc_a, body.doc_b)
    await session.commit()
    return await _payment_body(session, body.doc_a)


@router.post("/payments/split", summary="Record that two documents are separate payments")
async def split_payment(
    body: OverrideRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    await add_override(session, "SPLIT", body.doc_a, body.doc_b)
    await session.commit()
    return await _payment_body(session, body.doc_a)


@router.get("/payments/duplicates", summary="Documents that describe one payment")
async def list_duplicates(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict[str, object]]]:
    result = await session.execute(
        text(
            "SELECT payment_id, array_agg(document_id ORDER BY document_id) AS ids, "
            "count(*) AS n FROM payments GROUP BY payment_id HAVING count(*) > 1 "
            "ORDER BY n DESC, payment_id LIMIT 100"
        )
    )
    return {
        "groups": [
            {"payment_id": int(row.payment_id), "document_ids": list(row.ids), "count": int(row.n)}
            for row in result.all()
        ]
    }
```

- [ ] **Step 4: Register the router**

In `src/library/app.py`, beside the other includes: `api_router.include_router(payments.router)`,
and add `payments` to the `from library.api import (...)` list.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api_payments.py -v` → 6 passed.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/library/api/payments.py src/library/app.py tests/ 
uv run ruff check src/ tests/ migrations/
git add src/library/api/payments.py src/library/app.py tests/conftest.py tests/test_api_payments.py
git commit -m "feat(money): payment group, override and duplicate endpoints"
```

---

### Task 6: Show the payment group on a document

**Files:**
- Create: `frontend/src/api/payments.ts`, `frontend/src/components/payments/PaymentGroup.vue`
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/components/payments/__tests__/PaymentGroup.spec.ts`

**Interfaces:**
- Consumes: the Task 5 routes.
- Produces:
  - `interface PaymentDocumentRef { id: number; title: string | null; document_date: string | null; amount_kind: string | null; reference: string | null }`
  - `fetchPayment(id: number): Promise<{ payment_id: number; documents: PaymentDocumentRef[] }>`
  - `mergePayment(a: number, b: number)`, `splitPayment(a: number, b: number)` with the same return shape
  - `PaymentGroup` props `{ documentId: number }`

The component renders **nothing** when the group is just this document — a
"1 document" panel is noise on every page. It appears only when there is a
collapse to explain.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/payments/__tests__/PaymentGroup.spec.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PaymentGroup from '../PaymentGroup.vue'

const fetchPayment = vi.fn()
const splitPayment = vi.fn()
vi.mock('@/api/payments', () => ({
  fetchPayment: (...a: unknown[]) => fetchPayment(...a),
  splitPayment: (...a: unknown[]) => splitPayment(...a),
  mergePayment: vi.fn(),
}))

const PAIR = {
  payment_id: 7,
  documents: [
    { id: 7, title: 'Invoice', document_date: '2026-08-04', amount_kind: 'payment_due', reference: null },
    { id: 8, title: 'Receipt', document_date: '2026-08-04', amount_kind: 'payment_made', reference: null },
  ],
}

beforeEach(() => {
  fetchPayment.mockReset()
  splitPayment.mockReset()
})

describe('PaymentGroup', () => {
  it('renders nothing when the document is alone in its payment', async () => {
    fetchPayment.mockResolvedValue({ payment_id: 7, documents: [PAIR.documents[0]] })
    const wrapper = mount(PaymentGroup, { props: { documentId: 7 } })
    await flushPromises()
    expect(wrapper.find('[data-testid="payment-group"]').exists()).toBe(false)
  })

  it('lists both documents when two are collapsed into one payment', async () => {
    fetchPayment.mockResolvedValue(PAIR)
    const wrapper = mount(PaymentGroup, { props: { documentId: 7 } })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="payment-group-row"]')).toHaveLength(2)
  })

  it('splits the pair and re-renders from the response', async () => {
    fetchPayment.mockResolvedValue(PAIR)
    splitPayment.mockResolvedValue({ payment_id: 7, documents: [PAIR.documents[0]] })
    const wrapper = mount(PaymentGroup, { props: { documentId: 7 } })
    await flushPromises()
    await wrapper.get('[data-testid="payment-split"]').trigger('click')
    await flushPromises()
    expect(splitPayment).toHaveBeenCalledWith(7, 8)
    expect(wrapper.find('[data-testid="payment-group"]').exists()).toBe(false)
  })

  it('surfaces a load failure instead of rendering an empty panel', async () => {
    fetchPayment.mockRejectedValue(new Error('nope'))
    const wrapper = mount(PaymentGroup, { props: { documentId: 7 } })
    await flushPromises()
    expect(wrapper.get('[data-testid="payment-error"]').text()).toContain('Could not load')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/payments/`
Expected: FAIL — the component does not exist.

- [ ] **Step 3: Write the client and component**

Create `frontend/src/api/payments.ts`:

```ts
/** Typed API for payment identity (docs/money-facts.md). */

import { apiFetch } from './client'

export interface PaymentDocumentRef {
  id: number
  title: string | null
  document_date: string | null
  amount_kind: string | null
  reference: string | null
}

export interface PaymentRef {
  payment_id: number
  documents: PaymentDocumentRef[]
}

export function fetchPayment(id: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>(`/api/documents/${id}/payment`)
}

export function mergePayment(docA: number, docB: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>('/api/payments/merge', {
    method: 'POST',
    body: JSON.stringify({ doc_a: docA, doc_b: docB }),
  })
}

export function splitPayment(docA: number, docB: number): Promise<PaymentRef> {
  return apiFetch<PaymentRef>('/api/payments/split', {
    method: 'POST',
    body: JSON.stringify({ doc_a: docA, doc_b: docB }),
  })
}
```

Create `frontend/src/components/payments/PaymentGroup.vue`:

```vue
<script setup lang="ts">
/**
 * "This and N other documents describe one payment."
 *
 * Renders nothing when the group is just this document — a one-row panel on
 * every page is noise. It appears only when there is a collapse to explain, and
 * offers the split that corrects a wrong one.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchPayment, splitPayment, type PaymentRef } from '@/api/payments'

const props = defineProps<{ documentId: number }>()

const payment = ref<PaymentRef | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)

const collapsed = computed<boolean>(() => (payment.value?.documents.length ?? 0) > 1)

async function load(): Promise<void> {
  error.value = null
  try {
    payment.value = await fetchPayment(props.documentId)
  } catch {
    error.value = 'Could not load this payment.'
  }
}

async function split(otherId: number): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    payment.value = await splitPayment(props.documentId, otherId)
  } catch {
    error.value = 'Could not split these documents. Try again.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
watch(() => props.documentId, load)
</script>

<template>
  <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400" data-testid="payment-error">
    {{ error }}
  </p>

  <section v-else-if="collapsed" class="card p-4" data-testid="payment-group">
    <h3 class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
      One payment, {{ payment!.documents.length }} documents
    </h3>
    <ul class="mt-2 space-y-1">
      <li
        v-for="doc in payment!.documents"
        :key="doc.id"
        class="flex items-center justify-between gap-3 text-sm"
        data-testid="payment-group-row"
      >
        <RouterLink :to="`/documents/${doc.id}`" class="min-w-0 truncate hover:underline">
          {{ doc.title ?? `Document #${doc.id}` }}
        </RouterLink>
        <span class="shrink-0 text-xs text-gray-500 dark:text-gray-400">
          {{ doc.amount_kind ?? 'unclassified' }}
        </span>
        <button
          v-if="doc.id !== documentId"
          type="button"
          class="btn-xs shrink-0 text-violet-600 hover:text-violet-700 dark:text-violet-400"
          data-testid="payment-split"
          :disabled="busy"
          @click="split(doc.id)"
        >
          Not the same payment
        </button>
      </li>
    </ul>
  </section>
</template>
```

- [ ] **Step 4: Mount it**

In `frontend/src/views/DocumentDetailView.vue`, render
`<PaymentGroup :document-id="document.id" />` in the metadata column.

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/components/payments/` → 4 passed.
Run: `cd frontend && npx vue-tsc --noEmit` → clean.

- [ ] **Step 6: Commit**

```bash
cd frontend && npx prettier --write src/api/payments.ts src/components/payments/
cd .. && git add frontend/src/api/payments.ts frontend/src/components/payments/ frontend/src/views/DocumentDetailView.vue
git commit -m "feat(money): show a document's payment group with a split control"
```

---

### Task 7: Documentation and journal

**Files:**
- Create: `docs/money-facts.md`, `journal/260828-money-facts.md`
- Modify: `docs/README.md`, `docs/api.md`

- [ ] **Step 1: Write `docs/money-facts.md`**

H1 is a clean title with no number or date. Cover: the seven `amount_kind`
values and which three are summable; why `NULL` means "not decided" and is
treated as not-summable; `reference` and why it is the only date-independent
pairing evidence; the four rules and the VETO, **with the reasoning that R3's
complementarity is what makes date tolerance safe**; the two known limits
(partial payments, cross-currency settlement); the override table; and
`library backfill-amounts`.

**No real sender names or amounts.** Illustrate with invented ones.

- [ ] **Step 2: Document the API**

Add the Task 5 routes to `docs/api.md` in its existing style.

- [ ] **Step 3: Journal entry**

`journal/260828-money-facts.md`: why `amount_total` alone was not enough (counts
only — 20 duplicate groups covering 40 of 174 amount-bearing documents), the
finding that all 20 were one event documented twice, why a date window cannot
work in either direction, and the complementarity insight that resolved it.

- [ ] **Step 4: Link and commit**

```bash
make check-docs
git add docs/ journal/
git commit -m "docs(money): document amount semantics and payment identity"
```

---

## Done when

- [ ] `uv run pytest -q` passes in full.
- [ ] `uv run ruff check src/ tests/ migrations/` and `uv run ruff format --check .` are clean.
- [ ] `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` succeeds with the views in place.
- [ ] `cd frontend && npx vitest run && npx vue-tsc --noEmit` passes.
- [ ] `uv run library backfill-amounts --limit 5` classifies documents, and `GET /api/payments/duplicates` reports the collapsed groups.
- [ ] No real names or amounts anywhere in the diff.
