"""Integration tests for the fill-only repair pass (real test Postgres).

Mirrors ``tests/test_extraction_apply.py``: the LLM call
(``repair.repair_extract``) is monkeypatched, everything else — trigger
gates, fill-only apply semantics, events, budget, revalidation — runs for
real against the shared test database.
"""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library import jobs
from library.config import Settings, get_settings
from library.extraction import repair as repair_module
from library.extraction.apply import todays_spend_usd
from library.extraction.repair import (
    REPAIR_PROMPT_VERSION,
    RepairMetadata,
    RepairOutcome,
    maybe_repair_extraction,
)
from library.models import (
    Document,
    DocumentSource,
    IngestionEvent,
    Kind,
    ReviewStatus,
    Sender,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def settings() -> Settings:
    """Direct-call settings: key present, budget far above any test spend."""
    return Settings(anthropic_api_key="test-key", extraction_daily_budget_usd=1_000.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A markdown rendering of the doc-150 shape: the vision layer plainly shows
# the merchant name and the printed date that extraction missed.
RECEIPT_MARKDOWN = (
    "# Bistro De Kade\n\nKanaalstraat 12, Haarlem\n\nDatum: 30-06-2026\n\n"
    "| Item | Prijs |\n|---|---|\n| Lunch | 12,50 |\n\n**Totaal € 12,50**"
)


def make_repair_metadata(**overrides: Any) -> RepairMetadata:
    base: dict[str, Any] = {
        "sender_name": "Bistro De Kade",
        "document_date": "2026-06-30",
        "amount_total": None,
        "currency": None,
        "confidence": "high",
    }
    base.update(overrides)
    return RepairMetadata.model_validate(base)


def make_repair_outcome(metadata: RepairMetadata, cost_usd: float = 0.00075) -> RepairOutcome:
    return RepairOutcome(
        metadata=metadata,
        model="claude-haiku-4-5",
        prompt_version=REPAIR_PROMPT_VERSION,
        input_tokens=500,
        output_tokens=50,
        cost_usd=cost_usd,
    )


def patch_repair(
    monkeypatch: pytest.MonkeyPatch, outcome: RepairOutcome | Exception
) -> list[Document]:
    """Replace the LLM call; return the (mutable) list of documents it saw."""
    calls: list[Document] = []

    async def fake_repair_extract(document: Document, **kwargs: Any) -> RepairOutcome:
        calls.append(document)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(repair_module, "repair_extract", fake_repair_extract)
    return calls


async def get_kind_id(session_factory: async_sessionmaker[AsyncSession], slug: str) -> int:
    async with session_factory() as session:
        return (await session.execute(select(Kind.id).where(Kind.slug == slug))).scalar_one()


async def upsert_test_sender(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    """Idempotently seed a sender by exact name (the test DB is shared)."""
    async with session_factory() as session:
        existing = (
            await session.execute(select(Sender).where(func.lower(Sender.name) == name.lower()))
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        sender = Sender(name=name)
        session.add(sender)
        await session.commit()
        return sender.id


async def make_doc150_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    **overrides: Any,
) -> int:
    """A doc-150-shaped document: receipt, amount set, no date, thin OCR text.

    The default has no sender; pass ``sender_id`` to attach one. ``extra``
    carries a plausible ``extraction`` block (the repair trigger requires
    extraction to have run).
    """
    kwargs: dict[str, Any] = {
        "kind_id": await get_kind_id(session_factory, "receipt"),
        "amount_total": Decimal("12.50"),
        "currency": "EUR",
        "document_date": None,
        "ocr_text": "kade lunch totaal 12,50",
        "ocr_confidence": 90.0,
        "pages_markdown": RECEIPT_MARKDOWN,
        "review_status": ReviewStatus.NEEDS_REVIEW,
        "extra": {
            "extraction": {
                "prompt_version": "2026-07-14.1",
                "model": "claude-haiku-4-5",
                "confidence": "high",
                "input_mode": "text",
            }
        },
    }
    kwargs.update(overrides)
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.pdf",
            **kwargs,
        )
        session.add(document)
        await session.commit()
        return document.id


async def run_repair(
    session_factory: async_sessionmaker[AsyncSession], document_id: int, settings: Settings
) -> None:
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await maybe_repair_extraction(session, document, settings)


async def get_repair_events(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> list[tuple[str, dict[str, Any]]]:
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(IngestionEvent)
                    .where(
                        IngestionEvent.document_id == document_id,
                        IngestionEvent.event.in_(
                            ["extraction_repair_completed", "extraction_repair_skipped"]
                        ),
                    )
                    .order_by(IngestionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        return [(event.event, event.detail) for event in events]


# ---------------------------------------------------------------------------
# The doc-150 shape, end-to-end at the markdown-stage seam
# ---------------------------------------------------------------------------


async def test_doc150_shape_repaired_at_markdown_stage_seam(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Date null + generic sender + real markdown → date filled, sender replaced,
    completed event, review_status back to unreviewed — via jobs.run_markdown."""
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LIBRARY_EXTRACTION_DAILY_BUDGET_USD", "100000")
    monkeypatch.setenv("LIBRARY_MARKDOWN_ENABLED", "false")  # markdown layer pre-seeded
    get_settings.cache_clear()

    generic_id = await upsert_test_sender(session_factory, "Restaurant")
    calls = patch_repair(monkeypatch, make_repair_outcome(make_repair_metadata()))
    document_id = await make_doc150_document(
        session_factory, f"repair-doc150-{uuid.uuid4().hex[:8]}", sender_id=generic_id
    )

    try:
        async with session_factory() as session:
            document = await session.get(Document, document_id)
            assert document is not None
            await jobs.run_markdown(session, document)
    finally:
        get_settings.cache_clear()

    assert len(calls) == 1
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.document_date == date(2026, 6, 30)
        assert document.sender is not None and document.sender.name == "Bistro De Kade"
        assert document.amount_total == Decimal("12.50")  # untouched
        assert document.review_status is ReviewStatus.UNREVIEWED
        repair = document.extra["extraction_repair"]
        assert repair["prompt_version"] == REPAIR_PROMPT_VERSION
        assert repair["input"] == "markdown"
        assert set(repair["fields_filled"]) == {"document_date", "sender_id"}
        # The old generic sender row is not deleted.
        old = await session.get(Sender, generic_id)
        assert old is not None and old.name == "Restaurant"

    events = await get_repair_events(session_factory, document_id)
    completed = [detail for event, detail in events if event == "extraction_repair_completed"]
    assert len(completed) == 1
    assert completed[0]["model"] == "claude-haiku-4-5"
    assert completed[0]["cost_usd"] == pytest.approx(0.00075)
    assert completed[0]["input_tokens"] == 500
    assert sorted(completed[0]["gaps"]) == ["generic_sender", "missing_date"]


async def test_repair_fills_scalars_only_when_null(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair result never overwrites an already-set amount/currency."""
    patch_repair(
        monkeypatch,
        make_repair_outcome(
            make_repair_metadata(amount_total="999.99", currency="USD", sender_name=None)
        ),
    )
    document_id = await make_doc150_document(
        session_factory, f"repair-scalars-{uuid.uuid4().hex[:8]}"
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.amount_total == Decimal("12.50")  # NOT 999.99
        assert document.currency == "EUR"  # NOT USD
        assert document.document_date == date(2026, 6, 30)  # was NULL → filled
        assert document.extra["extraction_repair"]["fields_filled"] == ["document_date"]


# ---------------------------------------------------------------------------
# Safety property: user edits and non-generic senders are never touched
# ---------------------------------------------------------------------------


async def test_user_edited_date_is_not_overwritten(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_repair(monkeypatch, make_repair_outcome(make_repair_metadata(sender_name=None)))
    document_id = await make_doc150_document(
        session_factory,
        f"repair-user-date-{uuid.uuid4().hex[:8]}",
        extra={
            "extraction": {"prompt_version": "x", "model": "m", "confidence": "high"},
            "user_edited_fields": ["document_date"],
        },
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.document_date is None  # user cleared it; repair must not refill
        assert "document_date" not in document.extra["extraction_repair"]["fields_filled"]
        # The user_edited_fields marker survives the extra merge.
        assert document.extra["user_edited_fields"] == ["document_date"]


async def test_non_generic_sender_is_not_replaced(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real merchant name that merely contains a category word stays."""
    real_id = await upsert_test_sender(session_factory, "Garage Spaarndam")
    patch_repair(
        monkeypatch, make_repair_outcome(make_repair_metadata(sender_name="Shell Spaarndam"))
    )
    # missing_date is the qualifying gap; the sender is fine.
    document_id = await make_doc150_document(
        session_factory, f"repair-nongeneric-{uuid.uuid4().hex[:8]}", sender_id=real_id
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.sender_id == real_id  # untouched
        assert document.document_date == date(2026, 6, 30)  # the gap was still filled
        assert "sender_id" not in document.extra["extraction_repair"]["fields_filled"]


async def test_user_edited_sender_is_not_replaced_even_when_generic(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_id = await upsert_test_sender(session_factory, "Restaurant")
    patch_repair(monkeypatch, make_repair_outcome(make_repair_metadata()))
    document_id = await make_doc150_document(
        session_factory,
        f"repair-user-sender-{uuid.uuid4().hex[:8]}",
        sender_id=generic_id,
        extra={
            "extraction": {"prompt_version": "x", "model": "m", "confidence": "high"},
            "user_edited_fields": ["sender_id"],
        },
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.sender_id == generic_id
        assert "sender_id" not in document.extra["extraction_repair"]["fields_filled"]


async def test_generic_repair_result_is_never_applied(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model echoing a category word back must not seed/replace a sender."""
    patch_repair(
        monkeypatch,
        make_repair_outcome(make_repair_metadata(sender_name="Shop", document_date=None)),
    )
    # No sender at all → missing_sender is the qualifying gap.
    document_id = await make_doc150_document(
        session_factory, f"repair-generic-out-{uuid.uuid4().hex[:8]}"
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.sender_id is None
        assert document.extra["extraction_repair"]["fields_filled"] == []
        # Still needs review: the gaps remain.
        assert document.review_status is ReviewStatus.NEEDS_REVIEW


async def test_repair_fills_unset_sender_with_real_name(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merchant = f"Bakkerij Vreugd {uuid.uuid4().hex[:6]}"
    patch_repair(monkeypatch, make_repair_outcome(make_repair_metadata(sender_name=merchant)))
    document_id = await make_doc150_document(
        session_factory, f"repair-fill-sender-{uuid.uuid4().hex[:8]}"
    )

    await run_repair(session_factory, document_id, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.sender is not None and document.sender.name == merchant
        assert set(document.extra["extraction_repair"]["fields_filled"]) == {
            "document_date",
            "sender_id",
        }


# ---------------------------------------------------------------------------
# Skip gates (no LLM call on any of them)
# ---------------------------------------------------------------------------


async def test_no_qualifying_findings_skips_with_no_gaps(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_repair(monkeypatch, RuntimeError("must not be called"))
    real_id = await upsert_test_sender(session_factory, f"Albert Heijn {uuid.uuid4().hex[:6]}")
    document_id = await make_doc150_document(
        session_factory,
        f"repair-no-gaps-{uuid.uuid4().hex[:8]}",
        sender_id=real_id,
        document_date=date(2026, 6, 30),
    )

    await run_repair(session_factory, document_id, settings)

    assert calls == []
    events = await get_repair_events(session_factory, document_id)
    assert events == [("extraction_repair_skipped", {"reason": "no_gaps"})]


async def test_empty_pages_markdown_skips_with_no_markdown(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_repair(monkeypatch, RuntimeError("must not be called"))
    document_id = await make_doc150_document(
        session_factory, f"repair-no-md-{uuid.uuid4().hex[:8]}", pages_markdown=None
    )

    await run_repair(session_factory, document_id, settings)

    assert calls == []
    events = await get_repair_events(session_factory, document_id)
    assert events == [("extraction_repair_skipped", {"reason": "no_markdown"})]


async def test_document_without_extraction_skips_with_no_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_repair(monkeypatch, RuntimeError("must not be called"))
    document_id = await make_doc150_document(
        session_factory, f"repair-no-extraction-{uuid.uuid4().hex[:8]}", extra={}
    )

    await run_repair(session_factory, document_id, settings)

    assert calls == []
    events = await get_repair_events(session_factory, document_id)
    assert events == [("extraction_repair_skipped", {"reason": "no_extraction"})]


async def test_repair_disabled_with_extraction_skips(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_repair(monkeypatch, RuntimeError("must not be called"))
    document_id = await make_doc150_document(
        session_factory, f"repair-disabled-{uuid.uuid4().hex[:8]}"
    )
    settings = Settings(anthropic_api_key="test-key", extraction_enabled=False)

    await run_repair(session_factory, document_id, settings)

    assert calls == []
    events = await get_repair_events(session_factory, document_id)
    assert events == [("extraction_repair_skipped", {"reason": "disabled"})]


async def test_budget_exhausted_skips_and_counts_repair_spend(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair cost counts toward the extraction budget: a prior repair event's
    cost pushes today's spend over the gate and the next repair skips."""
    calls = patch_repair(monkeypatch, RuntimeError("must not be called"))
    document_id = await make_doc150_document(
        session_factory, f"repair-budget-{uuid.uuid4().hex[:8]}"
    )
    async with session_factory() as session:
        spent_before = await todays_spend_usd(session)
        session.add(
            IngestionEvent(
                document_id=document_id,
                event="extraction_repair_completed",
                detail={"cost_usd": 5.0},
            )
        )
        await session.commit()
        # The gate's own accounting includes the repair completion event.
        assert await todays_spend_usd(session) == pytest.approx(spent_before + 5.0)

    # A budget the extraction events alone would NOT exhaust, but the repair
    # event pushes over.
    settings = Settings(
        anthropic_api_key="test-key", extraction_daily_budget_usd=spent_before + 4.0
    )
    await run_repair(session_factory, document_id, settings)

    assert calls == []
    events = await get_repair_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "extraction_repair_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "budget"
    assert skipped[0]["spent_usd"] >= spent_before + 5.0
    assert skipped[0]["budget_usd"] == pytest.approx(spent_before + 4.0)


async def test_second_run_same_prompt_version_skips_already_repaired(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A repair that fills nothing (all nulls) still stamps the prompt version.
    outcome = make_repair_outcome(
        make_repair_metadata(sender_name=None, document_date=None, confidence="low")
    )
    calls = patch_repair(monkeypatch, outcome)
    document_id = await make_doc150_document(
        session_factory, f"repair-idempotent-{uuid.uuid4().hex[:8]}"
    )

    await run_repair(session_factory, document_id, settings)
    assert len(calls) == 1

    await run_repair(session_factory, document_id, settings)
    assert len(calls) == 1  # no second LLM call

    events = await get_repair_events(session_factory, document_id)
    assert [event for event, _ in events] == [
        "extraction_repair_completed",
        "extraction_repair_skipped",
    ]
    assert events[1][1]["reason"] == "already_repaired"


# ---------------------------------------------------------------------------
# Wiring: the backfill task runs the repair pass too
# ---------------------------------------------------------------------------


async def test_markdown_document_task_runs_repair_pass(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backfill task (apply_markdown → repair → re-embed) hits the repair
    gate — proven by the repair skip event it records (no API key in env)."""
    document_id = await make_doc150_document(
        session_factory, f"repair-task-wiring-{uuid.uuid4().hex[:8]}"
    )
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: session_factory)
    monkeypatch.delenv("LIBRARY_ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        await jobs.markdown_document(document_id=document_id)
    finally:
        get_settings.cache_clear()

    events = await get_repair_events(session_factory, document_id)
    assert events == [("extraction_repair_skipped", {"reason": "missing_api_key"})]


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


async def test_repair_exception_does_not_fail_the_markdown_stage(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LIBRARY_EXTRACTION_DAILY_BUDGET_USD", "100000")
    monkeypatch.setenv("LIBRARY_MARKDOWN_ENABLED", "false")
    get_settings.cache_clear()

    patch_repair(monkeypatch, RuntimeError("anthropic api unreachable"))
    document_id = await make_doc150_document(
        session_factory, f"repair-error-{uuid.uuid4().hex[:8]}"
    )

    try:
        async with session_factory() as session:
            document = await session.get(Document, document_id)
            assert document is not None
            # The markdown stage must complete normally despite the repair error.
            await jobs.run_markdown(session, document)
    finally:
        get_settings.cache_clear()

    events = await get_repair_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "extraction_repair_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "error"
    assert skipped[0]["error"] == "anthropic api unreachable"

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.document_date is None  # nothing was written
        assert "extraction_repair" not in document.extra
