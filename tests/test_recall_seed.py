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


async def test_seed_corpus_produces_the_declared_chunk_count_per_document(
    session: AsyncSession, fake_embedder: None
) -> None:
    """Every `RecallDoc.chunks` is a promise about the PIPELINE. Hold it to that.

    This used to assert one chunk per document. That invariant was deliberately
    removed: issue #106 cannot be measured by a corpus with no variation in chunk
    count, so bodies now declare what they produce and this checks the
    declaration end to end.

    Not redundant with the pure guard in `test_recall_scenarios.py`, and the
    difference is the point. That one calls `chunk_text` directly, so it proves
    the arithmetic. This one runs `_seed_corpus` -> `run_embed` -> `chunker_for_mime`
    against a real database, so it proves the *routing*: that the seeder still
    writes `application/pdf` with `ocr_text` and no page rows, and therefore still
    lands on `chunk_text` rather than `chunk_markdown`. A change to the mime type,
    to page seeding, or to `chunker_for_mime`'s dispatch would leave the pure
    guard green and this one red.
    """
    ids_by_marker = await _seed_corpus(session)
    counts = {
        document_id: count
        for document_id, count in (
            await session.execute(
                select(DocumentChunk.document_id, func.count()).group_by(DocumentChunk.document_id)
            )
        ).all()
    }
    assert len(counts) == len(CORPUS)
    mismatched = {
        doc.marker: (doc.chunks, counts[ids_by_marker[doc.marker]])
        for doc in CORPUS
        if counts[ids_by_marker[doc.marker]] != doc.chunks
    }
    assert not mismatched, f"declared vs seeded chunk counts differ: {mismatched}"
