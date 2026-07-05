"""DocumentComment model round-trip and cascade behaviour against a real migrated Postgres 17."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from library.models import Document, DocumentComment, DocumentSource

pytestmark = pytest.mark.integration

SHA_A = "2" * 64
SHA_B = "3" * 64


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def test_comment_attaches_and_cascades(session: AsyncSession) -> None:
    """A comment persists against its document and is deleted when the document is."""
    document = Document(sha256=SHA_A, mime_type="application/pdf", source=DocumentSource.UPLOAD)
    session.add(document)
    await session.commit()

    comment = DocumentComment(document_id=document.id, body="this is my current house")
    session.add(comment)
    await session.commit()
    assert comment.created_at is not None

    await session.delete(document)
    await session.commit()

    assert await session.get(DocumentComment, comment.id) is None


async def test_comment_round_trip_via_relationship(session: AsyncSession) -> None:
    """Document.comments loads attached comments ordered by created_at."""
    document = Document(sha256=SHA_B, mime_type="application/pdf", source=DocumentSource.UPLOAD)
    session.add(document)
    await session.commit()

    session.add(DocumentComment(document_id=document.id, body="first comment"))
    session.add(DocumentComment(document_id=document.id, body="second comment"))
    await session.commit()
    session.expunge_all()

    loaded = (
        await session.execute(
            select(Document)
            .where(Document.sha256 == SHA_B)
            .options(selectinload(Document.comments))
        )
    ).scalar_one()
    assert [c.body for c in loaded.comments] == ["first comment", "second comment"]
