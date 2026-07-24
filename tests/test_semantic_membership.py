"""Tests for the document-vector helper (Smart Groups semantic membership)."""

import dataclasses
import hashlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource
from library.semantic_membership import MembershipScore, document_vectors, score_vector


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


@pytest.mark.integration
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


@pytest.mark.integration
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


@pytest.mark.integration
async def test_document_vectors_empty_input_returns_empty_dict(session: AsyncSession) -> None:
    assert await document_vectors(session, []) == {}


# --- score_vector: pure, no DB (nearest-positive-neighbour with negative veto) ---


def _graded(a: float, b: float) -> list[float]:
    # A 2-D direction embedded in a longer vector; graded so distances are distinct.
    return [a, b] + [0.0] * 1022


def test_belongs_when_near_a_positive_and_no_negatives() -> None:
    cand = _graded(0.99, 0.14)
    positives = [_graded(1.0, 0.0), _graded(0.0, 1.0)]
    result = score_vector(cand, positives, [], tau=0.55, margin=0.02)
    assert result.belongs is True
    assert result.sim_pos > 0.9
    assert result.sim_neg == 0.0


def test_rejected_when_below_threshold() -> None:
    # dot=0.4, |cand|~1.00044, |positive|=1 -> cosine ~0.3998, well below tau=0.9.
    cand = _graded(0.4, 0.917)
    positives = [_graded(1.0, 0.0)]
    result = score_vector(cand, positives, [], tau=0.9, margin=0.02)
    assert result.sim_pos < 0.9
    assert result.belongs is False


def test_negative_veto() -> None:
    # Candidate is close to a positive but even closer to a pruned negative.
    cand = _graded(0.8, 0.6)
    positives = [_graded(0.7, 0.714)]
    negatives = [_graded(0.8, 0.6)]  # identical to candidate -> sim_neg == 1.0
    result = score_vector(cand, positives, negatives, tau=0.55, margin=0.02)
    assert result.sim_neg == pytest.approx(1.0, abs=1e-6)
    assert result.belongs is False


def test_membership_score_is_frozen_dataclass() -> None:
    s = MembershipScore(sim_pos=0.9, sim_neg=0.1, belongs=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.sim_pos = 0.0  # type: ignore[misc]


def test_empty_positives_returns_zero_score() -> None:
    result = score_vector(_graded(1.0, 0.0), [], [_graded(0.0, 1.0)], tau=0.5, margin=0.02)
    assert result == MembershipScore(0.0, 0.0, False)


def test_sim_pos_is_native_float_not_numpy() -> None:
    # pgvector rows surface numpy.float32 scalars; downstream JSON serialization
    # (Tasks 6-7 store/serialize these scores) breaks unless we cast to a native float.
    result = score_vector(_graded(0.99, 0.14), [_graded(1.0, 0.0)], [], tau=0.55, margin=0.02)
    assert isinstance(result.sim_pos, float)
    assert type(result.sim_pos) is float
