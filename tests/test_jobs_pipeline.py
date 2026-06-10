"""Tests for the process_document pipeline skeleton."""

import hashlib
from collections.abc import AsyncIterator

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
from library.jobs import advance_pipeline, job_app, process_document
from library.models import Document, DocumentSource, DocumentStatus, IngestionEvent

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def make_document(session_factory: async_sessionmaker[AsyncSession], marker: str) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.pdf",
        )
        session.add(document)
        await session.commit()
        return document.id


async def get_status_and_events(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> tuple[DocumentStatus, list[tuple[str, dict[str, object]]]]:
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        events = (
            (
                await session.execute(
                    select(IngestionEvent)
                    .where(IngestionEvent.document_id == document_id)
                    .order_by(IngestionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        return document.status, [(event.event, event.detail) for event in events]


async def test_pipeline_reaches_indexed_with_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document_id = await make_document(session_factory, "pipeline-happy")

    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    transitions = [event for event in events if event[0] == "status_changed"]
    assert [event[1] for event in transitions] == [
        {"from": "received", "to": "ocr"},
        {"from": "ocr", "to": "extract"},
        {"from": "extract", "to": "indexed"},
    ]


async def test_pipeline_is_idempotent_when_already_indexed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document_id = await make_document(session_factory, "pipeline-idempotent")
    await advance_pipeline(session_factory, document_id)
    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    # Re-running added no extra transition events.
    assert len([event for event in events if event[0] == "status_changed"]) == 3


async def test_pipeline_failure_marks_document_failed(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "pipeline-failure")

    def boom(document: Document) -> None:
        raise RuntimeError("ocr engine exploded")

    monkeypatch.setattr(jobs, "run_ocr", boom)

    with pytest.raises(RuntimeError, match="ocr engine exploded"):
        await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.FAILED
    failed = [event for event in events if event[0] == "failed"]
    assert len(failed) == 1
    assert failed[0][1]["error"] == "ocr engine exploded"
    assert failed[0][1]["status"] == "ocr"


async def test_pipeline_missing_document_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="999999999"):
        await advance_pipeline(session_factory, 999999999)


async def test_process_document_task_registered_and_deferrable() -> None:
    assert process_document.name == "library.jobs.process_document"
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        async with job_app.open_async():
            await process_document.defer_async(document_id=42)
        assert len(connector.jobs) == 1
        job = next(iter(connector.jobs.values()))
        assert job["task_name"] == "library.jobs.process_document"
        assert job["args"] == {"document_id": 42}
