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
        {"from": "extract", "to": "markdown"},
        {"from": "markdown", "to": "embed"},
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
    assert len([event for event in events if event[0] == "status_changed"]) == 5


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


async def test_pipeline_defers_series_insight_when_sender_and_kind_present(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    from library.models import Kind, Sender

    async with session_factory() as session:
        sender = Sender(name="SeriesInsight Energy")
        session.add(sender)
        kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
        await session.flush()
        document = Document(
            sha256=hashlib.sha256(b"series-insight-defer").hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            sender_id=sender.id,
            kind_id=kind.id,
        )
        session.add(document)
        await session.commit()
        document_id = document.id
        sender_id = sender.id
        kind_id = kind.id

    await advance_pipeline(session_factory, document_id)

    insight_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.generate_series_insight"
    ]
    assert [job["args"] for job in insight_jobs] == [{"sender_id": sender_id, "kind_id": kind_id}]


async def test_pipeline_skips_series_insight_without_sender_or_kind(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "series-insight-noskip")

    await advance_pipeline(session_factory, document_id)

    insight_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.generate_series_insight"
    ]
    assert insight_jobs == []


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


async def test_born_digital_markdown_reaches_indexed_with_page_and_chunks(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A text/markdown document flows through to indexed: OCR passthrough →
    one born-digital DocumentPage → markdown-aware chunks → embedded."""
    from library.models import EMBEDDING_DIM, DocumentChunk, DocumentPage

    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()

    body = "# Heading\n\n- one\n- two\n\nclosing paragraph"

    def fake_run_ocr(document: Document, original_path: Path, derived: Path) -> OcrResult:
        # Mirror the real router's text/markdown passthrough.
        return OcrResult(text=body, confidence=None, searchable_pdf=None, engine="text", pages=None)

    async def fake_embed_texts(
        texts: list[str], *, settings: object, client: object | None = None
    ) -> list[list[float]]:
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    monkeypatch.setattr(ocr_router, "run_ocr", fake_run_ocr)
    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)

    sha = hashlib.sha256(b"born-digital-md-pipeline").hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="text/markdown",
            source=DocumentSource.UPLOAD,
            original_filename="note.md",
        )
        session.add(document)
        await session.commit()
        document_id = document.id

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.status is DocumentStatus.INDEXED
        assert document.ocr_text == body

        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(pages) == 1
        assert pages[0].page_number == 1
        assert pages[0].markdown == body

        chunks = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(chunks) >= 1

    get_settings.cache_clear()


def test_next_status_includes_markdown() -> None:
    assert jobs._NEXT_STATUS[DocumentStatus.EXTRACT] == DocumentStatus.MARKDOWN
    assert jobs._NEXT_STATUS[DocumentStatus.MARKDOWN] == DocumentStatus.EMBED


async def test_pipeline_runs_markdown_stage_between_extract_and_embed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The markdown stage must run after extract and before embed."""
    calls: list[str] = []

    async def _stub_extraction(session: AsyncSession, document: Document) -> None:
        calls.append("extract")

    async def _stub_markdown(session: AsyncSession, document: Document) -> None:
        calls.append("markdown")

    async def _stub_embed(session: AsyncSession, document: Document) -> None:
        calls.append("embed")

    monkeypatch.setattr(jobs, "run_extraction", _stub_extraction)
    monkeypatch.setattr(jobs, "run_markdown", _stub_markdown)
    monkeypatch.setattr(jobs, "run_embed", _stub_embed)

    document_id = await make_document(session_factory, "pipeline-markdown-order")
    await advance_pipeline(session_factory, document_id)

    assert "markdown" in calls
    assert calls.index("markdown") > calls.index("extract")
    assert calls.index("markdown") < calls.index("embed")


# --- Per-user Pushover dispatch wiring (W3) ---


async def _make_owner(session_factory: async_sessionmaker[AsyncSession], *events: str) -> int:
    """Insert a user opted into ``events`` with valid Pushover credentials."""
    from library.models import User

    async with session_factory() as session:
        user = User(
            username=f"owner-{hashlib.sha256((events or ('x',))[0].encode()).hexdigest()[:10]}",
            password_hash="x",
            preferences={
                "notifications": {
                    "enabled": True,
                    "pushover_app_token": "app",
                    "pushover_user_key": "usr",
                    "events": list(events),
                }
            },
        )
        session.add(user)
        await session.commit()
        return user.id


async def _make_owned_document(
    session_factory: async_sessionmaker[AsyncSession], marker: str, uploader_id: int
) -> int:
    sha = hashlib.sha256(marker.encode()).hexdigest()
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.pdf",
            uploader_id=uploader_id,
        )
        session.add(document)
        await session.commit()
        return document.id


async def test_pipeline_pushes_success_to_owner(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[dict[str, object]] = []

    async def _capture(**kwargs: object):
        sends.append(kwargs)
        from library.notifications import PushoverResult

        return PushoverResult(ok=True, request_id="r")

    monkeypatch.setattr("library.notifications.send_pushover", _capture)

    owner_id = await _make_owner(session_factory, "document_success")
    document_id = await _make_owned_document(session_factory, "push-success", owner_id)

    await advance_pipeline(session_factory, document_id)

    # Exactly one push, to the owner, the success message — proves session.get
    # eager-loads the uploader relationship against the real DB.
    assert len(sends) == 1
    assert sends[0]["title"] == "Document processed"
    assert sends[0]["app_token"] == "app"
    assert sends[0]["user_key"] == "usr"


async def test_pipeline_failure_pushes_error_to_owner(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[dict[str, object]] = []

    async def _capture(**kwargs: object):
        sends.append(kwargs)
        from library.notifications import PushoverResult

        return PushoverResult(ok=True, request_id="r")

    monkeypatch.setattr("library.notifications.send_pushover", _capture)

    def explode(document: Document, original_path: Path, derived: Path) -> OcrResult:
        raise RuntimeError("tesseract binary missing")

    monkeypatch.setattr(ocr_router, "run_ocr", explode)

    owner_id = await _make_owner(session_factory, "processing_error")
    document_id = await _make_owned_document(session_factory, "push-error", owner_id)

    with pytest.raises(RuntimeError, match="tesseract binary missing"):
        await advance_pipeline(session_factory, document_id)

    assert len(sends) == 1
    assert sends[0]["title"] == "Processing failed"
    assert sends[0]["priority"] == 1


async def test_pipeline_no_push_when_owner_not_subscribed(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[dict[str, object]] = []

    async def _capture(**kwargs: object):
        sends.append(kwargs)
        from library.notifications import PushoverResult

        return PushoverResult(ok=True)

    monkeypatch.setattr("library.notifications.send_pushover", _capture)

    # Owner opted only into duplicate, so a successful completion sends nothing.
    owner_id = await _make_owner(session_factory, "duplicate")
    document_id = await _make_owned_document(session_factory, "push-none", owner_id)

    await advance_pipeline(session_factory, document_id)

    assert sends == []
