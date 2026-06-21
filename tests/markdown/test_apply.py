"""Integration tests for the markdown apply stage (real test Postgres)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import Settings, get_settings
from library.markdown import apply as markdown_apply
from library.markdown.apply import apply_markdown, todays_markdown_spend_usd
from library.markdown.generator import GeneratedPage, MarkdownResult
from library.models import Document, DocumentPage, DocumentSource, IngestionEvent

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Minimal async context-manager stub for AsyncAnthropic
# ---------------------------------------------------------------------------


def _stub_anthropic():
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None: ...

    return _Client


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_extraction_apply.py)
# ---------------------------------------------------------------------------


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
    return Settings(anthropic_api_key="test-key", markdown_daily_budget_usd=1_000.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def make_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    mime_type: str = "application/pdf",
    ocr_text: str | None = "Example OCR text",
    **kwargs: Any,
) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type=mime_type,
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


def _fake_result(pages: int = 2, cost: float = 0.001) -> MarkdownResult:
    return MarkdownResult(
        pages=[GeneratedPage(page_number=i + 1, markdown=f"# Page {i + 1}") for i in range(pages)],
        model="claude-haiku-4-5",
        prompt_version="t",
        input_tokens=10,
        output_tokens=20,
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_disabled_records_skip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """markdown_enabled=False → markdown_skipped event, no DocumentPage rows."""
    document_id = await make_document(session_factory, "md-apply-disabled")
    settings = Settings(anthropic_api_key="test-key", markdown_enabled=False)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    assert skipped == [{"reason": "disabled"}]

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert pages == []


async def test_missing_api_key_records_skip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document_id = await make_document(session_factory, "md-apply-no-key")
    settings = Settings(markdown_enabled=True)  # anthropic_api_key defaults to None

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    assert skipped == [{"reason": "missing_api_key"}]


async def test_budget_exceeded_records_skip(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "md-apply-budget")

    # Seed a markdown_completed event that blows the budget.
    async with session_factory() as session:
        session.add(
            IngestionEvent(
                document_id=document_id,
                event="markdown_completed",
                detail={"cost_usd": 10.0},
            )
        )
        await session.commit()

    settings = Settings(anthropic_api_key="test-key", markdown_daily_budget_usd=5.0)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert await todays_markdown_spend_usd(session) >= 10.0
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    budget_skips = [d for d in skipped if d.get("reason") == "budget"]
    assert len(budget_skips) == 1
    assert budget_skips[0]["spent_usd"] >= 10.0
    assert budget_skips[0]["budget_usd"] == 5.0


async def test_success_writes_pages_and_event(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: DocumentPage rows written, markdown_completed event recorded."""
    document_id = await make_document(session_factory, "md-apply-happy")

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [b"img1", b"img2"])
    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _stub_anthropic())

    async def fake_generate(*args: Any, **kwargs: Any) -> MarkdownResult:
        return _fake_result(pages=2, cost=0.001)

    monkeypatch.setattr(markdown_apply, "generate_markdown", fake_generate)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage)
                    .where(DocumentPage.document_id == document_id)
                    .order_by(DocumentPage.page_number)
                )
            )
            .scalars()
            .all()
        )

    assert [p.markdown for p in pages] == ["# Page 1", "# Page 2"]
    assert pages[0].char_count == len("# Page 1")
    assert pages[0].page_number == 1

    events = await get_events(session_factory, document_id)
    completed = [detail for ev, detail in events if ev == "markdown_completed"]
    assert len(completed) == 1
    assert completed[0]["model"] == "claude-haiku-4-5"
    assert completed[0]["pages"] == 2
    assert completed[0]["cost_usd"] == pytest.approx(0.001)


async def test_success_replaces_existing_pages(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running apply replaces prior DocumentPage rows, not appends."""
    document_id = await make_document(session_factory, "md-apply-replace")

    # Seed a prior page.
    async with session_factory() as session:
        session.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                markdown="old content",
                char_count=len("old content"),
            )
        )
        await session.commit()

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [b"img1"])
    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _stub_anthropic())

    async def fake_generate(*args: Any, **kwargs: Any) -> MarkdownResult:
        return _fake_result(pages=1, cost=0.001)

    monkeypatch.setattr(markdown_apply, "generate_markdown", fake_generate)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(pages) == 1
    assert pages[0].markdown == "# Page 1"


async def test_no_images_records_skip(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_page_images returns [] → markdown_skipped, no pages."""
    document_id = await make_document(session_factory, "md-apply-no-images", mime_type="text/plain")

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [])

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    assert any(d.get("reason") == "input_unusable" for d in skipped)

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert pages == []


async def test_renderer_raises_records_skip_not_failed(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renderer exception → skip/failed event; document is NOT marked failed.

    This is the critical best-effort test: corrupt uploads must not fail the
    document pipeline stage.
    """
    document_id = await make_document(session_factory, "md-apply-renderer-raises")

    def boom(*a: Any, **k: Any) -> list[bytes]:
        raise RuntimeError("pypdfium2 PdfiumError: corrupt PDF")

    monkeypatch.setattr(markdown_apply, "render_page_images", boom)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # apply_markdown must NOT raise.
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    event_names = [ev for ev, _ in events]
    # Document must get a skip event (not failed), never "markdown_completed".
    assert "markdown_completed" not in event_names
    assert "markdown_skipped" in event_names

    # No DocumentPage rows must have been written.
    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert pages == []


async def test_markdown_skipped_exception_from_generator(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MarkdownSkipped raised by generator → markdown_skipped event, no pages."""
    from library.markdown.generator import MarkdownSkipped

    document_id = await make_document(session_factory, "md-apply-gen-skipped")

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [b"img"])
    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _stub_anthropic())

    async def fake_generate(*args: Any, **kwargs: Any) -> MarkdownResult:
        raise MarkdownSkipped("input_unusable", "model returned no pages")

    monkeypatch.setattr(markdown_apply, "generate_markdown", fake_generate)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    assert any(d.get("reason") == "input_unusable" for d in skipped)

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert pages == []


async def test_generator_generic_exception_records_failed(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic exception from the generator → markdown_failed event; document fine."""
    document_id = await make_document(session_factory, "md-apply-gen-failed")

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [b"img"])
    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _stub_anthropic())

    async def fake_generate(*args: Any, **kwargs: Any) -> MarkdownResult:
        raise RuntimeError("anthropic api unreachable")

    monkeypatch.setattr(markdown_apply, "generate_markdown", fake_generate)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # Must not raise.
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    failed = [detail for ev, detail in events if ev == "markdown_failed"]
    assert len(failed) == 1
    assert "anthropic api unreachable" in failed[0]["error"]

    async with session_factory() as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert pages == []


async def test_extraction_budget_query_excludes_markdown_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """todays_spend_usd (extraction) must not count markdown_completed events."""
    from library.extraction.apply import todays_spend_usd as extraction_spend

    document_id = await make_document(session_factory, "md-apply-budget-isolation")

    async with session_factory() as session:
        # Add a markdown_completed event with cost — must not appear in extraction budget.
        session.add(
            IngestionEvent(
                document_id=document_id,
                event="markdown_completed",
                detail={"cost_usd": 99.0},
            )
        )
        await session.commit()

    async with session_factory() as session:
        extraction_total = await extraction_spend(session)
        markdown_total = await todays_markdown_spend_usd(session)

    # Extraction budget must not include the markdown event.
    assert extraction_total == pytest.approx(0.0)
    # Markdown budget must include it.
    assert markdown_total >= 99.0
