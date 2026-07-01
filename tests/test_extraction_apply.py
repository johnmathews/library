"""Integration tests for applying extraction results (real test Postgres)."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import Settings, get_settings
from library.extraction import apply as apply_module
from library.extraction.apply import (
    apply_extraction,
    match_user_by_email,
    resolve_recipient_from_email,
    todays_spend_usd,
    upsert_recipient,
)
from library.extraction.extractor import (
    PROMPT_VERSION,
    CallUsage,
    ExtractionOutcome,
)
from library.extraction.schema import ExtractedMetadata
from library.jobs import advance_pipeline, extract_document, job_app
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    Recipient,
    Sender,
    Tag,
    User,
)
from library.ocr import router as ocr_router
from library.ocr.base import OcrResult

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
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Direct-call settings: key present, budget far above any test spend."""
    return Settings(anthropic_api_key="test-key", extraction_daily_budget_usd=1_000.0)


@pytest.fixture
def fake_router(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> OcrResult:
    result = OcrResult(
        text="Factuur Eneco mei 2026, totaal 123,45 EUR",
        confidence=90.0,
        searchable_pdf=None,
        engine="tesseract",
        pages=1,
    )

    def fake_run_ocr(document: Document, original_path: Path, derived: Path) -> OcrResult:
        return result

    monkeypatch.setattr(ocr_router, "run_ocr", fake_run_ocr)
    return result


def make_metadata(**overrides: Any) -> ExtractedMetadata:
    base: dict[str, Any] = {
        "kind_slug": "invoice",
        "sender_name": "Eneco",
        "recipient_name": "John",
        "title": "Energierekening mei 2026",
        "summary": "Maandfactuur voor energie. Te betalen voor 1 juli 2026.",
        "document_date": "2026-05-15",
        "amount_total": "123.45",
        "currency": "EUR",
        "due_date": "2026-07-01",
        "expiry_date": None,
        "language": "nld",
        "tags": ["energie", "wonen"],
        "confidence": "high",
        "reasoning_note": None,
    }
    base.update(overrides)
    return ExtractedMetadata.model_validate(base)


def make_outcome(metadata: ExtractedMetadata, cost_usd: float = 0.002) -> ExtractionOutcome:
    return ExtractionOutcome(
        metadata=metadata,
        model="claude-haiku-4-5",
        prompt_version=PROMPT_VERSION,
        input_mode="text",
        escalated=False,
        calls=[
            CallUsage(
                model="claude-haiku-4-5",
                input_tokens=1_000,
                output_tokens=200,
                cost_usd=cost_usd,
            )
        ],
    )


def patch_extract(monkeypatch: pytest.MonkeyPatch, outcome: ExtractionOutcome | Exception) -> None:
    async def fake_extract(document: Document, ocr_text: str, **kwargs: Any) -> ExtractionOutcome:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(apply_module, "extract", fake_extract)


async def make_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    ocr_text: str | None = "Factuur Eneco",
    **kwargs: Any,
) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.pdf",
            ocr_text=ocr_text,
            **kwargs,
        )
        session.add(document)
        await session.commit()
        return document.id


async def get_events(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> list[tuple[str, dict[str, Any]]]:
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(IngestionEvent)
                    .where(IngestionEvent.document_id == document_id)
                    .order_by(IngestionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        return [(event.event, event.detail) for event in events]


async def test_dutch_invoice_outcome_populates_metadata(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_extract(monkeypatch, make_outcome(make_metadata()))
    document_id = await make_document(session_factory, "apply-happy")

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.kind is not None and document.kind.slug == "invoice"
        assert document.sender is not None and document.sender.name == "Eneco"
        assert document.recipient is not None and document.recipient.name == "John"
        assert document.title == "Energierekening mei 2026"
        assert document.document_date == date(2026, 5, 15)
        assert document.due_date == date(2026, 7, 1)
        assert document.amount_total == Decimal("123.45")
        assert document.currency == "EUR"
        assert document.language is DocumentLanguage.NLD
        assert {tag.slug for tag in document.tags} >= {"energie", "wonen"}
        extraction = document.extra["extraction"]
        assert extraction["prompt_version"] == PROMPT_VERSION
        assert extraction["model"] == "claude-haiku-4-5"
        assert extraction["confidence"] == "high"
        assert extraction["cost_usd"] == pytest.approx(0.002)
        assert set(extraction["fields_set"]) >= {
            "kind_id",
            "sender_id",
            "recipient_id",
            "title",
            "amount_total",
        }

    events = await get_events(session_factory, document_id)
    completed = [detail for event, detail in events if event == "extraction_completed"]
    assert len(completed) == 1
    assert completed[0]["model"] == "claude-haiku-4-5"
    assert completed[0]["cost_usd"] == pytest.approx(0.002)
    assert completed[0]["input_tokens"] == 1_000


async def test_sender_upsert_is_case_insensitive(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        session.add(Sender(name="ENECO Services"))
        await session.commit()

    patch_extract(monkeypatch, make_outcome(make_metadata(sender_name="eneco services")))
    document_id = await make_document(session_factory, "apply-sender-ci")

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Sender)
                .where(func.lower(Sender.name) == "eneco services")
            )
        ).scalar_one()
        assert count == 1
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.sender is not None and document.sender.name == "ENECO Services"


async def test_recipient_upsert_is_case_insensitive(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        session.add(Recipient(name="Apply CI Recipient"))
        await session.commit()

    patch_extract(monkeypatch, make_outcome(make_metadata(recipient_name="apply ci recipient")))
    document_id = await make_document(session_factory, "apply-recipient-ci")

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Recipient)
                .where(func.lower(Recipient.name) == "apply ci recipient")
            )
        ).scalar_one()
        assert count == 1
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.recipient is not None and document.recipient.name == "Apply CI Recipient"


async def test_user_edited_recipient_is_never_overwritten(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        locked = Recipient(name="Apply Locked Recipient")
        session.add(locked)
        await session.commit()
        locked_id = locked.id

    patch_extract(monkeypatch, make_outcome(make_metadata(recipient_name="John")))
    document_id = await make_document(
        session_factory,
        "apply-recipient-locked",
        recipient_id=locked_id,
        extra={"user_edited_fields": ["recipient_id"]},
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert (
            document.recipient is not None and document.recipient.name == "Apply Locked Recipient"
        )
        assert "recipient_id" not in document.extra["extraction"]["fields_set"]


async def test_tags_are_created_once_and_reused(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        session.add(Tag(slug="zonnepanelen", name="Zonnepanelen"))
        await session.commit()

    patch_extract(monkeypatch, make_outcome(make_metadata(tags=["zonnepanelen", "verzekering"])))
    document_id = await make_document(session_factory, "apply-tags")

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        for slug in ("zonnepanelen", "verzekering"):
            count = (
                await session.execute(select(func.count()).select_from(Tag).where(Tag.slug == slug))
            ).scalar_one()
            assert count == 1
        document = await session.get(Document, document_id)
        assert document is not None
        assert {tag.slug for tag in document.tags} == {"zonnepanelen", "verzekering"}


async def test_user_edited_fields_are_never_overwritten(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_extract(monkeypatch, make_outcome(make_metadata()))
    document_id = await make_document(
        session_factory,
        "apply-user-edited",
        title="My own title",
        extra={"user_edited_fields": ["title", "tags"]},
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.title == "My own title"
        assert document.tags == []
        assert document.summary == make_metadata().summary  # non-edited fields still set
        fields_set = document.extra["extraction"]["fields_set"]
        assert "title" not in fields_set
        assert "tags" not in fields_set
        # The user_edited_fields marker survives the extra merge.
        assert document.extra["user_edited_fields"] == ["title", "tags"]


async def test_reextraction_overwrites_previous_extraction_values(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "apply-rerun")

    patch_extract(monkeypatch, make_outcome(make_metadata(title="First title")))
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    patch_extract(monkeypatch, make_outcome(make_metadata(title="Second title")))
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.title == "Second title"


async def test_extraction_failure_still_reaches_indexed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LIBRARY_EXTRACTION_DAILY_BUDGET_USD", "1000")
    get_settings.cache_clear()
    patch_extract(monkeypatch, RuntimeError("anthropic api unreachable"))
    document_id = await make_document(session_factory, "apply-failure", ocr_text=None)

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.status is DocumentStatus.INDEXED

    events = await get_events(session_factory, document_id)
    failed = [detail for event, detail in events if event == "extraction_failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "anthropic api unreachable"
    assert failed[0]["prompt_version"] == PROMPT_VERSION


async def test_extraction_skipped_without_api_key_and_document_indexed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIBRARY_ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    document_id = await make_document(session_factory, "apply-no-key", ocr_text=None)

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.status is DocumentStatus.INDEXED

    events = await get_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "extraction_skipped"]
    assert skipped == [{"reason": "missing_api_key"}]


async def test_extraction_skipped_when_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_extract(monkeypatch, RuntimeError("must not be called"))
    document_id = await make_document(session_factory, "apply-disabled")
    settings = Settings(anthropic_api_key="test-key", extraction_enabled=False)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "extraction_skipped"]
    assert skipped == [{"reason": "disabled"}]


async def test_budget_exceeded_skips_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "apply-budget")
    async with session_factory() as session:
        session.add(
            IngestionEvent(
                document_id=document_id,
                event="extraction_completed",
                detail={"cost_usd": 10.0},
            )
        )
        await session.commit()

    patch_extract(monkeypatch, RuntimeError("must not be called"))
    settings = Settings(anthropic_api_key="test-key", extraction_daily_budget_usd=5.0)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert await todays_spend_usd(session) >= 10.0
        await apply_extraction(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "extraction_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "budget"
    assert skipped[0]["spent_usd"] >= 10.0
    assert skipped[0]["budget_usd"] == 5.0


async def test_apply_sets_needs_review_on_flagged_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future document_date makes the document needs_review with a finding."""
    metadata = ExtractedMetadata(
        kind_slug="invoice",
        sender_name="Eneco",
        recipient_name="John",
        title="t",
        summary="s",
        document_date=date(2099, 1, 1),
        amount_total="10.00",
        currency="EUR",
        due_date=None,
        expiry_date=None,
        language="nld",
        tags=[],
        confidence="high",
        reasoning_note=None,
    )
    outcome = ExtractionOutcome(
        metadata=metadata,
        model="claude-haiku-4-5",
        prompt_version=PROMPT_VERSION,
        input_mode="text",
        escalated=False,
        calls=[CallUsage("claude-haiku-4-5", 10, 10, 0.0)],
    )

    async def fake_extract(document, ocr_text, *, client, settings):
        return outcome

    monkeypatch.setattr(apply_module, "extract", fake_extract)

    async with session_factory() as session:
        doc = Document(
            sha256="d" * 64,
            mime_type="text/plain",
            source=DocumentSource.UPLOAD,
            ocr_text="Factuur Eneco totaal 10,00",
            extra={},
        )
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
        kind_slug="invoice",
        sender_name="Eneco",
        recipient_name="John",
        title="t",
        summary="s",
        document_date=date(2026, 5, 1),
        amount_total="10.00",
        currency="EUR",
        due_date=None,
        expiry_date=None,
        language="nld",
        tags=[],
        confidence="high",
        reasoning_note=None,
    )
    outcome = ExtractionOutcome(
        metadata=metadata,
        model="claude-haiku-4-5",
        prompt_version=PROMPT_VERSION,
        input_mode="text",
        escalated=False,
        calls=[CallUsage("claude-haiku-4-5", 10, 10, 0.0)],
    )

    async def fake_extract(document, ocr_text, *, client, settings):
        return outcome

    monkeypatch.setattr(apply_module, "extract", fake_extract)

    async with session_factory() as session:
        doc = Document(
            sha256="e" * 64,
            mime_type="text/plain",
            source=DocumentSource.UPLOAD,
            ocr_text="Factuur Eneco totaal 10,00 EUR",
            ocr_confidence=95.0,
            extra={},
        )
        session.add(doc)
        await session.commit()
        await apply_extraction(session, doc, settings)
        await session.refresh(doc)

    from library.models import ReviewStatus

    assert doc.review_status is ReviewStatus.UNREVIEWED
    assert doc.extra["validation"]["findings"] == []


async def test_extract_document_task_registered_and_deferrable() -> None:
    assert extract_document.name == "library.jobs.extract_document"
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        async with job_app.open_async():
            await extract_document.defer_async(document_id=7)
        assert len(connector.jobs) == 1
        job = next(iter(connector.jobs.values()))
        assert job["task_name"] == "library.jobs.extract_document"
        assert job["args"] == {"document_id": 7}


# ----------------------------------------- dual-name recipient resolution (W13)


async def test_upsert_recipient_matches_username(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A name equal to a user's username resolves to that user's linked recipient."""
    async with session_factory() as session:
        user = User(username="alice", password_hash="x", display_name="Alice Smith")
        session.add(user)
        await session.flush()

        recipient = await upsert_recipient(session, "Alice")  # case-insensitive username
        await session.commit()

        assert recipient.user_id == user.id
        # Named by the display name, not the matched username.
        assert recipient.name == "Alice Smith"


async def test_upsert_recipient_matches_display_name_same_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Username and display name resolve to one and the same recipient row."""
    async with session_factory() as session:
        user = User(username="bob", password_hash="x", display_name="Bob Jones")
        session.add(user)
        await session.flush()

        by_username = await upsert_recipient(session, "bob")
        by_display = await upsert_recipient(session, "BOB JONES")
        await session.commit()

        assert by_username.id == by_display.id
        assert by_display.user_id == user.id


async def test_upsert_recipient_plain_name_unlinked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A name matching no user upserts a plain, user-less recipient."""
    async with session_factory() as session:
        recipient = await upsert_recipient(session, "Acme Logistics BV")
        await session.commit()

        assert recipient.user_id is None
        assert recipient.name == "Acme Logistics BV"


# --------------------------------- email To: → recipient fallback (only-fill-when-empty)


async def _make_user_with_forward(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    display_name: str,
    forward_addresses: list[str],
) -> int:
    async with session_factory() as session:
        user = User(
            username=username,
            password_hash="x",
            display_name=display_name,
            preferences={"notifications": {"email_forward_addresses": forward_addresses}},
        )
        session.add(user)
        await session.commit()
        return user.id


async def test_match_user_by_email_is_case_insensitive(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    address = f"box-{uuid.uuid4().hex[:8]}@example.com"
    user_id = await _make_user_with_forward(
        session_factory,
        username=f"mbe-{uuid.uuid4().hex[:8]}",
        display_name="Match By Email",
        forward_addresses=[address],
    )
    async with session_factory() as session:
        assert await match_user_by_email(session, f"  {address.upper()} ") == user_id
        assert await match_user_by_email(session, f"nobody-{uuid.uuid4().hex}@example.com") is None
        assert await match_user_by_email(session, "") is None


async def test_resolve_recipient_from_email_returns_user_recipient(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    address = f"rr-{uuid.uuid4().hex[:8]}@example.com"
    display_name = f"Recipient User {uuid.uuid4().hex[:6]}"
    user_id = await _make_user_with_forward(
        session_factory,
        username=f"rru-{uuid.uuid4().hex[:8]}",
        display_name=display_name,
        forward_addresses=[address],
    )
    async with session_factory() as session:
        # First matching address wins; unknown ones are skipped.
        recipient_id = await resolve_recipient_from_email(
            session, [f"unknown-{uuid.uuid4().hex}@x.test", address.upper()]
        )
        await session.commit()
        assert recipient_id is not None
        recipient = await session.get(Recipient, recipient_id)
        assert recipient is not None
        assert recipient.user_id == user_id
        assert recipient.name == display_name


async def test_resolve_recipient_from_email_unknown_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await resolve_recipient_from_email(session, []) is None
        assert (
            await resolve_recipient_from_email(session, [f"nobody-{uuid.uuid4().hex}@x.test"])
            is None
        )


async def test_apply_fills_recipient_from_email_to_when_llm_empty(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM leaves recipient null → fallback fills it from the email To: user."""
    address = f"fill-{uuid.uuid4().hex[:8]}@example.com"
    display_name = f"Fallback User {uuid.uuid4().hex[:6]}"
    user_id = await _make_user_with_forward(
        session_factory,
        username=f"fbu-{uuid.uuid4().hex[:8]}",
        display_name=display_name,
        forward_addresses=[address],
    )
    patch_extract(monkeypatch, make_outcome(make_metadata(recipient_name=None)))
    document_id = await make_document(
        session_factory, f"apply-to-fallback-{uuid.uuid4().hex[:8]}", extra={"email_to": [address]}
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.recipient is not None
        assert document.recipient.user_id == user_id
        assert document.recipient.name == display_name
        assert "recipient_id" in document.extra["extraction"]["fields_set"]


async def test_apply_llm_recipient_wins_over_email_to(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the LLM names a recipient, the To:-derived user is NOT used."""
    address = f"lose-{uuid.uuid4().hex[:8]}@example.com"
    llm_recipient = f"Acme Corp {uuid.uuid4().hex[:6]}"
    await _make_user_with_forward(
        session_factory,
        username=f"lwu-{uuid.uuid4().hex[:8]}",
        display_name=f"Should Not Win {uuid.uuid4().hex[:6]}",
        forward_addresses=[address],
    )
    patch_extract(monkeypatch, make_outcome(make_metadata(recipient_name=llm_recipient)))
    document_id = await make_document(
        session_factory, f"apply-llm-wins-{uuid.uuid4().hex[:8]}", extra={"email_to": [address]}
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.recipient is not None
        assert document.recipient.name == llm_recipient
        assert document.recipient.user_id is None  # the plain LLM name, not the To: user


async def test_apply_email_to_matching_nobody_leaves_recipient_none(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A To: address matching no user leaves recipient null when the LLM is empty."""
    patch_extract(monkeypatch, make_outcome(make_metadata(recipient_name=None)))
    document_id = await make_document(
        session_factory,
        f"apply-to-nobody-{uuid.uuid4().hex[:8]}",
        extra={"email_to": [f"nobody-{uuid.uuid4().hex}@example.com"]},
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_extraction(session, document, settings)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.recipient_id is None
        assert "recipient_id" not in document.extra["extraction"]["fields_set"]
