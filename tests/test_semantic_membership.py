"""Tests for the document-vector helper (Smart Groups semantic membership)."""

import dataclasses
import hashlib
import uuid
from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import (
    EMBEDDING_DIM,
    AuthoredSeries,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    DocumentChunk,
    DocumentSource,
    SeriesMode,
    SuggestionState,
)
from library.semantic_membership import (
    MembershipScore,
    document_vectors,
    evaluate_group,
    score_vector,
    sweep_backfill,
)


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


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def make_document(session: AsyncSession) -> Callable[..., Document]:
    """A bare document row (no chunks) to hang embeddings off of, unique per call."""

    async def _make(title: str, **fields: object) -> Document:
        marker = f"{title}-{uuid.uuid4()}"
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=title,
            title=title,
            **fields,
        )
        session.add(document)
        await session.commit()
        return document

    return _make


@pytest.fixture
def add_chunk(session: AsyncSession) -> Callable[[Document, list[float]], None]:
    """Attach a chunk embedding (padded with zeros to EMBEDDING_DIM) to a document."""

    def _add(document: Document, vec: list[float]) -> None:
        padded = list(vec) + [0.0] * (EMBEDDING_DIM - len(vec))
        session.add(
            DocumentChunk(
                document_id=document.id, chunk_index=1, text=document.title, embedding=padded
            )
        )

    return _add


@pytest.mark.integration
async def test_document_vectors_mean_pools_and_normalizes(
    session: AsyncSession, make_document: Callable[..., Document]
) -> None:
    doc = await make_document("ev-charge-fastned")
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
async def test_document_vectors_omits_documents_without_chunks(
    session: AsyncSession, make_document: Callable[..., Document]
) -> None:
    with_chunks = await make_document("with-chunks")
    without_chunks = await make_document("without-chunks")
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


# --- evaluate_group / sweep_backfill: DB-backed membership engine ---


@pytest.mark.integration
async def test_evaluate_group_returns_only_belonging_docs(
    session: AsyncSession,
    settings: Settings,
    make_document: Callable[..., Document],
    add_chunk: Callable[[Document, list[float]], None],
) -> None:
    group = AuthoredSeries(name="ev-eval-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-eval-member")
    add_chunk(member, [0.9, 0.1])
    near = await make_document(title="ev-eval-near")
    add_chunk(near, [0.88, 0.12])
    far = await make_document(title="ev-eval-far")
    add_chunk(far, [0.0, 1.0])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    await session.commit()

    hits = await evaluate_group(session, settings, group.id, [near.id, far.id])
    ids = [doc_id for doc_id, _ in hits]
    assert near.id in ids
    assert far.id not in ids


@pytest.mark.integration
async def test_evaluate_group_empty_candidates_returns_empty(
    session: AsyncSession, settings: Settings
) -> None:
    group = AuthoredSeries(name="ev-eval-empty-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    await session.commit()

    assert await evaluate_group(session, settings, group.id, []) == []


@pytest.mark.integration
async def test_evaluate_group_anchor_only_scores_but_is_not_persisted(
    session: AsyncSession,
    settings: Settings,
    make_document: Callable[..., Document],
    add_chunk: Callable[[Document, list[float]], None],
) -> None:
    """A group with no real members still admits candidates via an anchor's
    similarity alone (extra_positive_ids), and the anchor itself is never
    written as a member — it's a scoring-only seed, not real membership."""
    group = AuthoredSeries(name="ev-anchor-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    await session.commit()  # group has zero members and zero exclusions

    anchor = await make_document(title="ev-anchor-seed")
    add_chunk(anchor, [0.9, 0.1])
    near = await make_document(title="ev-anchor-near")
    add_chunk(near, [0.88, 0.12])
    far = await make_document(title="ev-anchor-far")
    add_chunk(far, [0.0, 1.0])
    await session.commit()

    hits = await evaluate_group(
        session, settings, group.id, [near.id, far.id, anchor.id], extra_positive_ids=[anchor.id]
    )
    hit_ids = {doc_id for doc_id, _ in hits}
    assert near.id in hit_ids
    assert far.id not in hit_ids

    rows = (
        (
            await session.execute(
                select(AuthoredSeriesMember).where(
                    AuthoredSeriesMember.authored_series_id == group.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # evaluate_group never writes members, anchor included


@pytest.mark.integration
async def test_sweep_backfill_is_idempotent_on_repeat_sweep(
    session: AsyncSession,
    settings: Settings,
    make_document: Callable[..., Document],
    add_chunk: Callable[[Document, list[float]], None],
) -> None:
    """A second sweep that re-surfaces a still-pending doc must not raise
    IntegrityError on the (series, document) unique constraint, nor duplicate
    the suggestion row."""
    group = AuthoredSeries(name="ev-sweep-idem-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-sweep-idem-member")
    add_chunk(member, [0.9, 0.1])
    match = await make_document(
        title="ev-sweep-idem-match", amount_total=Decimal("50.00"), currency="EUR"
    )
    add_chunk(match, [0.88, 0.12])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    await session.commit()

    first_hits = await sweep_backfill(session, settings, group.id, anchor_ids=[])
    assert {doc_id for doc_id, _ in first_hits} == {match.id}

    second_hits = await sweep_backfill(session, settings, group.id, anchor_ids=[])
    assert {doc_id for doc_id, _ in second_hits} == {match.id}

    rows = (
        (
            await session.execute(
                select(AuthoredSeriesSuggestion).where(
                    AuthoredSeriesSuggestion.authored_series_id == group.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # no duplicate row from the second sweep
    assert rows[0].document_id == match.id


@pytest.mark.integration
async def test_sweep_backfill_writes_pending_suggestions(
    session: AsyncSession,
    settings: Settings,
    make_document: Callable[..., Document],
    add_chunk: Callable[[Document, list[float]], None],
) -> None:
    group = AuthoredSeries(name="ev-sweep-uniqtag", currency="EUR", mode=SeriesMode.SEMANTIC)
    session.add(group)
    await session.flush()
    member = await make_document(title="ev-sweep-member")
    add_chunk(member, [0.9, 0.1])
    match = await make_document(
        title="ev-sweep-match", amount_total=Decimal("50.00"), currency="EUR"
    )
    add_chunk(match, [0.88, 0.12])
    noise = await make_document(
        title="ev-sweep-noise", amount_total=Decimal("10.00"), currency="EUR"
    )
    add_chunk(noise, [0.0, 1.0])
    session.add(AuthoredSeriesMember(authored_series_id=group.id, document_id=member.id))
    await session.commit()

    hits = await sweep_backfill(session, settings, group.id, anchor_ids=[])
    hit_ids = {doc_id for doc_id, _ in hits}
    assert match.id in hit_ids
    assert noise.id not in hit_ids

    rows = (
        (
            await session.execute(
                select(AuthoredSeriesSuggestion).where(
                    AuthoredSeriesSuggestion.authored_series_id == group.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.document_id for row in rows} == {match.id}
    assert rows[0].state == SuggestionState.PENDING
    assert rows[0].score == pytest.approx(hits[0][1].sim_pos)
