"""Tests for hybrid (vector + FTS, RRF) retrieval in library.search."""

import hashlib
import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime

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


def graded_vector(similarity: float) -> list[float]:
    """A 1024-dim unit vector whose cosine similarity to ``unit_vector(0)`` is
    exactly ``similarity`` — so its cosine *distance* to it is ``1 - similarity``.

    Orthogonal one-hot vectors (``unit_vector(1)``, ``(2)``, ...) are all cosine-
    distance 1.0 from ``unit_vector(0)``; using them to build "increasingly far"
    chunks produces a tie the DB breaks arbitrarily (a flaky ordering). This
    puts each chunk at a *strictly* increasing, deterministic distance instead:
    the e0 component carries the similarity and the leftover magnitude goes on a
    second, shared axis (direction is irrelevant — only distance to the e0 query
    ranks the chunks)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = similarity
    vector[1] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
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

    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=10)

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

    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=5)

    hit = next(hit for hit in hits if hit.document.id == document_id)
    assert hit.chunk_text == "the near one"
    assert hit.chunk_index == 1


async def test_collapse_one_row_per_document_nearest_first(session: AsyncSession) -> None:
    """The HNSW-prefetch collapse returns each document once, by its nearest chunk.

    Doc A carries three chunks at graded distances; it must appear exactly once
    (carrying its *nearest* chunk) and must not crowd B/C out of the candidate
    set — the fanout over-fetch + one-row-per-document collapse guarantee this.
    Uses ``graded_vector`` for strictly-increasing, deterministic distances.
    """
    a = await seed_document(
        session,
        "a",
        ocr_text="a",
        chunks=[
            ("a-near", graded_vector(0.9)),
            ("a-mid", graded_vector(0.4)),
            ("a-far", graded_vector(0.2)),
        ],
    )
    b = await seed_document(session, "b", ocr_text="b", chunks=[("b", graded_vector(0.5))])
    c = await seed_document(session, "c", ocr_text="c", chunks=[("c", graded_vector(0.1))])

    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=10)

    ids = [hit.document.id for hit in hits]
    assert ids == [a, b, c]  # ordered by each document's nearest chunk
    assert len(ids) == len(set(ids))  # A's three chunks collapse to one row
    a_hit = next(hit for hit in hits if hit.document.id == a)
    assert a_hit.chunk_text == "a-near"  # carries A's nearest chunk, not a farther one


async def test_unfiltered_search_excludes_soft_deleted_documents(session: AsyncSession) -> None:
    """The index-accelerated unfiltered path still excludes soft-deletes (W3 review fix).

    Regression guard: the HNSW fast path moved the soft-delete exclusion from
    *before* the ANN limit to the post-prefetch collapse. A soft-deleted document
    — here the *nearest* one — must not leak into unfiltered results.
    """
    live = await seed_document(session, "live", ocr_text="a", chunks=[("live", graded_vector(0.9))])
    dead = await seed_document(
        session, "dead", ocr_text="b", chunks=[("dead", graded_vector(0.95))]
    )
    doc = await session.get(Document, dead)
    assert doc is not None
    doc.deleted_at = datetime.now(UTC)  # soft-delete the nearer document
    await session.commit()

    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=10)

    ids = [hit.document.id for hit in hits]
    assert live in ids
    assert dead not in ids


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


async def test_project_slug_filter_restricts_results(session: AsyncSession) -> None:
    from library.models import Document, Project

    in_project = await seed_document(session, "p-in", ocr_text="x", chunks=[("c", unit_vector(0))])
    await seed_document(session, "p-out", ocr_text="y", chunks=[("c", unit_vector(0))])

    document = (
        await session.execute(select(Document).where(Document.id == in_project))
    ).scalar_one()
    document.projects = [Project(slug="audit", name="Audit")]
    await session.commit()

    hits = await semantic_search(
        session,
        query="",
        query_embedding=unit_vector(0),
        filters=DocumentFilters(project_slugs=("audit",)),
        top_k=10,
    )

    assert [hit.document.id for hit in hits] == [in_project]


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

    hits = await semantic_search(session, query="energy", query_embedding=unit_vector(0), top_k=10)

    ids = [hit.document.id for hit in hits]
    assert set(ids) == {vector_only, fts_only}
    assert ids[0] == fts_only  # appears in both retrievers → fused to the top


async def test_many_chunk_document_does_not_crowd_out_others(session: AsyncSession) -> None:
    """The candidate pool counts documents, not chunks: a doc with many near
    chunks must not consume the whole pool and hide other documents."""
    crowded = await seed_document(
        session,
        "crowded",
        ocr_text="x",
        chunks=[(f"chunk {i}", unit_vector(0)) for i in range(8)],
    )
    other = await seed_document(
        session, "other", ocr_text="y", chunks=[("near too", unit_vector(1))]
    )

    # candidate_pool=2 would be fully consumed by the 8-chunk doc without
    # per-document collapsing.
    hits = await semantic_search(
        session, query="", query_embedding=unit_vector(0), top_k=10, candidate_pool=2
    )

    ids = {hit.document.id for hit in hits}
    assert crowded in ids
    assert other in ids


async def test_no_matches_returns_empty(session: AsyncSession) -> None:
    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=5)
    assert hits == []


async def test_semantic_hit_carries_page_number(session: AsyncSession) -> None:
    """A chunk with page_number=N surfaces page_number==N on the SemanticHit."""
    document_id = await seed_document(
        session,
        "page-number-doc",
        ocr_text="solar invoice",
        chunks=[("solar invoice", unit_vector(0))],
    )
    # Update the chunk to have a page_number (seed_document doesn't set it).
    from sqlalchemy import update

    await session.execute(
        update(DocumentChunk).where(DocumentChunk.document_id == document_id).values(page_number=3)
    )
    await session.commit()

    hits = await semantic_search(session, query="solar", query_embedding=unit_vector(0), top_k=5)

    assert hits
    hit = next(h for h in hits if h.document.id == document_id)
    assert hit.page_number == 3


async def test_multi_chunk_passages_for_long_doc(session: AsyncSession) -> None:
    """chunks_per_doc=3 returns the 3 nearest chunks (distance order) for a long
    doc; a single-chunk doc yields exactly one passage."""
    long_doc = await seed_document(
        session,
        "long",
        ocr_text="long multi-topic doc",
        chunks=[
            ("nearest passage", unit_vector(0)),  # cosine distance 0.0
            ("second passage", graded_vector(0.9)),  # 0.1
            ("third passage", graded_vector(0.8)),  # 0.2
            ("fourth passage", graded_vector(0.7)),  # 0.3
        ],
    )
    short_doc = await seed_document(
        session, "short", ocr_text="short doc", chunks=[("only passage", unit_vector(0))]
    )

    # Query embedding is unit_vector(0); the graded chunk vectors sit at strictly
    # increasing cosine distances (0.0 < 0.1 < 0.2 < 0.3), so the nearest-3 order
    # is deterministic — no equidistant tie for the DB to break arbitrarily.
    hits = await semantic_search(
        session, query="", query_embedding=unit_vector(0), top_k=10, chunks_per_doc=3
    )

    by_id = {hit.document.id: hit for hit in hits}
    assert by_id[long_doc].chunk_texts == (
        "nearest passage",
        "second passage",
        "third passage",
    )
    assert by_id[short_doc].chunk_texts == ("only passage",)


async def test_chunks_per_doc_one_is_legacy(session: AsyncSession) -> None:
    """The default single-chunk mode mirrors chunk_text in chunk_texts."""
    document_id = await seed_document(
        session,
        "legacy",
        ocr_text="alpha",
        chunks=[("the near one", unit_vector(0)), ("the far one", unit_vector(200))],
    )

    hits = await semantic_search(session, query="", query_embedding=unit_vector(0), top_k=5)

    hit = next(hit for hit in hits if hit.document.id == document_id)
    assert hit.chunk_texts == (hit.chunk_text,)
    assert hit.chunk_texts == ("the near one",)


async def test_semantic_hit_page_number_none_when_unset(session: AsyncSession) -> None:
    """A chunk without page_number gives page_number==None on the SemanticHit."""
    await seed_document(
        session,
        "no-page-doc",
        ocr_text="energy bill",
        chunks=[("energy bill", unit_vector(1))],
    )

    hits = await semantic_search(session, query="energy", query_embedding=unit_vector(1), top_k=5)

    assert hits
    assert hits[0].page_number is None
