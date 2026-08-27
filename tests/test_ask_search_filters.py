"""Filters, reach and depth on Ask's semantic_search tool (#5, #7)."""

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.ask import engine as engine_mod
from library.ask.engine import TOOLS, _run_semantic_search, _top_k_arg
from library.config import get_settings
from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource, Kind
from library.search import DocumentFilters, search_reach

pytestmark = pytest.mark.integration


def vec(index: int) -> list[float]:
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
        yield session


@pytest.fixture
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every query embeds to the same vector the seeded chunks carry, so
    ranking is decided by the filters under test rather than by similarity."""

    async def fake_embed_query(text: str, *, settings: Any, client: Any = None) -> list[float]:
        return vec(0)

    monkeypatch.setattr(engine_mod, "embed_query", fake_embed_query)


async def seed(
    session: AsyncSession,
    marker: str,
    *,
    kind_slug: str | None = None,
    chunks: tuple[tuple[str, list[float]], ...] = (),
) -> int:
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        ocr_text=f"{marker} alpha",
        kind=kind,
        title=marker,
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


def test_schema_offers_the_shared_filters_but_not_review_status() -> None:
    """`review_status` is deliberately absent — see the comment beside
    `_REVIEW_STATUS_PROPERTY`: a filter is only offered to a tool that can
    report what the filter removed, and this tool's coverage block reports
    reach, not exclusion reasons."""
    schema = next(tool for tool in TOOLS if tool["name"] == "semantic_search")["input_schema"]
    properties = schema["properties"]
    for name in ("kind", "sender_contains", "date_from", "date_to", "projects", "matters", "tags"):
        assert name in properties, name
    assert "review_status" not in properties
    assert "top_k" in properties
    assert schema["required"] == ["query"]


async def test_search_reach_counts_matched_and_unembedded(session: AsyncSession) -> None:
    """One round trip, two counts. `unembedded` is what distinguishes
    'the archive is silent' from 'these documents were never indexed'."""
    for n in range(3):
        await seed(session, f"reach-with-{n}", kind_slug="invoice", chunks=(("t", vec(0)),))
    for n in range(2):
        await seed(session, f"reach-without-{n}", kind_slug="invoice")
    await seed(session, "reach-other", kind_slug="receipt", chunks=(("t", vec(0)),))

    reach = await search_reach(session, DocumentFilters(kind_slug="invoice"))
    assert (reach.matched, reach.unembedded) == (5, 2)


async def test_filters_narrow_the_search_and_are_reported(
    session: AsyncSession, stub_embedder: None
) -> None:
    for n in range(3):
        await seed(session, f"filter-invoice-{n}", kind_slug="invoice", chunks=(("alpha", vec(0)),))
    for n in range(2):
        await seed(session, f"filter-receipt-{n}", kind_slug="receipt", chunks=(("alpha", vec(0)),))

    filtered = await _run_semantic_search(
        session, get_settings(), {"query": "alpha", "kind": "invoice"}, set(), {}
    )
    assert len(filtered["results"]) == 3
    assert filtered["coverage"] == {"matched": 3, "returned": 3, "unembedded": 0}

    unfiltered = await _run_semantic_search(session, get_settings(), {"query": "alpha"}, set(), {})
    assert unfiltered["coverage"]["matched"] == 5


def test_top_k_is_clamped_into_range() -> None:
    """Every row here was measured against the real retriever before being
    written down. The negative rows are the ones that matter: without the
    floor, `semantic_search`'s `ranked[:top_k]` slices from the END."""
    settings = get_settings()
    assert _top_k_arg(None, settings) == settings.retrieve_top_k
    assert _top_k_arg(-3, settings) == 1
    assert _top_k_arg(-1, settings) == 1
    assert _top_k_arg(0, settings) == 1
    assert _top_k_arg(1, settings) == 1
    assert _top_k_arg(25, settings) == 25
    assert _top_k_arg(1000, settings) == settings.ask_search_max_top_k
    # The schema says integer, but a schema steers the model without binding it.
    assert _top_k_arg("ten", settings) == settings.retrieve_top_k
    assert _top_k_arg("7", settings) == 7


async def test_negative_top_k_does_not_leak_a_near_complete_set(
    session: AsyncSession, stub_embedder: None
) -> None:
    """Regression guard for the measured behaviour: before the clamp, this
    returned six of seven documents."""
    for n in range(7):
        await seed(session, f"depth-{n}", chunks=(("alpha", vec(0)),))

    result = await _run_semantic_search(
        session, get_settings(), {"query": "alpha", "top_k": -1}, set(), {}
    )
    assert len(result["results"]) == 1
    assert result["coverage"] == {"matched": 7, "returned": 1, "unembedded": 0}
