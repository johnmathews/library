"""Contextual chunk headers: composition, storage, and the re-embed hook (#6)."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library import jobs
from library.config import get_settings
from library.documents_service import HEADER_FIELDS, header_fields_changed
from library.jobs import compose_context_header
from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource, Kind, Sender
from library.search import semantic_search
from tests.conftest import fetch_all
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def _embed_jobs(database_url: str, document_id: int) -> list[tuple[Any, ...]]:
    return fetch_all(
        database_url,
        "SELECT task_name FROM procrastinate_jobs "
        "WHERE task_name = 'library.jobs.embed_document' "
        "AND (args ->> 'document_id')::bigint = :id",
        id=document_id,
    )


# ---- the predicate ----


def test_header_fields_are_the_names_apply_document_update_returns() -> None:
    assert {"sender_id", "kind_id", "title", "document_date"} == HEADER_FIELDS


def test_header_fields_changed() -> None:
    assert header_fields_changed(["sender_id"]) is True
    assert header_fields_changed(["title"]) is True
    assert header_fields_changed(["document_date"]) is True
    assert header_fields_changed(["kind_id"]) is True
    assert header_fields_changed(["summary"]) is False
    assert header_fields_changed(["tags", "projects"]) is False
    assert header_fields_changed([]) is False
    assert header_fields_changed(["summary", "title"]) is True


# ---- the defer hook ----


def test_editing_a_header_field_defers_a_reembed(
    api_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "hdr-sender", sender_name="Old Name BV")
    assert _embed_jobs(api_database_url, doc_id) == []
    response = api_client.patch(f"/api/documents/{doc_id}", json={"sender": "New Name BV"})
    assert response.status_code == 200, response.text
    assert len(_embed_jobs(api_database_url, doc_id)) == 1


def test_editing_a_non_header_field_defers_nothing(
    api_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "hdr-summary")
    response = api_client.patch(f"/api/documents/{doc_id}", json={"summary": "changed"})
    assert response.status_code == 200, response.text
    assert _embed_jobs(api_database_url, doc_id) == []


def test_an_empty_patch_defers_nothing(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "hdr-noop")
    response = api_client.patch(f"/api/documents/{doc_id}", json={})
    assert response.status_code == 200, response.text
    assert _embed_jobs(api_database_url, doc_id) == []


def test_a_same_value_patch_still_defers_a_reembed(
    api_client: TestClient, api_database_url: str
) -> None:
    """Known-and-accepted, not a regression: ``PATCH`` with a header field set
    to its CURRENT value still counts as "changed" and still defers a
    re-embed. ``apply_document_update``'s semantics are deliberately not being
    tightened here to distinguish a same-value write from a real change — this
    test only documents the behaviour so a future reader does not mistake it
    for a bug."""
    doc_id = seed_document(api_database_url, "hdr-samevalue", sender_name="Same Name BV")
    assert _embed_jobs(api_database_url, doc_id) == []
    response = api_client.patch(f"/api/documents/{doc_id}", json={"sender": "Same Name BV"})
    assert response.status_code == 200, response.text
    assert len(_embed_jobs(api_database_url, doc_id)) == 1


# ---- composition + storage ----


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    e = create_async_engine(api_database_url)
    yield e
    await e.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s


async def test_compose_header_omits_missing_fields(session: AsyncSession) -> None:
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    sender = Sender(name="Northwind Energy (fixture)")
    session.add(sender)
    await session.flush()
    full = Document(
        sha256=hashlib.sha256(b"hdr-full").hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=date(2019, 3, 14),
        title="Jaarafrekening",
        ocr_text="x",
    )
    bare = Document(
        sha256=hashlib.sha256(b"hdr-bare").hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        ocr_text="x",
    )
    session.add_all([full, bare])
    await session.commit()
    assert compose_context_header(full) == (
        "Northwind Energy (fixture) · 2019-03-14 · utility-bill · Jaarafrekening"
    )
    assert compose_context_header(bare) == ""


async def test_header_is_stored_but_never_shown_to_ask(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header must reach the embedder and NOT the excerpt."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()
    seen: list[str] = []

    async def fake_embed_texts(texts, *, settings, client=None):
        seen.extend(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)

    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    sender = Sender(name="Northwind Energy (fixture)")
    session.add(sender)
    await session.flush()
    document = Document(
        sha256=hashlib.sha256(b"hdr-embed").hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=date(2024, 11, 4),
        title="Annual statement",
        ocr_text="Bedrag 0,00",
    )
    session.add(document)
    await session.commit()

    await jobs.run_embed(session, document)

    assert len(seen) == 1
    assert seen[0].startswith("Northwind Energy (fixture) · 2024-11-04 · utility-bill")
    assert seen[0].endswith("Bedrag 0,00")

    chunk = (
        await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id))
    ).scalar_one()
    assert chunk.text == "Bedrag 0,00", "the stored passage must stay raw"
    assert chunk.context_header is not None

    hits = await semantic_search(
        session, query="bedrag", query_embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1), top_k=5
    )
    assert hits[0].chunk_text == "Bedrag 0,00", "Ask must not see the header"


async def test_document_with_no_metadata_embeds_bare_text(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty header must not become a leading blank line in the vector."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()
    seen: list[str] = []

    async def fake_embed_texts(texts, *, settings, client=None):
        seen.extend(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)
    document = Document(
        sha256=hashlib.sha256(b"hdr-none").hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        ocr_text="just body text",
    )
    session.add(document)
    await session.commit()
    await jobs.run_embed(session, document)
    assert seen == ["just body text"]
    chunk = (
        await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id))
    ).scalar_one()
    assert chunk.context_header is None
