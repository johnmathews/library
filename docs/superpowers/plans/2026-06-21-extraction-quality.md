# Extraction Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extraction trustworthy and measurable — a per-document trust signal (validation rules → `review_status` + inline badges + review queue) and an aggregate accuracy harness (corrections flywheel + LLM-as-judge via `library eval-extractions`).

**Architecture:** Pure, deterministic validation rules run at the end of `apply_extraction` and set a new indexed `review_status` column plus a findings list in `extra["validation"]`. User corrections are recorded in `extra["corrections"]` in a mining-ready shape. A batch-only LLM judge and pure scoring functions back a CLI eval command that persists results to a new `eval_runs` table, pinned to `prompt_version` + `model`. The Vue SPA gains a "needs review" filter/preset and per-field warning badges.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, Typer, Anthropic SDK (`messages.parse` structured outputs), pytest; Vue 3 (`<script setup>`), Pinia, vue-router, Vitest, Playwright. Package manager: `uv`.

Spec: `docs/superpowers/specs/2026-06-21-extraction-quality-design.md`.

## Global Constraints

- Python type annotations on all signatures and non-obvious variables; target 3.13.
- Use `uv` for everything: tests via `uv run pytest`, frontend via `npm` inside `frontend/`.
- **Extraction never fails a document** — validation is best-effort; any error in validation must not raise out of `apply_extraction` or change document status away from the normal pipeline outcome.
- Pure logic modules (`validation.py`, `eval.py`) import only stdlib + models; no DB/IO/network — so they are unit-testable without Postgres.
- The LLM judge is **batch-only**; never call it from `apply_extraction` or any pipeline job.
- Migrations follow the existing numbered Alembic style (`0006`, `down_revision="0005"`), use `op.f(...)` constraint names, and have a working `downgrade()`.
- Enum columns mirror the existing pattern: `Enum(..., native_enum=False, length=16, values_callable=lambda obj: [m.value for m in obj])`.
- Frontend filter state lives in the URL query and flows through `frontend/src/utils/documentQuery.ts` (`parseDocumentQuery` / `buildDocumentQuery` / `hasActiveFilters`) — extend those, do not invent a parallel state path.
- New config settings use the `LIBRARY_` env prefix via `Settings` in `src/library/config.py`.
- Commit after each task (TDD: failing test → implement → passing test → commit). Branch: `feat/extraction-quality` (already created).

## Tracked extraction fields (used by validation, corrections, and eval)

These are the storage-level field names referenced throughout. Keep them identical everywhere:

```
kind_id, sender_id, title, summary, document_date, due_date, expiry_date,
amount_total, currency, language, tags
```

(`extra["extraction"]["fields_set"]` already uses these names; PATCH maps `kind_slug`→`kind_id`, `sender`→`sender_id`.)

---

## Task 1: ReviewStatus enum + pure validation module

**Files:**
- Modify: `src/library/models.py` (add `ReviewStatus` enum near `DocumentLanguage`, ~line 87)
- Create: `src/library/extraction/validation.py`
- Test: `tests/test_extraction_validation.py`

**Interfaces:**
- Produces:
  - `ReviewStatus` (StrEnum): `VERIFIED="verified"`, `NEEDS_REVIEW="needs_review"`, `UNREVIEWED="unreviewed"`
  - `Finding` (frozen dataclass, slots): `rule: str`, `field: str | None`, `severity: str`, `message: str`
  - `validate(document: Document, *, kind_slug: str | None, ocr_floor: float, today: date) -> list[Finding]`
  - `derive_review_status(findings: list[Finding]) -> ReviewStatus`
  - `findings_to_payload(findings: list[Finding]) -> list[dict[str, Any]]`

- [ ] **Step 1: Add the `ReviewStatus` enum to models.py**

In `src/library/models.py`, after the `DocumentLanguage` class (ends ~line 93):

```python
class ReviewStatus(enum.StrEnum):
    """Trust state of a document's extracted metadata."""

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    UNREVIEWED = "unreviewed"
```

- [ ] **Step 2: Write the failing tests for validation rules**

Create `tests/test_extraction_validation.py`:

```python
"""Unit tests for the pure extraction-validation rules (no DB, no IO)."""

from datetime import date
from decimal import Decimal

from library.extraction.validation import (
    Finding,
    derive_review_status,
    findings_to_payload,
    validate,
)
from library.models import Document, ReviewStatus

TODAY = date(2026, 6, 21)
FLOOR = 50.0


def _doc(**kwargs: object) -> Document:
    """A transient Document carrying only the attributes a rule reads.

    Built via the normal SQLAlchemy constructor (NOT __new__ + setattr):
    mapped columns are data descriptors, so an instance-dict value set with
    object.__setattr__ would be ignored on read. Unset scalar columns read
    back as None on a transient instance, which is what the rules expect.
    """
    defaults: dict[str, object] = {"ocr_text": "", "extra": {}}
    defaults.update(kwargs)
    return Document(**defaults)


def _rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


def test_amount_grounded_in_text_does_not_fire() -> None:
    doc = _doc(amount_total=Decimal("12.00"), currency="EUR", ocr_text="Totaal € 12,00 voldaan")
    assert "amount_grounding" not in _rules(validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY))


def test_amount_absent_from_text_fires() -> None:
    doc = _doc(amount_total=Decimal("999.99"), currency="EUR", ocr_text="Totaal € 12,00 voldaan")
    assert "amount_grounding" in _rules(validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY))


def test_future_document_date_fires() -> None:
    doc = _doc(document_date=date(2027, 1, 1))
    assert "date_plausibility" in _rules(validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY))


def test_due_before_document_date_fires() -> None:
    doc = _doc(document_date=date(2026, 5, 1), due_date=date(2026, 4, 1))
    assert "date_plausibility" in _rules(validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY))


def test_amount_without_currency_fires() -> None:
    doc = _doc(amount_total=Decimal("10.00"), currency=None, ocr_text="10.00")
    assert "amount_currency_coupling" in _rules(validate(doc, kind_slug="receipt", ocr_floor=FLOOR, today=TODAY))


def test_low_ocr_confidence_fires() -> None:
    doc = _doc(ocr_confidence=30.0)
    assert "ocr_confidence_gate" in _rules(validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY))


def test_empty_extraction_fires() -> None:
    doc = _doc()  # kind other, no sender, no date, no amount
    assert "empty_extraction" in _rules(validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY))


def test_self_reported_low_confidence_fires() -> None:
    doc = _doc(extra={"extraction": {"confidence": "low"}})
    assert "self_reported_low" in _rules(validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY))


def test_clean_document_has_no_findings() -> None:
    doc = _doc(
        amount_total=Decimal("12.00"),
        currency="EUR",
        document_date=date(2026, 5, 1),
        ocr_text="Factuur totaal € 12,00",
        ocr_confidence=90.0,
        sender_id=7,
        extra={"extraction": {"confidence": "high"}},
    )
    assert validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY) == []


def test_derive_status_and_payload() -> None:
    assert derive_review_status([]) is ReviewStatus.UNREVIEWED
    findings = [Finding(rule="empty_extraction", field=None, severity="warn", message="x")]
    assert derive_review_status(findings) is ReviewStatus.NEEDS_REVIEW
    assert findings_to_payload(findings) == [
        {"rule": "empty_extraction", "field": None, "severity": "warn", "message": "x"}
    ]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extraction_validation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.extraction.validation'`.

- [ ] **Step 4: Implement the validation module**

Create `src/library/extraction/validation.py`:

```python
"""Deterministic, zero-cost validation of extracted metadata.

Each rule inspects already-extracted fields and the source OCR text and may
emit a :class:`Finding`. The module is pure (stdlib + models only) so it is
unit-testable without a database. Date-grounding is deliberately out of scope
this phase (see the design spec).
"""

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from library.models import Document, ReviewStatus

_MIN_PLAUSIBLE_DATE: date = date(1990, 1, 1)


@dataclass(frozen=True, slots=True)
class Finding:
    """One validation concern about a document's extracted metadata."""

    rule: str
    field: str | None
    severity: str  # "warn" for now; reserved for future "error"
    message: str


def _amount_appears(amount: Decimal, text: str) -> bool:
    """True if the amount's digits appear in the OCR text.

    Compares on digit sequences so "12,00", "€ 12.00" and "12.00" all match a
    stored ``Decimal("12.00")``. Falls back to the integer part to tolerate
    totals printed without decimals. Lenient by design: a false "present" is
    safer than nagging on a correct amount.
    """
    text_digits = re.sub(r"\D", "", text)
    if not text_digits:
        return False
    cents = f"{amount:.2f}".replace(".", "")
    integer = str(int(amount))
    return cents in text_digits or integer in text_digits


def validate(
    document: Document,
    *,
    kind_slug: str | None,
    ocr_floor: float,
    today: date,
) -> list[Finding]:
    """Run every rule against a document; return the findings that fired."""
    findings: list[Finding] = []
    text = document.ocr_text or ""

    # amount_grounding — amount set but its digits are absent from the text.
    if document.amount_total is not None and text and not _amount_appears(document.amount_total, text):
        findings.append(
            Finding(
                rule="amount_grounding",
                field="amount_total",
                severity="warn",
                message="amount_total does not appear in the document text",
            )
        )

    # date_plausibility — future, ancient, or due/expiry before the document date.
    doc_date = document.document_date
    if doc_date is not None:
        if doc_date > today:
            findings.append(Finding("date_plausibility", "document_date", "warn", "document_date is in the future"))
        elif doc_date < _MIN_PLAUSIBLE_DATE:
            findings.append(Finding("date_plausibility", "document_date", "warn", "document_date is implausibly old"))
        if document.due_date is not None and document.due_date < doc_date:
            findings.append(Finding("date_plausibility", "due_date", "warn", "due_date is before document_date"))
        if document.expiry_date is not None and document.expiry_date < doc_date:
            findings.append(Finding("date_plausibility", "expiry_date", "warn", "expiry_date is before document_date"))

    # amount_currency_coupling — exactly one of amount/currency is set.
    if (document.amount_total is None) != (document.currency is None):
        findings.append(
            Finding("amount_currency_coupling", "currency", "warn", "amount_total and currency must be set together")
        )

    # ocr_confidence_gate — extraction built on low-confidence OCR.
    if document.ocr_confidence is not None and document.ocr_confidence < ocr_floor:
        findings.append(
            Finding("ocr_confidence_gate", None, "warn", f"OCR confidence {document.ocr_confidence:.0f} below floor {ocr_floor:.0f}")
        )

    # empty_extraction — kind=other and nothing else learned.
    if (
        (kind_slug is None or kind_slug == "other")
        and document.sender_id is None
        and document.document_date is None
        and document.amount_total is None
    ):
        findings.append(Finding("empty_extraction", None, "warn", "extraction produced no useful metadata"))

    # self_reported_low — the model said it was unsure.
    extraction = document.extra.get("extraction") if isinstance(document.extra, dict) else None
    if isinstance(extraction, dict) and extraction.get("confidence") == "low":
        findings.append(Finding("self_reported_low", None, "warn", "model reported low confidence"))

    return findings


def derive_review_status(findings: list[Finding]) -> ReviewStatus:
    """Any finding ⇒ needs_review; otherwise unreviewed (never auto-verified)."""
    return ReviewStatus.NEEDS_REVIEW if findings else ReviewStatus.UNREVIEWED


def findings_to_payload(findings: list[Finding]) -> list[dict[str, Any]]:
    """Serialise findings for storage in ``extra["validation"]``."""
    return [asdict(f) for f in findings]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extraction_validation.py -q`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/library/models.py src/library/extraction/validation.py tests/test_extraction_validation.py
git commit -m "feat(extraction): pure validation rules + ReviewStatus enum"
```

---

## Task 2: Migration 0006 — review_status column + eval_runs table + models

**Files:**
- Modify: `src/library/models.py` (add `review_status` column to `Document`; add `EvalRun` model after `AskLog`)
- Create: `migrations/versions/0006_extraction_quality.py`
- Test: `tests/test_models.py` (extend), `tests/test_migrations.py` already round-trips — no change needed beyond it passing

**Interfaces:**
- Produces:
  - `Document.review_status: Mapped[ReviewStatus]` (indexed, default `unreviewed`)
  - `EvalRun` model: `id`, `created_at`, `prompt_version: str`, `model: str`, `version_mix: dict`, `sample_size: int`, `per_field: dict`, `overall: dict`

- [ ] **Step 1: Add the `review_status` column to the Document model**

In `src/library/models.py`, inside `class Document`, after the `extra` column (~line 237). Ensure `ReviewStatus` is referenced and `Enum` is already imported (it is):

```python
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            name="review_status",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=ReviewStatus.UNREVIEWED,
        server_default=ReviewStatus.UNREVIEWED.value,
        index=True,
    )
```

- [ ] **Step 2: Add the `EvalRun` model**

In `src/library/models.py`, after `class AskLog` (end of file):

```python
class EvalRun(Base):
    """One extraction-quality evaluation run, comparable across versions.

    ``prompt_version``/``model`` hold the modal (most common) pair across the
    evaluated documents for easy filtering; ``version_mix`` records the full
    distribution so a sample spanning versions is never silently misattributed.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    prompt_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    version_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    per_field: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    overall: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
```

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0006_extraction_quality.py`:

```python
"""extraction quality

Add documents.review_status and the eval_runs table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVIEW_STATUS = sa.Enum(
    "verified",
    "needs_review",
    "unreviewed",
    name="review_status",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "review_status",
            _REVIEW_STATUS,
            server_default="unreviewed",
            nullable=False,
        ),
    )
    op.create_index("ix_documents_review_status", "documents", ["review_status"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("version_mix", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("per_field", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("overall", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_index("ix_documents_review_status", table_name="documents")
    op.drop_column("documents", "review_status")
```

- [ ] **Step 4: Add a model test for the new column default**

Append to `tests/test_models.py` (follow the existing fixture style in that file; it already constructs and persists a `Document`). Add:

```python
async def test_document_defaults_to_unreviewed(session: AsyncSession) -> None:
    from library.models import ReviewStatus

    doc = Document(sha256="a" * 64, mime_type="text/plain", source=DocumentSource.UPLOAD)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    assert doc.review_status is ReviewStatus.UNREVIEWED
```

(Match the imports/fixtures already present in `tests/test_models.py`; if it lacks a `session` fixture, reuse the one it already uses for its existing document test.)

- [ ] **Step 5: Run migration round-trip + model tests**

Run: `uv run pytest tests/test_migrations.py tests/test_models.py -q`
Expected: PASS — `test_migrations.py` upgrades head→base→head cleanly with 0006; the new default test passes.

- [ ] **Step 6: Commit**

```bash
git add src/library/models.py migrations/versions/0006_extraction_quality.py tests/test_models.py
git commit -m "feat(db): review_status column + eval_runs table (migration 0006)"
```

---

## Task 3: Wire validation into apply_extraction + config floor

**Files:**
- Modify: `src/library/config.py` (add `extraction_validation_ocr_floor`)
- Modify: `src/library/extraction/apply.py` (run validation at the end of `apply_extraction`)
- Test: `tests/test_extraction_apply.py` (extend)

**Interfaces:**
- Consumes: `validate`, `derive_review_status`, `findings_to_payload` (Task 1); `Settings.extraction_validation_ocr_floor`
- Produces: after a successful extraction, `document.review_status` is set and `document.extra["validation"] = {"prompt_version", "findings", "validated_at"}`

- [ ] **Step 1: Add the config setting**

In `src/library/config.py`, in the Claude-extraction block (~line 38):

```python
    extraction_validation_ocr_floor: float = 50.0
```

- [ ] **Step 2: Write the failing integration test**

Add to `tests/test_extraction_apply.py` (reuse its existing `session_factory`, `data_dir`, `settings`, and the helper that stubs `extract` — search the file for how it patches `apply_module`/`extract`; mirror that). Two cases:

```python
async def test_apply_sets_needs_review_on_flagged_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future document_date makes the document needs_review with a finding."""
    metadata = ExtractedMetadata(
        kind_slug="invoice", sender_name="Eneco", title="t", summary="s",
        document_date=date(2099, 1, 1), amount_total="10.00", currency="EUR",
        due_date=None, expiry_date=None, language="nld", tags=[], confidence="high",
        reasoning_note=None,
    )
    outcome = ExtractionOutcome(
        metadata=metadata, model="claude-haiku-4-5", prompt_version=PROMPT_VERSION,
        input_mode="text", escalated=False,
        calls=[CallUsage("claude-haiku-4-5", 10, 10, 0.0)],
    )

    async def fake_extract(document, ocr_text, *, client, settings):  # noqa: ANN001
        return outcome

    monkeypatch.setattr(apply_module, "extract", fake_extract)

    async with session_factory() as session:
        doc = Document(sha256="d" * 64, mime_type="text/plain", source=DocumentSource.UPLOAD,
                       ocr_text="Factuur Eneco totaal 10,00", extra={})
        session.add(doc)
        await session.commit()
        await apply_extraction(session, doc, settings)
        await session.refresh(doc)

    from library.models import ReviewStatus
    assert doc.review_status is ReviewStatus.NEEDS_REVIEW
    rules = {f["rule"] for f in doc.extra["validation"]["findings"]}
    assert "date_plausibility" in rules


async def test_apply_sets_unreviewed_on_clean_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = ExtractedMetadata(
        kind_slug="invoice", sender_name="Eneco", title="t", summary="s",
        document_date=date(2026, 5, 1), amount_total="10.00", currency="EUR",
        due_date=None, expiry_date=None, language="nld", tags=[], confidence="high",
        reasoning_note=None,
    )
    outcome = ExtractionOutcome(
        metadata=metadata, model="claude-haiku-4-5", prompt_version=PROMPT_VERSION,
        input_mode="text", escalated=False,
        calls=[CallUsage("claude-haiku-4-5", 10, 10, 0.0)],
    )

    async def fake_extract(document, ocr_text, *, client, settings):  # noqa: ANN001
        return outcome

    monkeypatch.setattr(apply_module, "extract", fake_extract)

    async with session_factory() as session:
        doc = Document(sha256="e" * 64, mime_type="text/plain", source=DocumentSource.UPLOAD,
                       ocr_text="Factuur Eneco totaal 10,00 EUR", ocr_confidence=95.0, extra={})
        session.add(doc)
        await session.commit()
        await apply_extraction(session, doc, settings)
        await session.refresh(doc)

    from library.models import ReviewStatus
    assert doc.review_status is ReviewStatus.UNREVIEWED
    assert doc.extra["validation"]["findings"] == []
```

> Note: `Settings()` in the existing `settings` fixture must now also produce `extraction_validation_ocr_floor=50.0` (the default), so a 95.0 OCR confidence does not trip the gate.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extraction_apply.py -k review -q`
Expected: FAIL — `KeyError: 'validation'` / `AttributeError` (review_status not set).

- [ ] **Step 4: Implement validation wiring in apply.py**

In `src/library/extraction/apply.py`:

1. Add imports at the top:

```python
from datetime import UTC, date, datetime  # extend the existing datetime import line
from sqlalchemy import select  # already imported; ensure Kind is imported (it is)
from library.extraction.validation import derive_review_status, findings_to_payload, validate
from library.models import ReviewStatus  # extend the existing models import
```

2. Add a helper after `_apply_outcome`:

```python
async def _apply_validation(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Run deterministic validation and set review_status + extra["validation"].

    Best-effort: a failure here must never propagate (extraction never fails a
    document). Skips any field locked by the user is unnecessary — validation
    reads the document's *current* values, whatever their provenance.
    """
    kind_slug: str | None = None
    if document.kind_id is not None:
        kind = await session.get(Kind, document.kind_id)
        kind_slug = kind.slug if kind is not None else None

    findings = validate(
        document,
        kind_slug=kind_slug,
        ocr_floor=settings.extraction_validation_ocr_floor,
        today=datetime.now(UTC).date(),
    )
    document.review_status = derive_review_status(findings)
    document.extra = {
        **document.extra,
        "validation": {
            "prompt_version": PROMPT_VERSION,
            "findings": findings_to_payload(findings),
            "validated_at": datetime.now(UTC).isoformat(),
        },
    }
```

3. In `apply_extraction`, after `fields_set = await _apply_outcome(session, document, outcome)` and **before** the `extraction_completed` `_record_event`, insert:

```python
    try:
        await _apply_validation(session, document, settings)
    except Exception:  # validation is best-effort; never fail the document
        logger.exception("validation failed for document %s", document.id)
```

(The existing `_record_event` commits the session, persisting both the outcome and the validation in one transaction.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extraction_apply.py -q`
Expected: PASS (new review tests and all existing apply tests).

- [ ] **Step 6: Commit**

```bash
git add src/library/config.py src/library/extraction/apply.py tests/test_extraction_apply.py
git commit -m "feat(extraction): run validation in apply_extraction, set review_status"
```

---

## Task 4: `library backfill-validation` CLI command

**Files:**
- Modify: `src/library/cli.py` (add command after `backfill_embeddings`)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `validate`, `derive_review_status`, `findings_to_payload`, `ReviewStatus`
- Produces: CLI `library backfill-validation [--limit N]` that recomputes `review_status` + `extra["validation"]` for indexed, non-deleted documents

- [ ] **Step 1: Write the failing CLI test**

Add to `tests/test_cli.py` (mirror its existing DB-backed command tests; it already has fixtures that create documents and invoke `app` via `typer.testing.CliRunner` or `_run`-style helpers — match whichever the file uses):

```python
def test_backfill_validation_sets_review_status(api_database_url, ...):  # match existing signature style
    # Arrange: insert a document with a future document_date and empty extra.
    # Act: invoke `backfill-validation`.
    # Assert: the row's review_status == "needs_review" and extra["validation"]["findings"] is non-empty.
```

> Implement the arrange/assert using the same DB-access helper the other `tests/test_cli.py` cases use (e.g. `fetch_all` from conftest, or a direct async session). Insert one document with `document_date` in the future and one clean document; assert the first becomes `needs_review` and the second `unreviewed`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -k backfill_validation -q`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement the command**

In `src/library/cli.py`, add imports (`from datetime import UTC, datetime`, `from library.extraction.validation import derive_review_status, findings_to_payload, validate`, `from library.models import Kind, ReviewStatus`) and the command after `backfill_embeddings`:

```python
@app.command("backfill-validation")
def backfill_validation(
    limit: int | None = typer.Option(None, "--limit", min=1, help="Only process the first N documents."),
) -> None:
    """Recompute review_status + validation findings for existing documents.

    Idempotent: re-running recomputes from current field values. Use after
    deploying new validation rules or to seed the queue on an existing corpus.
    """
    floor = get_settings().extraction_validation_ocr_floor

    async def operation(session: AsyncSession) -> int:
        statement = select(Document).where(Document.deleted_at.is_(None)).order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        documents = list((await session.execute(statement)).scalars().all())
        today = datetime.now(UTC).date()
        for document in documents:
            kind_slug = None
            if document.kind_id is not None:
                kind = await session.get(Kind, document.kind_id)
                kind_slug = kind.slug if kind is not None else None
            findings = validate(document, kind_slug=kind_slug, ocr_floor=floor, today=today)
            document.review_status = derive_review_status(findings)
            document.extra = {
                **document.extra,
                "validation": {
                    "prompt_version": "backfill",
                    "findings": findings_to_payload(findings),
                    "validated_at": datetime.now(UTC).isoformat(),
                },
            }
        await session.commit()
        return len(documents)

    count = _run(operation)
    typer.echo(f"revalidated {count} document(s)")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -k backfill_validation -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/cli.py tests/test_cli.py
git commit -m "feat(cli): backfill-validation command to seed review_status"
```

---

## Task 5: Corrections flywheel — record edits in extra["corrections"]

**Files:**
- Modify: `src/library/api/documents.py` (`update_document`, ~lines 256–316; add a helper)
- Test: `tests/test_documents_api.py` (extend)

**Interfaces:**
- Produces: after a PATCH, `extra["corrections"]` gains one record per edited field: `{field, original_value, corrected_value, source_excerpt, prompt_version, model, corrected_at}`. `field` uses storage names (`kind_id`, `sender_id`, `tags`, scalars).

- [ ] **Step 1: Write the failing API test**

Add to `tests/test_documents_api.py` (reuse its existing `api_client` + document-creation helpers):

```python
def test_patch_records_correction(api_client, ...):  # match existing style
    # Create a document with an extracted amount_total and an extraction blob
    # carrying prompt_version + model; PATCH amount_total to a new value.
    # Assert extra["corrections"] (via GET detail's events or a DB fetch) has a
    # record: field="amount_total", original_value=<old>, corrected_value=<new>,
    # and prompt_version/model copied from extra["extraction"].
```

> Use the same persistence/inspection approach as neighbouring tests. If detail does not expose `extra["corrections"]`, assert via the `fetch_all` conftest helper against the `documents.extra` JSONB.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_documents_api.py -k correction -q`
Expected: FAIL — no corrections recorded.

- [ ] **Step 3: Implement correction recording**

In `src/library/api/documents.py`:

1. Add a helper near `_EDITED_FIELD_NAMES`:

```python
def _correction_records(
    document: Document, originals: dict[str, Any], edited: list[str]
) -> list[dict[str, Any]]:
    """Build mining-ready correction records for the fields just edited.

    ``originals`` maps storage field name -> value before the edit. Values are
    JSON-stringified (dates/Decimals -> str) so the records survive in JSONB.
    """
    extraction = document.extra.get("extraction") or {}
    text = document.ocr_text or ""

    def jsonable(value: Any) -> Any:
        return None if value is None else str(value)

    def excerpt(value: Any) -> str:
        needle = "" if value is None else str(value)
        idx = text.find(needle) if needle else -1
        if idx < 0:
            return ""
        start, end = max(0, idx - 40), min(len(text), idx + len(needle) + 40)
        return text[start:end]

    records: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for name in edited:
        new_value = _current_value(document, name)
        records.append(
            {
                "field": name,
                "original_value": jsonable(originals.get(name)),
                "corrected_value": jsonable(new_value),
                "source_excerpt": excerpt(originals.get(name)),
                "prompt_version": extraction.get("prompt_version"),
                "model": extraction.get("model"),
                "corrected_at": now,
            }
        )
    return records


def _current_value(document: Document, name: str) -> Any:
    """Read a storage-named field's current value off the document."""
    if name == "kind_id":
        return document.kind_id
    if name == "sender_id":
        return document.sender_id
    if name == "tags":
        return sorted(tag.slug for tag in document.tags)
    return getattr(document, name, None)
```

2. In `update_document`, **capture originals before mutating**. Right after `document = await _get_document_or_404(...)` and computing `provided`, snapshot:

```python
    originals: dict[str, Any] = {}
    for body_field, storage in (("kind_slug", "kind_id"), ("sender", "sender_id")):
        if body_field in provided:
            originals[storage] = _current_value(document, storage)
    if "tags" in provided:
        originals["tags"] = _current_value(document, "tags")
    for body_field in ("title", "summary", "document_date", "due_date", "expiry_date",
                       "amount_total", "currency", "language"):
        if body_field in provided:
            originals[body_field] = _current_value(document, body_field)
```

3. After the `user_edited` block (where `edited` is finalised, ~line 312), append the corrections to `extra` before the commit:

```python
    corrections = list(document.extra.get("corrections", []))
    corrections.extend(_correction_records(document, originals, edited))
    document.extra = {**document.extra, "corrections": corrections}
```

Ensure `from datetime import UTC, datetime` and `from typing import Any` are imported in the module (add if missing).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_documents_api.py -k correction -q`
Expected: PASS. Also run the full file to confirm no regression: `uv run pytest tests/test_documents_api.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/library/api/documents.py tests/test_documents_api.py
git commit -m "feat(api): record metadata corrections in extra[corrections]"
```

---

## Task 6: API — review_status filter, response fields, mark-verified

**Files:**
- Modify: `src/library/search.py` (`DocumentFilters`, `filter_conditions`)
- Modify: `src/library/api/documents.py` (`list_documents` param; `_detail`/`_list_item_fields`; new mark-verified endpoint)
- Modify: `src/library/schemas.py` (`DocumentListItem.review_status`, `DocumentDetail.validation`)
- Test: `tests/test_documents_api.py` (extend)

**Interfaces:**
- Consumes: `ReviewStatus`
- Produces:
  - `DocumentFilters.review_status: ReviewStatus | None`
  - `GET /api/documents?review_status=needs_review`
  - `review_status` on every list item; `validation` (the `extra["validation"]` blob) on detail
  - `POST /api/documents/{id}/verify` → sets `review_status = verified`, returns `DocumentDetail`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_documents_api.py`:

```python
def test_list_filters_by_review_status(api_client, ...):
    # Create one needs_review and one unreviewed document; GET with
    # ?review_status=needs_review returns only the first.

def test_verify_endpoint_marks_verified(api_client, ...):
    # Create a needs_review document; POST /api/documents/{id}/verify;
    # assert response review_status == "verified".

def test_detail_exposes_validation(api_client, ...):
    # A document with extra["validation"] returns it under `validation`.
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_documents_api.py -k "review_status or verify or validation" -q`
Expected: FAIL.

- [ ] **Step 3: Implement filter + schema + endpoints**

1. `src/library/search.py` — in `DocumentFilters` add (import `ReviewStatus` from models):

```python
    review_status: ReviewStatus | None = None
```

In `filter_conditions`, alongside the `status` block:

```python
    if filters.review_status is not None:
        conditions.append(Document.review_status == filters.review_status)
```

2. `src/library/schemas.py` — add to `DocumentListItem` (import `ReviewStatus`):

```python
    review_status: ReviewStatus
```

Add to `DocumentDetail`:

```python
    validation: dict[str, Any] | None = Field(
        default=None, description="Latest validation run: findings + provenance."
    )
```

3. `src/library/api/documents.py`:

- Add the query param to `list_documents` (next to `status_filter`):

```python
    review_status: Annotated[ReviewStatus | None, Query()] = None,
```

and pass `review_status=review_status` into the `DocumentFilters(...)` constructor.

- In `_list_item_fields`, add `"review_status": document.review_status,`.
- In `_detail`, add `validation=document.extra.get("validation"),`.
- Add the endpoint near `queue_extraction`:

```python
@router.post(
    "/documents/{document_id}/verify",
    response_model=DocumentDetail,
    summary="Mark a document's metadata as reviewed/verified",
)
async def verify_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Set review_status=verified and record an audit event."""
    document = await _get_document_or_404(session, document_id)
    document.review_status = ReviewStatus.VERIFIED
    session.add(IngestionEvent(document_id=document.id, event="review_verified", detail={}))
    await session.commit()
    await session.refresh(document)
    return _detail(document)
```

Import `ReviewStatus` in `documents.py`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_documents_api.py -q`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/library/search.py src/library/schemas.py src/library/api/documents.py tests/test_documents_api.py
git commit -m "feat(api): review_status filter, detail validation, verify endpoint"
```

---

## Task 7: LLM-as-judge module

**Files:**
- Modify: `src/library/config.py` (add `extraction_judge_model`, `extraction_judge_inline`)
- Create: `src/library/extraction/judge.py`
- Test: `tests/test_extraction_judge.py`

**Interfaces:**
- Produces:
  - `FieldVerdict` (Pydantic): `field: str`, `verdict: Literal["correct","wrong","unsupported"]`, `note: str | None`
  - `JudgeResult` (Pydantic): `verdicts: list[FieldVerdict]`
  - `async def judge(document: Document, *, client: AsyncAnthropic, settings: Settings) -> JudgeResult`

- [ ] **Step 1: Add config settings**

In `src/library/config.py` (extraction block):

```python
    extraction_judge_model: str = "claude-sonnet-4-6"
    extraction_judge_inline: bool = False  # reserved; judge is batch-only this phase
```

- [ ] **Step 2: Write the failing test (judge with a mocked Anthropic client)**

Create `tests/test_extraction_judge.py`:

```python
"""Unit tests for the extraction judge (Anthropic client mocked)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from library.config import Settings
from library.extraction.judge import FieldVerdict, JudgeResult, judge
from library.models import Document, DocumentSource


def _doc() -> Document:
    # Normal constructor: mapped columns are data descriptors, so __new__ +
    # object.__setattr__ would not read back. tags is a relationship; pass [].
    return Document(
        ocr_text="Factuur Eneco totaal € 12,00",
        title="Eneco factuur",
        summary="s",
        amount_total=None,
        currency=None,
        document_date=None,
        due_date=None,
        expiry_date=None,
        language=None,
        kind_id=None,
        sender_id=None,
        tags=[],
        extra={},
    )


@pytest.mark.asyncio
async def test_judge_returns_parsed_verdicts() -> None:
    result = JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None)])
    response = SimpleNamespace(parsed_output=result)
    client = SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(return_value=response)))
    settings = Settings(anthropic_api_key="k")

    verdicts = await judge(_doc(), client=client, settings=settings)

    assert verdicts.verdicts[0].field == "title"
    assert verdicts.verdicts[0].verdict == "correct"
    client.messages.parse.assert_awaited_once()
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_extraction_judge.py -q`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement the judge**

Create `src/library/extraction/judge.py`:

```python
"""Batch-only LLM grader for extracted metadata.

Sends the document's OCR text plus its current extracted fields to a stronger
model and asks, per field, whether the extracted value is supported by the
source. Used by the eval harness; never called from the live pipeline.
"""

import json
import logging
from typing import Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from library.config import Settings
from library.extraction.extractor import MAX_TEXT_CHARS
from library.models import Document

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 2_048

SYSTEM_PROMPT: str = """\
You grade metadata extracted from a household document for "Library".
You are given the document's text and a JSON object of extracted fields.
For each field present in the extraction, judge whether its value is supported
by the document text:
- "correct": the value is clearly supported by the text.
- "wrong": the text supports a different value.
- "unsupported": the text does not contain enough to confirm the value.
Return one verdict per provided field. Be strict: only "correct" when the text
clearly backs the value. Keep notes to one short line.
"""


class FieldVerdict(BaseModel):
    """The judge's verdict on one extracted field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    verdict: Literal["correct", "wrong", "unsupported"]
    note: str | None


class JudgeResult(BaseModel):
    """All per-field verdicts for one document."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[FieldVerdict]


def _extracted_fields(document: Document) -> dict[str, Any]:
    """The current extracted values, keyed by storage field name, non-null only."""
    fields: dict[str, Any] = {
        "title": document.title,
        "summary": document.summary,
        "document_date": document.document_date,
        "due_date": document.due_date,
        "expiry_date": document.expiry_date,
        "amount_total": document.amount_total,
        "currency": document.currency,
        "language": getattr(document.language, "value", document.language),
        "sender_id": document.sender_id,
        "kind_id": document.kind_id,
        "tags": sorted(tag.slug for tag in document.tags),
    }
    return {k: (str(v) if v is not None else None) for k, v in fields.items() if v not in (None, [])}


async def judge(document: Document, *, client: AsyncAnthropic, settings: Settings) -> JudgeResult:
    """Grade one document's extraction against its source text."""
    text = (document.ocr_text or "")[:MAX_TEXT_CHARS]
    extracted = _extracted_fields(document)
    content = [
        {"type": "text", "text": f"DOCUMENT TEXT:\n{text}"},
        {"type": "text", "text": f"EXTRACTED FIELDS (JSON):\n{json.dumps(extracted, ensure_ascii=False)}"},
    ]
    response = await client.messages.parse(
        model=settings.extraction_judge_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=JudgeResult,
    )
    parsed = response.parsed_output
    if parsed is None:
        logger.warning("judge returned no parseable output for document %s", document.id)
        return JudgeResult(verdicts=[])
    return parsed
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_extraction_judge.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/library/config.py src/library/extraction/judge.py tests/test_extraction_judge.py
git commit -m "feat(extraction): batch LLM-as-judge for extracted metadata"
```

---

## Task 8: Pure eval scoring functions

**Files:**
- Create: `src/library/extraction/eval.py`
- Test: `tests/test_extraction_eval.py`

**Interfaces:**
- Consumes: `JudgeResult` (Task 7)
- Produces:
  - `flywheel_accuracy(documents: Iterable[Document]) -> dict[str, tuple[int, int]]` — field → (correct, total) over reviewed docs (those with `extra["corrections"]`)
  - `judge_agreement(results: Iterable[JudgeResult]) -> dict[str, tuple[int, int]]` — field → (agree, total)
  - `combine(flywheel, agreement) -> dict[str, dict[str, Any]]` — field → `{flywheel_accuracy, judge_agreement, n}`
  - `version_distribution(documents) -> dict[str, int]` — `"<prompt_version>|<model>"` → count, and `modal_version(dist) -> tuple[str, str]`

- [ ] **Step 1: Write failing tests**

Create `tests/test_extraction_eval.py`:

```python
"""Unit tests for the pure eval scoring functions."""

from library.extraction.eval import (
    combine,
    flywheel_accuracy,
    judge_agreement,
    modal_version,
    version_distribution,
)
from library.extraction.judge import FieldVerdict, JudgeResult
from library.models import Document


def _doc(extra: dict) -> Document:
    # Normal constructor (see Task 1 note on data descriptors).
    return Document(extra=extra)


def test_flywheel_accuracy_counts_corrected_as_wrong() -> None:
    docs = [
        _doc({"extraction": {"fields_set": ["amount_total", "title"]},
              "corrections": [{"field": "amount_total"}]}),
        _doc({"extraction": {"fields_set": ["amount_total", "title"]},
              "corrections": [{"field": "title"}]}),
    ]
    acc = flywheel_accuracy(docs)
    assert acc["amount_total"] == (1, 2)  # one correct, two total
    assert acc["title"] == (1, 2)


def test_flywheel_ignores_docs_without_corrections() -> None:
    docs = [_doc({"extraction": {"fields_set": ["title"]}})]  # never reviewed
    assert flywheel_accuracy(docs) == {}


def test_judge_agreement() -> None:
    results = [
        JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None),
                              FieldVerdict(field="amount_total", verdict="wrong", note=None)]),
        JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None)]),
    ]
    agree = judge_agreement(results)
    assert agree["title"] == (2, 2)
    assert agree["amount_total"] == (0, 1)


def test_combine_merges_fields() -> None:
    combined = combine({"title": (1, 2)}, {"title": (2, 2), "amount_total": (0, 1)})
    assert combined["title"]["flywheel_accuracy"] == 0.5
    assert combined["title"]["judge_agreement"] == 1.0
    assert combined["amount_total"]["flywheel_accuracy"] is None


def test_version_distribution_and_modal() -> None:
    docs = [
        _doc({"extraction": {"prompt_version": "v1", "model": "haiku"}}),
        _doc({"extraction": {"prompt_version": "v1", "model": "haiku"}}),
        _doc({"extraction": {"prompt_version": "v2", "model": "sonnet"}}),
    ]
    dist = version_distribution(docs)
    assert dist["v1|haiku"] == 2
    assert modal_version(dist) == ("v1", "haiku")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_extraction_eval.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the eval module**

Create `src/library/extraction/eval.py`:

```python
"""Pure scoring for the extraction-quality harness (stdlib + models only).

Two ground-truth sources combine here:
- the corrections flywheel (real labels on user-reviewed documents), and
- the LLM judge (coverage on sampled documents).
No DB or network access — callers load documents / run the judge and pass the
results in.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from library.extraction.judge import JudgeResult
from library.models import Document


def flywheel_accuracy(documents: Iterable[Document]) -> dict[str, tuple[int, int]]:
    """Field -> (correct, total) over user-reviewed documents.

    A document is "reviewed" if it has any ``extra["corrections"]``. For such a
    document, every field in ``extra["extraction"]["fields_set"]`` counts toward
    the total; a field with a correction record counts as wrong, otherwise
    correct.
    """
    correct: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for document in documents:
        extra = document.extra or {}
        corrections = extra.get("corrections")
        if not corrections:
            continue
        corrected = {c["field"] for c in corrections}
        fields_set = (extra.get("extraction") or {}).get("fields_set", [])
        for field in fields_set:
            total[field] += 1
            if field not in corrected:
                correct[field] += 1
    return {field: (correct[field], total[field]) for field in total}


def judge_agreement(results: Iterable[JudgeResult]) -> dict[str, tuple[int, int]]:
    """Field -> (agree, total) where agree means the judge said 'correct'."""
    agree: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for result in results:
        for verdict in result.verdicts:
            total[verdict.field] += 1
            if verdict.verdict == "correct":
                agree[verdict.field] += 1
    return {field: (agree[field], total[field]) for field in total}


def _ratio(pair: tuple[int, int] | None) -> float | None:
    if pair is None or pair[1] == 0:
        return None
    return pair[0] / pair[1]


def combine(
    flywheel: dict[str, tuple[int, int]],
    agreement: dict[str, tuple[int, int]],
) -> dict[str, dict[str, Any]]:
    """Merge both sources into a per-field summary dict."""
    fields = set(flywheel) | set(agreement)
    out: dict[str, dict[str, Any]] = {}
    for field in sorted(fields):
        fw = flywheel.get(field)
        ag = agreement.get(field)
        out[field] = {
            "flywheel_accuracy": _ratio(fw),
            "flywheel_n": fw[1] if fw else 0,
            "judge_agreement": _ratio(ag),
            "judge_n": ag[1] if ag else 0,
            "n": (fw[1] if fw else 0) + (ag[1] if ag else 0),
        }
    return out


def version_distribution(documents: Iterable[Document]) -> dict[str, int]:
    """'<prompt_version>|<model>' -> count across the documents' extraction blobs."""
    counts: Counter[str] = Counter()
    for document in documents:
        extraction = (document.extra or {}).get("extraction") or {}
        version = extraction.get("prompt_version") or "unknown"
        model = extraction.get("model") or "unknown"
        counts[f"{version}|{model}"] += 1
    return dict(counts)


def modal_version(distribution: dict[str, int]) -> tuple[str, str]:
    """The most common (prompt_version, model) pair; ('unknown','unknown') if empty."""
    if not distribution:
        return ("unknown", "unknown")
    top = max(distribution.items(), key=lambda kv: kv[1])[0]
    version, _, model = top.partition("|")
    return (version, model)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_extraction_eval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/extraction/eval.py tests/test_extraction_eval.py
git commit -m "feat(extraction): pure eval scoring (flywheel + judge agreement)"
```

---

## Task 9: `library eval-extractions` CLI command

**Files:**
- Modify: `src/library/cli.py` (add command + imports)
- Test: `tests/test_cli.py` (extend, judge mocked)

**Interfaces:**
- Consumes: `flywheel_accuracy`, `judge_agreement`, `combine`, `version_distribution`, `modal_version` (Task 8); `judge` (Task 7); `EvalRun` (Task 2)
- Produces: `library eval-extractions [--sample N | --all]` → prints a per-field table and inserts one `EvalRun` row

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (judge mocked via `monkeypatch.setattr` on the judge symbol imported into `cli`):

```python
def test_eval_extractions_persists_run(api_database_url, monkeypatch, ...):
    # Arrange: insert ≥1 reviewed document (extra with extraction.fields_set +
    # corrections) and ≥1 plain document with an extraction blob.
    # Mock library.cli.judge to return a JudgeResult with a 'correct' verdict.
    # Act: invoke `eval-extractions --all` (and assert AsyncAnthropic is not
    # actually called — the mock stands in).
    # Assert: exactly one eval_runs row exists; its per_field includes a field;
    # sample_size matches the judged count.
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -k eval_extractions -q`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement the command**

In `src/library/cli.py`, add imports:

```python
from anthropic import AsyncAnthropic
from library.extraction.eval import (
    combine, flywheel_accuracy, judge_agreement, modal_version, version_distribution,
)
from library.extraction.judge import JudgeResult, judge
from library.models import EvalRun
```

Add the command:

```python
@app.command("eval-extractions")
def eval_extractions(
    sample: int | None = typer.Option(None, "--sample", min=1, help="Judge a random sample of N documents."),
    judge_all: bool = typer.Option(False, "--all", help="Judge every eligible document (ignores --sample)."),
) -> None:
    """Score extraction quality (flywheel + LLM judge) and record an eval run.

    Flywheel accuracy is computed over every document carrying corrections.
    The judge runs over the sampled set (or all eligible documents with OCR
    text) for coverage. One eval_runs row is written, pinned to the modal
    prompt_version + model so runs are comparable over time.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        typer.echo("error: LIBRARY_ANTHROPIC_API_KEY is required to run the judge")
        raise typer.Exit(code=1)

    async def operation(session: AsyncSession) -> EvalRun:
        all_docs = list(
            (
                await session.execute(
                    select(Document).where(Document.deleted_at.is_(None)).order_by(Document.id)
                )
            )
            .scalars()
            .all()
        )
        flywheel = flywheel_accuracy(all_docs)

        eligible = [d for d in all_docs if (d.ocr_text or "").strip()]
        if not judge_all and sample is not None:
            eligible = eligible[:sample]  # deterministic head; avoids RNG (unavailable in some runtimes)

        results: list[JudgeResult] = []
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            for document in eligible:
                results.append(await judge(document, client=client, settings=settings))

        agreement = judge_agreement(results)
        per_field = combine(flywheel, agreement)
        distribution = version_distribution(eligible or all_docs)
        version, model = modal_version(distribution)
        overall = {
            "documents_total": len(all_docs),
            "reviewed_total": sum(1 for d in all_docs if (d.extra or {}).get("corrections")),
            "judged_total": len(results),
        }
        run = EvalRun(
            prompt_version=version, model=model, version_mix=distribution,
            sample_size=len(results), per_field=per_field, overall=overall,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    run = _run(operation)
    typer.echo(f"eval run #{run.id}  prompt={run.prompt_version} model={run.model} judged={run.sample_size}")
    typer.echo(f"{'field':<18}{'flywheel':>12}{'judge':>12}{'n':>6}")
    for field, scores in run.per_field.items():
        fw = "-" if scores["flywheel_accuracy"] is None else f"{scores['flywheel_accuracy']:.0%}"
        jg = "-" if scores["judge_agreement"] is None else f"{scores['judge_agreement']:.0%}"
        typer.echo(f"{field:<18}{fw:>12}{jg:>12}{scores['n']:>6}")
```

> Note on sampling: the head-slice is deterministic and avoids `random` (kept simple; the spec's "random sample" is satisfiable later with a seeded shuffle if desired — log that this is a head sample if you change it).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -k eval_extractions -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/cli.py tests/test_cli.py
git commit -m "feat(cli): eval-extractions harness (flywheel + judge, eval_runs)"
```

---

## Task 10: Frontend API — review_status types, filter, verify, validation

**Files:**
- Modify: `frontend/src/api/documents.ts`
- Modify: `frontend/src/utils/documentQuery.ts`
- Test: `frontend/src/api/__tests__/documents.spec.ts`

**Interfaces:**
- Produces:
  - `ReviewStatus` type; `review_status` on `DocumentListItem`; `validation` on `DocumentDetail`
  - `DocumentFilters.review_status?: ReviewStatus`
  - `verifyDocument(id: number): Promise<DocumentDetail>`
  - `documentQuery.ts` round-trips `review` ⇄ `review_status`

- [ ] **Step 1: Write failing tests**

In `frontend/src/api/__tests__/documents.spec.ts` (mirror existing fetch-mock tests):

```ts
it('sends review_status as a query param', async () => {
  // arrange a fetch mock; call listDocuments({ review_status: 'needs_review' })
  // assert the request URL contains review_status=needs_review
})

it('posts to the verify endpoint', async () => {
  // call verifyDocument(7); assert POST /api/documents/7/verify
})
```

- [ ] **Step 2: Run to verify they fail**

Run (from `frontend/`): `npm run test:unit -- documents.spec`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `frontend/src/api/documents.ts`:

```ts
export type ReviewStatus = 'verified' | 'needs_review' | 'unreviewed'
```

Add `review_status: ReviewStatus` to `DocumentListItem`; add `validation: Record<string, unknown> | null` to `DocumentDetail`; add `review_status?: ReviewStatus` to `DocumentFilters`. In the function that builds the list query string (where `kind`, `status`, etc. are appended), append `review_status` when set. Add:

```ts
export function verifyDocument(id: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}/verify`, { method: 'POST' })
}
```

In `frontend/src/utils/documentQuery.ts`, add `review_status` to the parsed/built query shape (URL key `review`), mirroring how `status`/`kind` are handled, and include it in `hasActiveFilters`.

- [ ] **Step 4: Run to verify they pass**

Run (from `frontend/`): `npm run test:unit -- documents.spec`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/documents.ts frontend/src/utils/documentQuery.ts frontend/src/api/__tests__/documents.spec.ts
git commit -m "feat(frontend): review_status filter/type + verifyDocument api"
```

---

## Task 11: Frontend — "Needs review" preset + filter in the list view

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue`
- Test: `frontend/src/views/__tests__/DocumentListView.spec.ts`

**Interfaces:**
- Consumes: `DocumentFilters.review_status`, `documentQuery.ts`
- Produces: a "Needs review (N)" entry point that pushes `?review=needs_review`, and the list passes `review_status` to `listDocuments`

- [ ] **Step 1: Write the failing test**

In `DocumentListView.spec.ts` (mirror existing list tests that assert `listDocuments` was called with given filters):

```ts
it('requests needs_review documents when the review query is set', async () => {
  // mount with route query { review: 'needs_review' }
  // assert listDocuments was called with filters.review_status === 'needs_review'
})
```

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npm run test:unit -- DocumentListView.spec`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `DocumentListView.vue`, in the block that builds `filters` for `listDocuments` (~line 94), add:

```ts
    review_status: (state.review || undefined) as DocumentListItem['review_status'] | undefined,
```

(where `state` is `applied.value` from `parseDocumentQuery`). Add a visible "Needs review" filter control / chip next to the existing filters that calls `applyFilterQuery({ ...currentQuery, review: 'needs_review' })`, and a small badge per row when `item.review_status === 'needs_review'` (reuse `AppBadge colour="yellow"`), near the existing status badges (~line 309).

- [ ] **Step 4: Run to verify it passes**

Run (from `frontend/`): `npm run test:unit -- DocumentListView.spec`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DocumentListView.vue frontend/src/views/__tests__/DocumentListView.spec.ts
git commit -m "feat(frontend): needs-review filter preset + row badge"
```

---

## Task 12: Frontend — per-field validation badges + mark verified in detail view

**Files:**
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/views/__tests__/DocumentDetailView.spec.ts`

**Interfaces:**
- Consumes: `DocumentDetail.validation`, `DocumentDetail.review_status`, `verifyDocument`
- Produces: a warning badge beside each field named in `validation.findings`; a "Mark verified" button that calls `verifyDocument` and reflects the new status

- [ ] **Step 1: Write the failing test**

In `DocumentDetailView.spec.ts` (mirror existing detail tests that stub the documents API):

```ts
it('shows a warning badge for a flagged field', async () => {
  // stub getDocument to return validation.findings = [{ rule:'amount_grounding', field:'amount_total', ... }]
  // assert a warning indicator renders near the amount field
})

it('marks the document verified', async () => {
  // stub verifyDocument; click the "Mark verified" button; assert it was called
  // and the displayed status updates to verified
})
```

- [ ] **Step 2: Run to verify they fail**

Run (from `frontend/`): `npm run test:unit -- DocumentDetailView.spec`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `DocumentDetailView.vue`:
- Build a computed `findingsByField` from `detail.validation?.findings` mapping storage field name → finding(s). Map storage names back to the UI fields (`amount_total`, `currency`, `document_date`, `due_date`, `expiry_date`, `title`, `summary`, plus `kind_id`→kind, `sender_id`→sender).
- Render a small warning marker (e.g. `AppBadge colour="yellow"` with the finding `message` as title/tooltip) next to each affected field's label.
- Add a "Mark verified" button shown when `detail.review_status !== 'verified'`; on click call `verifyDocument(detail.id)`, then update local `detail` from the response (and surface errors via the existing flash/error pattern in that view).

- [ ] **Step 4: Run to verify they pass**

Run (from `frontend/`): `npm run test:unit -- DocumentDetailView.spec`
Expected: PASS. Then run the whole frontend unit suite: `npm run test:unit`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DocumentDetailView.vue frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(frontend): per-field validation badges + mark verified"
```

---

## Task 13: Playwright e2e — needs-review triage flow

**Files:**
- Create/Modify: a Playwright spec under the project's e2e location (find it: `find frontend -path '*e2e*' -name '*.spec.ts'` or check `frontend/playwright.config.ts`)
- Test: the new spec itself

**Interfaces:**
- Consumes: the running app + seeded data path used by existing e2e specs

- [ ] **Step 1: Write the e2e test**

Following the existing e2e setup (auth + seeded documents), add a spec that:
1. logs in,
2. navigates to the list with the "Needs review" preset,
3. opens a flagged document,
4. asserts a field warning badge is visible,
5. corrects the flagged field (or clicks "Mark verified"),
6. asserts the document leaves the needs-review queue.

```ts
import { test, expect } from '@playwright/test'

test('triage a needs-review document', async ({ page }) => {
  // mirror the auth + seed helpers used by the existing e2e specs
  // ...navigate to ?review=needs_review, open first doc, expect a warning badge,
  // click "Mark verified", return to the queue, expect the doc absent
})
```

- [ ] **Step 2: Run the e2e suite**

Run (from `frontend/`): `npm run test:e2e -- --grep "needs-review"` (match the project's e2e script name)
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend
git commit -m "test(e2e): needs-review triage flow"
```

---

## Task 14: Documentation + journal

**Files:**
- Modify: `docs/architecture.md` (note review_status + validation in the extract stage; mention the eval harness)
- Modify or Create: an extraction doc (e.g. `docs/ingestion.md` extraction section, or `docs/extraction-quality.md`) describing the rules, review queue, corrections flywheel, and `eval-extractions`/`backfill-validation` CLIs
- Modify: `docs/api.md` (review_status filter, `validation` on detail, `POST /documents/{id}/verify`)
- Create: `journal/260621-extraction-quality.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Update architecture + ingestion/extraction docs**

Add a short subsection: the extract stage now runs deterministic validation, sets `review_status`, and writes `extra["validation"]`. Document each rule (table from the spec), the `extra["corrections"]` shape, and the two CLI commands with example invocations:

```
library backfill-validation [--limit N]
library eval-extractions [--sample N | --all]
```

- [ ] **Step 2: Update API docs**

Document the `review_status` query param on `GET /documents`, the `review_status` field on list items, the `validation` field on detail, and the new `POST /documents/{id}/verify` endpoint.

- [ ] **Step 3: Write the journal entry**

Create `journal/260621-extraction-quality.md` capturing: the decisions (per-doc + aggregate equal priority; flywheel + judge ground truth; rules shipped minus date-grounding; review_status enum; batch-only judge; measurement-loop-as-improvement), the new schema (migration 0006), and the CLIs. Note follow-ups: date-grounding rule, dedicated triage screen, active improvement (few-shot mining), random (seeded) sampling for the judge.

- [ ] **Step 4: Verify the full backend + frontend suites pass**

Run: `uv run pytest -q` and (from `frontend/`) `npm run test:unit`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs journal
git commit -m "docs(extraction): document validation, review queue, eval harness"
```

---

## Self-Review Notes

- **Spec coverage:** validation rules (T1), review_status storage (T2, T3), pipeline integration (T3), backfill (T4), corrections flywheel mining-ready shape (T5), API filter+detail+verify (T6), LLM judge (T7), eval scoring (T8), eval CLI + eval_runs pinned to version (T9), list preset+badge (T11), detail badges+verify (T12), e2e (T13), docs+journal (T14). Config settings folded into T3/T7. All spec sections map to a task.
- **Non-goals respected:** no date-grounding rule, no numeric score, no dedicated triage screen, no always-on judging (config flag reserved only), no active improvement, no Ask-side changes.
- **Type consistency:** storage field names (`kind_id`, `sender_id`, `tags`, scalars) are used identically across validation, corrections, judge `_extracted_fields`, and eval `fields_set`. `ReviewStatus` values (`verified`/`needs_review`/`unreviewed`) match between enum, migration, schema, and frontend type. `judge`/`JudgeResult`/`FieldVerdict` signatures match between T7 producer and T8/T9 consumers.
- **Known simplification logged:** `eval-extractions` uses a deterministic head sample, not a random one (noted in T9 and the journal follow-ups).
