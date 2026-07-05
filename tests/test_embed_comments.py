"""Tests for embedding document comments as extra chunks (Task 4).

Mirrors ``tests/test_embedding_pipeline.py``'s fixtures/conventions: a fake,
deterministic ``embed_texts`` stand-in and a per-test async engine bound to
the shared integration Postgres database.
"""

import hashlib
import math
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library import jobs
from library.config import Settings, get_settings
from library.jobs import run_embed
from library.models import (
    EMBEDDING_DIM,
    Document,
    DocumentChunk,
    DocumentComment,
    DocumentSource,
    DocumentStatus,
)
from library.search import semantic_search

pytestmark = pytest.mark.integration


@pytest.fixture
def enable_embedding(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _fake_embed_texts(
    texts: list[str], *, settings: Settings, client: object | None = None
) -> list[list[float]]:
    """Deterministic stand-in for the sidecar: one unit-ish vector per text."""
    return [[float(len(text) % 7)] + [0.0] * (EMBEDDING_DIM - 1) for text in texts]


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


def graded_vector(similarity: float) -> list[float]:
    """Deterministic vector at cosine similarity ``similarity`` to unit_vector(0)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = similarity
    vector[1] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


async def make_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    ocr_text: str | None,
    status: DocumentStatus = DocumentStatus.INDEXED,
) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            ocr_text=ocr_text,
            status=status,
        )
        session.add(document)
        await session.commit()
        return document.id


async def chunks_for(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> list[DocumentChunk]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )


async def test_run_embed_emits_one_chunk_per_comment(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    document_id = await make_document(session_factory, "embed-comment", ocr_text="area is 120 sqm")

    async with session_factory() as session:
        session.add(DocumentComment(document_id=document_id, body="this is my current house"))
        await session.commit()

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    comment_chunks = [c for c in chunks if c.comment_id is not None]
    content_chunks = [c for c in chunks if c.comment_id is None]
    assert len(comment_chunks) == 1
    assert "this is my current house" in comment_chunks[0].text
    assert comment_chunks[0].text.startswith("User comment (")
    assert comment_chunks[0].page_number is None
    assert content_chunks  # content chunks still present
    assert [c.chunk_index for c in chunks] == list(range(1, len(chunks) + 1))
    # Comment chunk(s) come after content chunks, still monotonic.
    assert comment_chunks[0].chunk_index == len(chunks)


async def test_run_embed_comment_only_document_still_embeds(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document with no OCR text but a comment should not be skipped as no_text."""
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    document_id = await make_document(session_factory, "embed-comment-only", ocr_text=None)

    async with session_factory() as session:
        session.add(DocumentComment(document_id=document_id, body="a note about this doc"))
        await session.commit()

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    assert len(chunks) == 1
    assert chunks[0].comment_id is not None


async def test_run_embed_is_idempotent_with_comments(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    document_id = await make_document(session_factory, "embed-comment-idempotent", ocr_text="text")

    async with session_factory() as session:
        session.add(DocumentComment(document_id=document_id, body="hello"))
        await session.commit()

    for _ in range(2):
        async with session_factory() as session:
            document = await session.get(Document, document_id)
            assert document is not None
            await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    comment_chunks = [c for c in chunks if c.comment_id is not None]
    assert len(comment_chunks) == 1  # re-embedding replaced, did not duplicate


async def test_comment_chunk_is_retrievable_via_semantic_search(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comment makes its document retrievable for a matching query.

    Uses a deterministic fake embedder (per the repo's graded_vector
    convention — see tests/test_semantic_search.py) that maps the comment
    text near the query vector and the unrelated document body far from it,
    so this is a real run_embed -> semantic_search round trip with no
    reliance on the actual embedding sidecar.
    """

    async def fake_embed_texts(
        texts: list[str], *, settings: Settings, client: object | None = None
    ) -> list[list[float]]:
        return [
            graded_vector(0.9) if "current house" in text else graded_vector(0.1) for text in texts
        ]

    async with session_factory() as session:
        # Clean slate: this asserts on absolute top-k membership against the
        # shared (session-scoped) integration database, mirroring
        # tests/test_semantic_search.py's isolation convention.
        await session.execute(delete(Document))
        await session.commit()

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)
    document_id = await make_document(
        session_factory, "embed-comment-retrieval", ocr_text="unrelated invoice text"
    )

    async with session_factory() as session:
        session.add(DocumentComment(document_id=document_id, body="this is my current house"))
        await session.commit()

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    async with session_factory() as session:
        hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=10)

    hit = next(h for h in hits if h.document.id == document_id)
    assert "this is my current house" in hit.chunk_text
