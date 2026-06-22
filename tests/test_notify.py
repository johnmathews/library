"""The pipeline emits a Postgres NOTIFY on each document status transition.

These feed the SSE endpoint (``library.api.events``): a second connection
``LISTEN``s on the channel and asserts the payloads the worker emits.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import get_settings
from library.jobs import (
    EVENTS_CHANNEL,
    advance_pipeline,
    notify_document_event,
    procrastinate_conninfo,
)
from library.models import Document, DocumentSource
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
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def fake_router(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> OcrResult:
    """Replace the OCR router so the pipeline runs without real OCR binaries."""
    searchable = data_dir / "searchable.pdf"
    searchable.write_bytes(b"%PDF-1.4 fake")
    result = OcrResult(
        text="OCR says hello",
        confidence=88.5,
        searchable_pdf=searchable,
        engine="tesseract",
        pages=2,
    )
    monkeypatch.setattr(ocr_router, "run_ocr", lambda document, original_path, derived: result)
    return result


@pytest.fixture
async def listener(api_database_url: str) -> AsyncIterator[list[dict[str, object]]]:
    """A raw asyncpg connection LISTENing on the events channel.

    Collects every NOTIFY payload (parsed) into the yielded list.
    """
    received: list[dict[str, object]] = []
    conn = await asyncpg.connect(procrastinate_conninfo(api_database_url))
    await conn.add_listener(
        EVENTS_CHANNEL,
        lambda _conn, _pid, _channel, payload: received.append(json.loads(payload)),
    )
    try:
        yield received
    finally:
        await conn.close()


async def make_document(session_factory: async_sessionmaker[AsyncSession], marker: str) -> int:
    async with session_factory() as session:
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            original_filename=f"{marker}.pdf",
        )
        session.add(document)
        await session.commit()
        return document.id


async def _wait_for(received: list[object], count: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while len(received) < count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)


async def test_notify_helper_emits_payload(
    session_factory: async_sessionmaker[AsyncSession],
    listener: list[dict[str, object]],
) -> None:
    document_id = await make_document(session_factory, "notify-helper")

    await notify_document_event(
        session_factory, document_id, "status_changed", "ocr", title="Hello"
    )

    await _wait_for(listener, 1)
    assert listener == [
        {
            "document_id": document_id,
            "event": "status_changed",
            "status": "ocr",
            "title": "Hello",
        }
    ]


async def test_pipeline_emits_status_changed_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: object,
    listener: list[dict[str, object]],
) -> None:
    document_id = await make_document(session_factory, "notify-pipeline")

    await advance_pipeline(session_factory, document_id)

    await _wait_for(listener, 5)
    statuses = [m["status"] for m in listener if m["event"] == "status_changed"]
    assert statuses == ["ocr", "extract", "markdown", "embed", "indexed"]
    assert all(m["document_id"] == document_id for m in listener)
