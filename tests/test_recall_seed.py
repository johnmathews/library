"""The recall corpus seeds through the real embedding path.

Uses a fake embedder — this test is about the seeding mechanics (one chunk per
document, markers resolving to ids), not about vector quality, which only
`library eval-recall` against a real bge-m3 sidecar can measure.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library import jobs
from library.ask.recall_scenarios import CORPUS
from library.cli import _seed_corpus
from library.config import get_settings
from library.models import EMBEDDING_DIM, DocumentChunk

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct, deterministic unit vectors — enough to store, not to rank."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()

    async def fake_embed_texts(
        texts: list[str], *, settings: object, client: object = None
    ) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIM
            vector[hash(text) % EMBEDDING_DIM] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)


async def test_seed_corpus_maps_every_marker_to_an_id(
    session: AsyncSession, fake_embedder: None
) -> None:
    ids_by_marker = await _seed_corpus(session)
    assert set(ids_by_marker) == {doc.marker for doc in CORPUS}
    assert len(set(ids_by_marker.values())) == len(CORPUS), "ids must be distinct"


async def test_seed_corpus_raises_when_embedding_is_disabled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_embed`` is fail-open: a disabled embedder records an
    ``embedding_skipped`` ``IngestionEvent`` and returns without raising, so a
    seed that embedded nothing would otherwise look like success. Verified by
    execution before this fix existed: with the embedder disabled, seeding ran
    to completion, created zero chunks, and raised nothing — this pins the
    fix, not just documents the symptom."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "false")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="produced no chunks"):
        await _seed_corpus(session)


async def test_seed_corpus_produces_exactly_one_chunk_per_document(
    session: AsyncSession, fake_embedder: None
) -> None:
    """The corpus promises document-level recall is unambiguous. Hold it to that.

    If this fails, a body has grown past `embedding_chunk_chars` and a document
    now spans several chunks — recall is still measurable, but the corpus's
    stated invariant is broken and the scenarios module's docstring is lying.
    """
    await _seed_corpus(session)
    per_document = (
        await session.execute(
            select(DocumentChunk.document_id, func.count()).group_by(DocumentChunk.document_id)
        )
    ).all()
    assert len(per_document) == len(CORPUS)
    assert {count for _, count in per_document} == {1}
