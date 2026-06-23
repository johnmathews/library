"""Model round-trip and FTS behaviour tests against a real migrated Postgres 17."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import (
    Document,
    DocumentChunk,
    DocumentSource,
    DocumentStatus,
    Kind,
    Tag,
    User,
)

pytestmark = pytest.mark.integration

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
SHA_G = "0" * 64


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def test_user_document_tag_round_trip(session: AsyncSession) -> None:
    user = User(username="john", password_hash="x" * 60, display_name="John")
    kind = (await session.execute(select(Kind).where(Kind.slug == "invoice"))).scalar_one()
    tags = [Tag(slug="energy", name="Energy"), Tag(slug="home", name="Home")]
    document = Document(
        sha256=SHA_A,
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        title="Energy invoice",
        document_date=date(2026, 5, 1),
        uploader=user,
        kind=kind,
        tags=tags,
    )
    session.add(document)
    await session.commit()
    session.expunge_all()

    loaded = (await session.execute(select(Document).where(Document.sha256 == SHA_A))).scalar_one()
    assert loaded.title == "Energy invoice"
    assert loaded.status == DocumentStatus.RECEIVED
    assert loaded.kind is not None and loaded.kind.slug == "invoice"
    assert loaded.uploader is not None and loaded.uploader.username == "john"
    assert {tag.slug for tag in loaded.tags} == {"energy", "home"}
    assert loaded.extra == {}
    assert loaded.created_at is not None


async def test_sha256_unique_enforced(session: AsyncSession) -> None:
    session.add(Document(sha256=SHA_B, mime_type="application/pdf", source=DocumentSource.API))
    await session.commit()
    session.add(Document(sha256=SHA_B, mime_type="image/png", source=DocumentSource.EMAIL))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_fts_dutch_stemming(session: AsyncSession) -> None:
    """A document containing "rekeningen" must match the Dutch stem query "rekening"."""
    session.add(
        Document(
            sha256=SHA_C,
            mime_type="application/pdf",
            source=DocumentSource.CONSUME,
            ocr_text="Hierbij ontvangt u uw rekeningen voor mei.",
        )
    )
    await session.commit()

    result = await session.execute(
        text(
            "SELECT sha256 FROM documents"
            " WHERE search_vector_nl @@ websearch_to_tsquery('dutch', 'rekening')"
        )
    )
    assert SHA_C in set(result.scalars())


async def test_document_chunk_round_trip(session: AsyncSession) -> None:
    """A DocumentChunk persists and reloads its 1024-dim embedding intact."""
    document = Document(sha256=SHA_E, mime_type="application/pdf", source=DocumentSource.UPLOAD)
    session.add(document)
    await session.commit()

    vector = [0.0] * 1024
    vector[0] = 1.0
    session.add(
        DocumentChunk(
            document_id=document.id, chunk_index=1, text="travel allowance", embedding=vector
        )
    )
    await session.commit()
    session.expunge_all()

    loaded = (
        await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id))
    ).scalar_one()
    assert loaded.chunk_index == 1
    assert loaded.text == "travel allowance"
    assert len(loaded.embedding) == 1024
    assert float(loaded.embedding[0]) == pytest.approx(1.0)


async def test_document_chunk_cascade_delete(session: AsyncSession) -> None:
    """Deleting a document removes its chunks via the DB-level ON DELETE CASCADE."""
    document = Document(sha256=SHA_F, mime_type="application/pdf", source=DocumentSource.UPLOAD)
    session.add(document)
    await session.commit()
    session.add(
        DocumentChunk(document_id=document.id, chunk_index=1, text="x", embedding=[0.0] * 1024)
    )
    await session.commit()

    await session.delete(document)
    await session.commit()

    remaining = (
        (
            await session.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []


async def test_fts_english_stemming(session: AsyncSession) -> None:
    """A document containing "invoices" must match the English stem query "invoice"."""
    session.add(
        Document(
            sha256=SHA_D,
            mime_type="application/pdf",
            source=DocumentSource.MCP,
            ocr_text="All outstanding invoices were paid in May.",
        )
    )
    await session.commit()

    result = await session.execute(
        text(
            "SELECT sha256 FROM documents"
            " WHERE search_vector_en @@ websearch_to_tsquery('english', 'invoice')"
        )
    )
    assert SHA_D in set(result.scalars())


async def test_document_defaults_to_unreviewed(session: AsyncSession) -> None:
    from library.models import ReviewStatus

    doc = Document(sha256=SHA_G, mime_type="text/plain", source=DocumentSource.UPLOAD)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    assert doc.review_status is ReviewStatus.UNREVIEWED


async def test_ask_thread_cascades_to_turns(session: AsyncSession) -> None:
    from library.models import AskThread, AskTurn

    thread = AskThread(title="Energy bills")
    thread.turns.append(
        AskTurn(query="who?", answer="Vattenfall", model="claude-sonnet-4-6", messages=[])
    )
    session.add(thread)
    await session.commit()

    await session.delete(thread)
    await session.commit()

    remaining = (await session.execute(select(AskTurn))).scalars().all()
    assert remaining == []
