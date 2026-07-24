"""Tests for the document-vector helper (Smart Groups semantic membership)."""

import hashlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource
from library.semantic_membership import document_vectors

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # Deleting documents cascades to their chunks; seeded kinds untouched.
        await session.execute(delete(Document))
        await session.commit()
        yield session


async def make_document(session: AsyncSession, marker: str) -> Document:
    """A bare document row (no chunks) to hang embeddings off of."""
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename=marker,
    )
    session.add(document)
    await session.commit()
    return document


async def test_document_vectors_mean_pools_and_normalizes(session: AsyncSession) -> None:
    doc = await make_document(session, "ev-charge-fastned")
    # Two chunks pointing along +x and +y; mean is (0.5, 0.5, 0, ...) -> normalized.
    dim = EMBEDDING_DIM
    vx = [1.0] + [0.0] * (dim - 1)
    vy = [0.0, 1.0] + [0.0] * (dim - 2)
    session.add(DocumentChunk(document_id=doc.id, chunk_index=1, text="a", embedding=vx))
    session.add(DocumentChunk(document_id=doc.id, chunk_index=2, text="b", embedding=vy))
    await session.commit()

    vectors = await document_vectors(session, [doc.id])
    v = vectors[doc.id]
    norm = sum(c * c for c in v) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)
    assert v[0] == pytest.approx(v[1], abs=1e-6)  # symmetric mean of +x and +y
    assert v[0] == pytest.approx(0.70710678, abs=1e-6)


async def test_document_vectors_omits_documents_without_chunks(session: AsyncSession) -> None:
    with_chunks = await make_document(session, "with-chunks")
    without_chunks = await make_document(session, "without-chunks")
    session.add(
        DocumentChunk(
            document_id=with_chunks.id,
            chunk_index=1,
            text="a",
            embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
        )
    )
    await session.commit()

    vectors = await document_vectors(session, [with_chunks.id, without_chunks.id])

    assert with_chunks.id in vectors
    assert without_chunks.id not in vectors


async def test_document_vectors_empty_input_returns_empty_dict(session: AsyncSession) -> None:
    assert await document_vectors(session, []) == {}
