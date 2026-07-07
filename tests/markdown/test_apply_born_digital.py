"""Integration tests for born-digital markdown passthrough (real test Postgres).

For ``text/markdown``/``text/plain`` the markdown stage synthesizes one
DocumentPage directly from ``ocr_text`` — no Anthropic call, no budget.
"""

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
from library.docx import DOCX_MIME
from library.markdown import apply as markdown_apply
from library.markdown.apply import apply_markdown
from library.models import Document, DocumentPage, DocumentSource, IngestionEvent

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
    return Settings(anthropic_api_key="test-key", markdown_daily_budget_usd=1_000.0)


async def make_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    mime_type: str,
    ocr_text: str | None,
) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type=mime_type,
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.md",
            ocr_text=ocr_text,
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


async def get_pages(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> list[DocumentPage]:
    async with session_factory() as session:
        return list(
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


def _forbid_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any AsyncAnthropic construction an immediate test failure."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("AsyncAnthropic must not be constructed for born-digital markdown")

    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _boom)


@pytest.mark.parametrize("mime_type", ["text/markdown", "text/plain", DOCX_MIME])
async def test_born_digital_writes_single_page_no_anthropic(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mime_type: str,
) -> None:
    _forbid_anthropic(monkeypatch)
    body = "# Heading\n\n- one\n- two\n\nlast paragraph"
    document_id = await make_document(
        session_factory, f"born-{mime_type}", mime_type=mime_type, ocr_text=body
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    pages = await get_pages(session_factory, document_id)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].markdown == body  # full body verbatim
    assert pages[0].char_count == len(body)

    # page_count is set to match the single synthesized page.
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.page_count == 1

    events = await get_events(session_factory, document_id)
    completed = [detail for ev, detail in events if ev == "markdown_completed"]
    assert len(completed) == 1
    assert completed[0] == {"engine": "passthrough", "model": None, "pages": 1, "cost_usd": 0.0}


async def test_born_digital_replaces_existing_page(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_anthropic(monkeypatch)
    document_id = await make_document(
        session_factory, "born-replace", mime_type="text/markdown", ocr_text="new body"
    )
    async with session_factory() as session:
        session.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                markdown="stale",
                char_count=len("stale"),
            )
        )
        await session.commit()

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    pages = await get_pages(session_factory, document_id)
    assert len(pages) == 1
    assert pages[0].markdown == "new body"


async def test_born_digital_empty_body_records_no_text(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_anthropic(monkeypatch)
    document_id = await make_document(
        session_factory, "born-empty", mime_type="text/markdown", ocr_text="   \n\n  "
    )

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await apply_markdown(session, document, settings)

    events = await get_events(session_factory, document_id)
    skipped = [detail for ev, detail in events if ev == "markdown_skipped"]
    assert skipped == [{"reason": "no_text"}]
    assert "markdown_completed" not in [ev for ev, _ in events]
    assert await get_pages(session_factory, document_id) == []

    # The no-text branch writes no page, so page_count stays unchanged (None).
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.page_count is None
