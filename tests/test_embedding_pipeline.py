"""Tests for the EMBED pipeline stage and the embed_document job."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library import jobs
from library.config import Settings, get_settings
from library.embedding import EmbeddingError
from library.jobs import advance_pipeline, embed_document, job_app, run_embed
from library.models import (
    EMBEDDING_DIM,
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
)
from library.ocr import router as ocr_router
from library.ocr.base import OcrResult

pytestmark = pytest.mark.integration


@pytest.fixture
def enable_embedding(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


async def events_for(
    session_factory: async_sessionmaker[AsyncSession], document_id: int, event: str
) -> list[dict[str, object]]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IngestionEvent.detail)
                .where(IngestionEvent.document_id == document_id, IngestionEvent.event == event)
                .order_by(IngestionEvent.id)
            )
        ).all()
        return [detail for (detail,) in rows]


async def test_run_embed_creates_chunks_and_event(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    long_text = " ".join(f"word{i}" for i in range(800))  # spans several chunks
    document_id = await make_document(session_factory, "embed-create", ocr_text=long_text)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(len(chunk.embedding) == EMBEDDING_DIM for chunk in chunks)
    embedded = await events_for(session_factory, document_id, "embedded")
    assert embedded == [{"chunks": len(chunks), "model": "bge-m3", "page_aware": False}]


async def test_run_embed_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    text = " ".join(f"word{i}" for i in range(800))
    document_id = await make_document(session_factory, "embed-idempotent", ocr_text=text)

    for _ in range(2):
        async with session_factory() as session:
            document = await session.get(Document, document_id)
            assert document is not None
            await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    # Re-embedding replaced, did not duplicate.
    assert [chunk.chunk_index for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert len(chunks) == len({chunk.chunk_index for chunk in chunks})


async def test_run_embed_failure_records_event_and_keeps_no_chunks(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(texts: list[str], *, settings: Settings, client: object | None = None) -> list:
        raise EmbeddingError("embedder unreachable")

    monkeypatch.setattr(jobs, "embed_texts", boom)
    document_id = await make_document(session_factory, "embed-fail", ocr_text="some real text here")

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)  # must not raise

    assert await chunks_for(session_factory, document_id) == []
    failed = await events_for(session_factory, document_id, "embedding_failed")
    assert len(failed) == 1
    assert failed[0]["error"] == "embedder unreachable"


async def test_run_embed_skips_textless_document_without_calling_sidecar(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def must_not_call(*args: object, **kwargs: object) -> list:
        raise AssertionError("embedder must not be called for textless documents")

    monkeypatch.setattr(jobs, "embed_texts", must_not_call)
    document_id = await make_document(session_factory, "embed-empty", ocr_text=None)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    skipped = await events_for(session_factory, document_id, "embedding_skipped")
    assert skipped == [{"reason": "no_text"}]


async def test_pipeline_embeds_on_the_way_to_indexed(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job_connector: InMemoryConnector,
) -> None:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)

    def fake_run_ocr(document: Document, original_path: Path, derived: Path) -> OcrResult:
        return OcrResult(
            text="a travel allowance of 0.21 per km applies",
            confidence=90.0,
            searchable_pdf=None,
            engine="text",
            pages=1,
        )

    monkeypatch.setattr(ocr_router, "run_ocr", fake_run_ocr)
    document_id = await make_document(
        session_factory, "embed-pipeline", ocr_text=None, status=DocumentStatus.RECEIVED
    )

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.status is DocumentStatus.INDEXED
    assert len(await chunks_for(session_factory, document_id)) >= 1
    assert len(await events_for(session_factory, document_id, "embedded")) == 1


async def test_embed_document_task_registered_and_deferrable() -> None:
    assert embed_document.name == "library.jobs.embed_document"
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        async with job_app.open_async():
            await embed_document.defer_async(document_id=7)
        assert len(connector.jobs) == 1
        job = next(iter(connector.jobs.values()))
        assert job["task_name"] == "library.jobs.embed_document"
        assert job["args"] == {"document_id": 7}


async def test_embed_tags_chunks_with_page_number(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    document_id = await make_document(session_factory, "embed-page-aware", ocr_text="fallback text")

    async with session_factory() as session:
        session.add_all(
            [
                DocumentPage(
                    document_id=document_id,
                    page_number=1,
                    markdown="alpha " * 200,
                    char_count=1200,
                ),
                DocumentPage(
                    document_id=document_id,
                    page_number=2,
                    markdown="beta " * 200,
                    char_count=1000,
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    assert chunks
    assert {c.page_number for c in chunks} <= {1, 2}
    assert [c.chunk_index for c in chunks] == list(range(1, len(chunks) + 1))

    embedded = await events_for(session_factory, document_id, "embedded")
    assert len(embedded) == 1
    assert embedded[0]["page_aware"] is True


async def test_embed_falls_back_to_ocr_text_when_no_pages(
    session_factory: async_sessionmaker[AsyncSession],
    enable_embedding: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "embed_texts", _fake_embed_texts)
    long_text = "word " * 500
    document_id = await make_document(session_factory, "embed-ocr-fallback", ocr_text=long_text)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await run_embed(session, document)

    chunks = await chunks_for(session_factory, document_id)
    assert chunks
    assert all(c.page_number is None for c in chunks)

    embedded = await events_for(session_factory, document_id, "embedded")
    assert len(embedded) == 1
    assert embedded[0]["page_aware"] is False
