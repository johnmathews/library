"""Tests for the Ask agent's ``get_document`` read tool.

Covers the structured-fields + comments + full-text payload, the
ocr_text-vs-pages text source, truncation, and the not-found guard.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.ask import engine as ask_engine
from library.ask.engine import _run_get_document
from library.config import get_settings
from library.models import DocumentComment, DocumentPage
from tests.test_documents_api import _seed_document

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _open_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_document_returns_fields_comments_and_text(api_database_url: str) -> None:
    document_id = await _seed_document(
        api_database_url,
        "askr-get-doc",
        title="Floor plan letter",
        ocr_text="The internal floor area is 120 square metres.",
    )

    async with _open_session(api_database_url) as session:
        session.add(DocumentComment(document_id=document_id, body="this is my current house"))
        await session.commit()

    async with _open_session(api_database_url) as session:
        result = await _run_get_document(session, get_settings(), {"document_id": document_id})

    assert result["title"] == "Floor plan letter"
    assert "120 square metres" in result["text"]
    assert any("current house" in c["body"] for c in result["comments"])
    assert result["text_truncated"] is False


@pytest.mark.asyncio
async def test_get_document_prefers_pages_over_ocr_text(api_database_url: str) -> None:
    document_id = await _seed_document(
        api_database_url,
        "askr-get-doc-pages",
        title="Paginated contract",
        ocr_text="stale ocr text that should not be used",
    )

    async with _open_session(api_database_url) as session:
        session.add_all(
            [
                DocumentPage(
                    document_id=document_id, page_number=1, markdown="# Page one", char_count=10
                ),
                DocumentPage(
                    document_id=document_id,
                    page_number=2,
                    markdown="Second page content",
                    char_count=19,
                ),
            ]
        )
        await session.commit()

    async with _open_session(api_database_url) as session:
        result = await _run_get_document(session, get_settings(), {"document_id": document_id})

    assert "Page one" in result["text"]
    assert "Second page content" in result["text"]
    assert "stale ocr text" not in result["text"]
    # Page order is preserved.
    assert result["text"].index("Page one") < result["text"].index("Second page content")


@pytest.mark.asyncio
async def test_get_document_truncates_text_and_flags_it(api_database_url: str) -> None:
    document_id = await _seed_document(
        api_database_url,
        "askr-get-doc-truncate",
        title="Long letter",
        ocr_text="x" * 500,
    )

    small_settings = get_settings().model_copy(update={"ask_get_document_max_chars": 100})

    async with _open_session(api_database_url) as session:
        result = await _run_get_document(session, small_settings, {"document_id": document_id})

    assert result["text_truncated"] is True
    assert len(result["text"]) == 100


@pytest.mark.asyncio
async def test_get_document_missing_id_returns_error(api_database_url: str) -> None:
    async with _open_session(api_database_url) as session:
        result = await _run_get_document(session, get_settings(), {"document_id": 999_999_999})

    assert "error" in result


def test_get_document_registered_in_tools() -> None:
    names = {tool["name"] for tool in ask_engine.TOOLS}
    assert "get_document" in names
