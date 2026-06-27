"""Tests for the consume folder watcher (library.consume + worker wiring).

Strategy: most cases drive ``ConsumeWatcher.process_path`` / ``sweep``
directly (deterministic, no filesystem-event timing), with fast
stability settings injected via the constructor. One end-to-end test
runs the real ``run()`` loop (startup sweep + live ``awatch`` events +
stop-event shutdown), and one exercises the worker integration with a
stubbed Procrastinate worker.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
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

from library import worker
from library.config import Settings, get_settings
from library.consume import ConsumeWatcher
from library.models import Document, DocumentSource, IngestionEvent

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
    """Point LIBRARY_DATA_DIR at tmp_path so stored originals stay local."""
    target = tmp_path / "data"
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(target))
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


@pytest.fixture
def consume_root(tmp_path: Path) -> Path:
    root = tmp_path / "consume"
    root.mkdir()
    return root


@pytest.fixture
def watcher(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> ConsumeWatcher:
    """A watcher with fast stability settings for tests."""
    return ConsumeWatcher(
        consume_root,
        session_factory,
        stability_s=0.05,
        poll_interval_s=0.05,
        stability_timeout_s=5.0,
    )


def make_pdf(marker: str | None = None) -> bytes:
    """Unique, sniffable-as-PDF content (api_database_url is shared)."""
    return b"%PDF-1.4\n% " + (marker or uuid.uuid4().hex).encode() + b"\n%%EOF\n"


async def documents_named(
    session_factory: async_sessionmaker[AsyncSession], filename: str
) -> list[Document]:
    async with session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.original_filename == filename)
        )
        return list(result.scalars().all())


def archived_files(consume_root: Path) -> list[Path]:
    consumed = consume_root / "consumed"
    if not consumed.exists():
        return []
    return [path for path in consumed.rglob("*") if path.is_file()]


def test_consume_settings_defaults() -> None:
    settings = Settings()
    assert settings.consume_dir is None  # feature off by default
    assert settings.consume_force_polling is False
    assert settings.consume_poll_interval_s == 2.0
    assert settings.consume_stability_s == 3.0
    assert settings.consume_on_success == "archive"


def test_consume_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_CONSUME_DIR", "/data/consume")
    monkeypatch.setenv("LIBRARY_CONSUME_FORCE_POLLING", "true")
    monkeypatch.setenv("LIBRARY_CONSUME_STABILITY_S", "0.5")
    monkeypatch.setenv("LIBRARY_CONSUME_ON_SUCCESS", "delete")
    settings = Settings()
    assert settings.consume_dir == Path("/data/consume")
    assert settings.consume_force_polling is True
    assert settings.consume_stability_s == 0.5
    assert settings.consume_on_success == "delete"


async def test_drop_pdf_creates_document_and_archives(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    name = f"scan-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    document = documents[0]
    assert document.source is DocumentSource.CONSUME
    assert document.uploader_id is None
    # Archived under consumed/YYYY/MM/ and gone from the drop dir.
    assert not (consume_root / name).exists()
    archived = archived_files(consume_root)
    assert [path.name for path in archived] == [name]
    relative = archived[0].relative_to(consume_root / "consumed")
    year, month = relative.parts[0], relative.parts[1]
    assert len(year) == 4 and year.isdigit()
    assert len(month) == 2 and month.isdigit()
    # Processing was enqueued through the normal pipeline.
    process_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.process_document"
        and job["args"] == {"document_id": document.id}
    ]
    assert len(process_jobs) == 1


async def test_markdown_file_is_candidate_and_ingests_as_markdown(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    name = f"note-{uuid.uuid4().hex[:8]}.md"
    path = consume_root / name
    path.write_bytes(b"# Heading " + uuid.uuid4().hex.encode() + b"\n\nbody text")

    assert watcher._is_candidate(path)

    await watcher.process_path(path)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].mime_type == "text/markdown"
    assert documents[0].source is DocumentSource.CONSUME
    assert not path.exists()  # archived on success


async def test_duplicate_drop_archives_without_second_document(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    content = make_pdf()
    first = consume_root / f"first-{uuid.uuid4().hex[:8]}.pdf"
    second = consume_root / f"second-{uuid.uuid4().hex[:8]}.pdf"
    first.write_bytes(content)
    second.write_bytes(content)

    await watcher.process_path(first)
    await watcher.process_path(second)

    documents = await documents_named(session_factory, first.name)
    assert len(documents) == 1
    assert await documents_named(session_factory, second.name) == []
    # The duplicate still counts as consumed: both files archived.
    assert sorted(path.name for path in archived_files(consume_root)) == sorted(
        [first.name, second.name]
    )
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(IngestionEvent).where(
                        IngestionEvent.document_id == documents[0].id,
                        IngestionEvent.event == "duplicate_upload",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].detail["filename"] == second.name
    assert events[0].detail["source"] == "consume"


async def test_growing_file_not_ingested_until_stable(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        stability_s=0.05,
        poll_interval_s=0.05,
        stability_timeout_s=0.25,  # give up quickly while the file grows
    )
    name = f"growing-{uuid.uuid4().hex[:8]}.pdf"
    path = consume_root / name
    path.write_bytes(b"%PDF-1.4\n% ")  # sniffable header; body arrives in chunks
    filler = uuid.uuid4().hex.encode() * 4  # 128 bytes, all chunks non-empty

    async def writer() -> None:
        # Append faster than the stability interval for longer than the
        # stability timeout: the watcher must give up, not ingest.
        for chunk in range(25):
            with path.open("ab") as handle:
                handle.write(filler[chunk * 4 : (chunk + 1) * 4])
            await asyncio.sleep(0.02)

    writer_task = asyncio.create_task(writer())
    await asyncio.sleep(0.05)  # let growth start
    await watcher.process_path(path)

    # Still growing when the watcher gave up: nothing ingested, file kept.
    assert await documents_named(session_factory, name) == []
    assert path.exists()

    await writer_task
    await watcher.process_path(path)  # the retry (next event / sweep)

    assert len(await documents_named(session_factory, name)) == 1
    assert not path.exists()


async def test_unsupported_extension_ignored_in_place(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    name = f"notes-{uuid.uuid4().hex[:8]}.docx"
    path = consume_root / name
    path.write_bytes(b"not ours")

    await watcher.sweep()

    assert path.exists()  # untouched: not moved to failed/, not ingested
    assert not (consume_root / "failed").exists()
    assert await documents_named(session_factory, name) == []


async def test_unsniffable_content_moved_to_failed(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    name = f"bad-{uuid.uuid4().hex[:8]}.pdf"
    # No known magic bytes and not UTF-8 decodable -> unsupported MIME.
    (consume_root / name).write_bytes(b"\x00\xff\xfe\xfa garbage " + uuid.uuid4().bytes)

    await watcher.process_path(consume_root / name)

    assert not (consume_root / name).exists()
    assert (consume_root / "failed" / name).exists()
    assert await documents_named(session_factory, name) == []


async def test_oversize_file_moved_to_failed(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        stability_s=0.05,
        poll_interval_s=0.05,
        max_bytes=16,
    )
    name = f"huge-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    assert (consume_root / "failed" / name).exists()
    assert await documents_named(session_factory, name) == []


async def test_temp_and_partial_patterns_ignored(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    ignored = [
        consume_root / ".syncthing.scan.pdf.tmp",
        consume_root / "~syncthing~scan.pdf.tmp",
        consume_root / "download.pdf.part",
        consume_root / ".hidden.pdf",
    ]
    for path in ignored:
        path.write_bytes(make_pdf())
    # Files already in consumed/ and failed/ must never be reprocessed.
    parked_consumed = consume_root / "consumed" / "2026" / "01" / "old.pdf"
    parked_failed = consume_root / "failed" / "bad.pdf"
    for path in (parked_consumed, parked_failed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_pdf())

    await watcher.sweep()

    for path in [*ignored, parked_consumed, parked_failed]:
        assert path.exists(), path
    for name in ["old.pdf", "bad.pdf"]:
        assert await documents_named(session_factory, name) == []


async def test_in_flight_dedup_single_ingest(
    watcher: ConsumeWatcher,
    consume_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    async def slow_process(path: Path) -> None:
        calls.append(path)
        await asyncio.sleep(0.1)

    monkeypatch.setattr(watcher, "_process_one", slow_process)
    path = consume_root / "twice.pdf"
    path.write_bytes(make_pdf())

    # Two events for the same path while the first is still in flight.
    await asyncio.gather(watcher.process_path(path), watcher.process_path(path))

    assert calls == [path]


async def test_run_sweeps_then_watches_then_stops(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    preexisting = f"preexisting-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / preexisting).write_bytes(make_pdf())

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(watcher.run(stop_event))

    async def archived(name: str) -> bool:
        for _ in range(200):  # up to ~10 s (awatch debounce is 1.6 s)
            if any(path.name == name for path in archived_files(consume_root)):
                return True
            await asyncio.sleep(0.05)
        return False

    # Startup sweep handles the file that was there before the watcher.
    assert await archived(preexisting)
    assert len(await documents_named(session_factory, preexisting)) == 1

    # A file dropped while watching is picked up via awatch events.
    dropped = f"dropped-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / dropped).write_bytes(make_pdf())
    assert await archived(dropped)
    assert len(await documents_named(session_factory, dropped)) == 1

    stop_event.set()
    await asyncio.wait_for(run_task, timeout=10)


async def test_delete_on_success_unlinks_instead_of_archiving(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        stability_s=0.05,
        poll_interval_s=0.05,
        on_success="delete",
    )
    name = f"deleted-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    assert len(await documents_named(session_factory, name)) == 1
    assert not (consume_root / name).exists()
    assert archived_files(consume_root) == []


class _StubJobApp:
    """Stands in for the Procrastinate app inside library.worker."""

    def __init__(self, work_for_s: float) -> None:
        self._work_for_s = work_for_s
        self.worker_ran = False

    @asynccontextmanager
    async def open_async(self) -> AsyncIterator["_StubJobApp"]:
        yield self

    async def run_worker_async(self) -> None:
        self.worker_ran = True
        await asyncio.sleep(self._work_for_s)


async def test_worker_runs_watcher_alongside_and_shuts_down_cleanly(
    watcher: ConsumeWatcher,
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_worker_with_consume ingests via the watcher and stops with the worker."""
    stub = _StubJobApp(work_for_s=1.5)
    monkeypatch.setattr(worker, "job_app", stub)
    monkeypatch.setattr(worker.ConsumeWatcher, "from_settings", lambda *_: watcher)
    name = f"worker-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    settings = Settings(consume_dir=consume_root)
    await asyncio.wait_for(worker.run_worker_with_consume(settings), timeout=15)

    assert stub.worker_ran
    assert len(await documents_named(session_factory, name)) == 1
    assert any(path.name == name for path in archived_files(consume_root))
