"""Tests for the process_document pipeline (skeleton + OCR stage wiring)."""

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
from library.config import get_settings
from library.jobs import advance_pipeline, job_app, process_document
from library.models import Document, DocumentSource, DocumentStatus, IngestionEvent
from library.ocr import router as ocr_router
from library.ocr.base import OcrResult

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point LIBRARY_DATA_DIR at tmp_path so derived_dir writes stay local."""
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_router(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> OcrResult:
    """Replace the OCR router with one returning a fixed result."""
    searchable = data_dir / "searchable.pdf"
    searchable.write_bytes(b"%PDF-1.4 fake")
    result = OcrResult(
        text="OCR says hello",
        confidence=88.5,
        searchable_pdf=searchable,
        engine="tesseract",
        pages=2,
    )

    def fake_run_ocr(document: Document, original_path: Path, derived: Path) -> OcrResult:
        return result

    monkeypatch.setattr(ocr_router, "run_ocr", fake_run_ocr)
    return result


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
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "pipeline-happy")

    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    transitions = [event for event in events if event[0] == "status_changed"]
    assert [event[1] for event in transitions] == [
        {"from": "received", "to": "ocr"},
        {"from": "ocr", "to": "extract"},
        {"from": "extract", "to": "embed"},
        {"from": "embed", "to": "indexed"},
    ]


async def test_pipeline_is_idempotent_when_already_indexed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "pipeline-idempotent")
    await advance_pipeline(session_factory, document_id)
    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    # Re-running added no extra transition events.
    assert len([event for event in events if event[0] == "status_changed"]) == 4


async def test_ocr_stage_persists_results_and_event(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "pipeline-ocr-persist")

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.ocr_text == "OCR says hello"
        assert document.ocr_confidence == 88.5
        assert document.page_count == 2
        assert document.searchable_pdf is True

    _, events = await get_status_and_events(session_factory, document_id)
    completed = [event for event in events if event[0] == "ocr_completed"]
    assert len(completed) == 1
    assert completed[0][1] == {
        "engine": "tesseract",
        "confidence": 88.5,
        "pages": 2,
        "characters": len("OCR says hello"),
    }


async def test_pipeline_defers_thumbnail_job_after_ocr(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "pipeline-thumb-defer")

    await advance_pipeline(session_factory, document_id)

    thumbnail_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.generate_thumbnail"
    ]
    assert [job["args"] for job in thumbnail_jobs] == [{"document_id": document_id}]


async def test_ocr_stage_failure_records_ocr_failed_event(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "pipeline-ocr-failure")

    def explode(document: Document, original_path: Path, derived: Path) -> OcrResult:
        raise RuntimeError("tesseract binary missing")

    monkeypatch.setattr(ocr_router, "run_ocr", explode)

    with pytest.raises(RuntimeError, match="tesseract binary missing"):
        await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.FAILED
    ocr_failed = [event for event in events if event[0] == "ocr_failed"]
    assert len(ocr_failed) == 1
    assert ocr_failed[0][1] == {"error": "tesseract binary missing"}
    failed = [event for event in events if event[0] == "failed"]
    assert len(failed) == 1
    assert failed[0][1]["status"] == "ocr"


async def test_pipeline_failure_marks_document_failed(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await make_document(session_factory, "pipeline-failure")

    async def boom(session: AsyncSession, document: Document) -> None:
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


async def test_thumbnail_defer_failure_does_not_fail_document(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient queue error after successful OCR must not strand the
    document in ``failed`` — the thumbnail is a best-effort derivation."""

    async def explode(**kwargs: object) -> None:
        raise ConnectionError("transient queue blip")

    monkeypatch.setattr(jobs.generate_thumbnail, "defer_async", explode)
    document_id = await make_document(session_factory, "pipeline-thumb-defer-fail")

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.status is DocumentStatus.INDEXED
