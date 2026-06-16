"""Tests for hybrid (vector + FTS, RRF) retrieval in library.search."""

import hashlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import (
    EMBEDDING_DIM,
    Document,
    DocumentChunk,
    DocumentSource,
    Kind,
)
from library.search import DocumentFilters, semantic_search

pytestmark = pytest.mark.integration


def unit_vector(index: int) -> list[float]:
    """A 1024-dim unit vector with 1.0 at ``index`` (orthogonal across indices)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # These tests assert on absolute ranking/emptiness, so start each from a
        # clean slate in the shared (session-scoped) API database. Deleting
        # documents cascades to their chunks; seeded kinds are untouched.
        await session.execute(delete(Document))
        await session.commit()
        yield session


async def seed_document(
    session: AsyncSession,
    marker: str,
    *,
    ocr_text: str,
    chunks: list[tuple[str, list[float]]],
    kind_slug: str | None = None,
) -> int:
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        ocr_text=ocr_text,
        kind=kind,
    )
    session.add(document)
    await session.commit()
    for index, (text, embedding) in enumerate(chunks, start=1):
        session.add(
            DocumentChunk(
                document_id=document.id, chunk_index=index, text=text, embedding=embedding
            )
        )
    await session.commit()
    return document.id


async def test_ranks_documents_by_vector_similarity(session: AsyncSession) -> None:
    near = await seed_document(
        session, "near", ocr_text="alpha", chunks=[("near chunk", unit_vector(0))]
    )
    far = await seed_document(
        session, "far", ocr_text="beta", chunks=[("far chunk", unit_vector(100))]
    )

    hits = await semantic_search(
        session, query="", query_embedding=unit_vector(0), top_k=10
    )

    ids = [hit.document.id for hit in hits]
    assert near in ids and far in ids
    assert ids[0] == near  # nearest vector ranks first


async def test_hit_carries_nearest_chunk_text(session: AsyncSession) -> None:
    document_id = await seed_document(
        session,
        "two-chunks",
        ocr_text="alpha",
        chunks=[("the near one", unit_vector(0)), ("the far one", unit_vector(200))],
    )

    hits = await semantic_search(
        session, query="", query_embedding=unit_vector(0), top_k=5
    )

    hit = next(hit for hit in hits if hit.document.id == document_id)
    assert hit.chunk_text == "the near one"
    assert hit.chunk_index == 1


async def test_filters_restrict_results(session: AsyncSession) -> None:
    invoice = await seed_document(
        session, "inv", ocr_text="x", chunks=[("c", unit_vector(0))], kind_slug="invoice"
    )
    await seed_document(
        session, "con", ocr_text="y", chunks=[("c", unit_vector(0))], kind_slug="contract"
    )

    hits = await semantic_search(
        session,
        query="",
        query_embedding=unit_vector(0),
        filters=DocumentFilters(kind_slug="invoice"),
        top_k=10,
    )

    assert [hit.document.id for hit in hits] == [invoice]


async def test_hybrid_fuses_vector_and_fts(session: AsyncSession) -> None:
    """A doc found only by FTS and one found only by vector both surface; the
    FTS match (also present in the vector tail) is boosted to the top."""
    vector_only = await seed_document(
        session, "vec", ocr_text="totally unrelated words", chunks=[("c", unit_vector(0))]
    )
    fts_only = await seed_document(
        session,
        "fts",
        ocr_text="energy invoice from the provider",
        chunks=[("c", unit_vector(300))],
    )

    hits = await semantic_search(
        session, query="energy", query_embedding=unit_vector(0), top_k=10
    )

    ids = [hit.document.id for hit in hits]
    assert set(ids) == {vector_only, fts_only}
    assert ids[0] == fts_only  # appears in both retrievers → fused to the top


async def test_no_matches_returns_empty(session: AsyncSession) -> None:
    hits = await semantic_search(
        session, query="", query_embedding=unit_vector(0), top_k=5
    )
    assert hits == []
