"""Tests for the consume folder watcher (library.consume + worker wiring).

Strategy: most cases drive ``ConsumeWatcher.process_path`` / ``sweep``
directly (deterministic, no filesystem-event timing), with fast
stability settings injected via the constructor. One end-to-end test
runs the real ``run()`` loop (startup sweep + live ``awatch`` events +
stop-event shutdown), and one exercises the worker integration with a
stubbed Procrastinate worker.
"""

import asyncio
import errno
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
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
from library.models import Document, DocumentSource, IngestionEvent, User

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
def consumed_dir(consume_root: Path) -> Path:
    """The default archive target: a sibling of the consume dir."""
    return consume_root.parent / "consumed"


@pytest.fixture
def failed_dir(consume_root: Path) -> Path:
    """The default rejects target: a sibling of the consume dir."""
    return consume_root.parent / "failed"


@pytest.fixture
def watcher(
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> ConsumeWatcher:
    """A watcher with fast stability settings for tests."""
    return ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consumed_dir,
        failed_dir=failed_dir,
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
    consumed = consume_root.parent / "consumed"  # sibling of the consume dir
    if not consumed.exists():
        return []
    return [path for path in consumed.rglob("*") if path.is_file()]


def test_consume_settings_defaults() -> None:
    settings = Settings()
    assert settings.consume_dir is None  # feature off by default
    assert settings.consumed_dir is None  # no consume dir -> no derived siblings
    assert settings.failed_dir is None
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
    # Archive dirs default to siblings of the consume dir.
    assert settings.consumed_dir == Path("/data/consumed")
    assert settings.failed_dir == Path("/data/failed")
    assert settings.consume_force_polling is True
    assert settings.consume_stability_s == 0.5
    assert settings.consume_on_success == "delete"


def test_consume_archive_dirs_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_CONSUME_DIR", "/data/consume")
    monkeypatch.setenv("LIBRARY_CONSUMED_DIR", "/archive/done")
    monkeypatch.setenv("LIBRARY_FAILED_DIR", "/archive/rejects")
    settings = Settings()
    assert settings.consumed_dir == Path("/archive/done")
    assert settings.failed_dir == Path("/archive/rejects")


async def test_drop_pdf_attributes_to_default_owner_when_configured(
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    """With import_default_owner set, a consumed doc is owned by that user, so the
    owner-as-recipient fallback can fire for otherwise-ownerless scans."""
    username = f"scanowner-{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        user = User(username=username, password_hash="x", display_name="")
        session.add(user)
        await session.commit()
        owner_id = user.id

    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consumed_dir,
        failed_dir=failed_dir,
        stability_s=0.05,
        poll_interval_s=0.05,
        stability_timeout_s=5.0,
        default_owner_username=username,
    )
    name = f"scan-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].uploader_id == owner_id


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
    # Archived under the sibling consumed/YYYY/MM/ and gone from the drop dir.
    assert not (consume_root / name).exists()
    archived = archived_files(consume_root)
    assert [path.name for path in archived] == [name]
    relative = archived[0].relative_to(consume_root.parent / "consumed")
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
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consumed_dir,
        failed_dir=failed_dir,
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
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    name = f"notes-{uuid.uuid4().hex[:8]}.docx"
    path = consume_root / name
    path.write_bytes(b"not ours")

    await watcher.sweep()

    assert path.exists()  # untouched: not moved to failed/, not ingested
    assert not failed_dir.exists()
    assert await documents_named(session_factory, name) == []


async def test_unsniffable_content_moved_to_failed(
    watcher: ConsumeWatcher,
    consume_root: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    name = f"bad-{uuid.uuid4().hex[:8]}.pdf"
    # No known magic bytes and not UTF-8 decodable -> unsupported MIME.
    (consume_root / name).write_bytes(b"\x00\xff\xfe\xfa garbage " + uuid.uuid4().bytes)

    await watcher.process_path(consume_root / name)

    assert not (consume_root / name).exists()
    assert (failed_dir / name).exists()  # parked in the sibling failed/
    assert await documents_named(session_factory, name) == []


async def test_oversize_file_moved_to_failed(
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consumed_dir,
        failed_dir=failed_dir,
        stability_s=0.05,
        poll_interval_s=0.05,
        max_bytes=16,
    )
    name = f"huge-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    assert (failed_dir / name).exists()
    assert await documents_named(session_factory, name) == []


async def test_temp_and_partial_patterns_ignored(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
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
    # Files already parked in the configured dirs must never be reprocessed.
    parked_consumed = consumed_dir / "2026" / "01" / "old.pdf"
    parked_failed = failed_dir / "bad.pdf"
    for path in (parked_consumed, parked_failed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_pdf())

    await watcher.sweep()

    for path in [*ignored, parked_consumed, parked_failed]:
        assert path.exists(), path
    for name in ["old.pdf", "bad.pdf"]:
        assert await documents_named(session_factory, name) == []


async def test_configured_dirs_inside_consume_root_are_skipped(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    """An override pointing back inside the consume dir must not re-ingest
    archived files (the ingest-archive-ingest loop regression)."""
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consume_root / "consumed",
        failed_dir=consume_root / "failed",
        stability_s=0.05,
        poll_interval_s=0.05,
        stability_timeout_s=5.0,
    )
    parked_consumed = consume_root / "consumed" / "2026" / "01" / "archived.pdf"
    parked_failed = consume_root / "failed" / "rejected.pdf"
    for path in (parked_consumed, parked_failed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_pdf())

    assert not watcher._is_candidate(parked_consumed)
    assert not watcher._is_candidate(parked_failed)

    await watcher.sweep()

    for path in (parked_consumed, parked_failed):
        assert path.exists(), path
    for name in ["archived.pdf", "rejected.pdf"]:
        assert await documents_named(session_factory, name) == []


async def test_move_falls_back_when_rename_crosses_filesystems(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When os.replace raises EXDEV (archive dir on another mount), the
    copy-then-rename fallback still delivers the file."""
    monkeypatch.setattr("library.consume.os.replace", _exdev_into(consumed_dir), raising=True)
    name = f"exdev-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    assert len(await documents_named(session_factory, name)) == 1
    assert not (consume_root / name).exists()
    assert [path.name for path in archived_files(consume_root)] == [name]


def _exdev_into(archive_dir: Path) -> Callable[[object, object], None]:
    """A fake ``os.replace`` with real EXDEV semantics for ``archive_dir``:
    renames INTO it from outside fail with EXDEV, renames within it (the
    fallback's tmp promotion) and everywhere else (the storage layer's own
    os.replace calls) succeed."""
    real_replace = os.replace

    def cross_device_replace(src: object, dst: object) -> None:
        src_path, dst_path = Path(str(src)), Path(str(dst))
        if dst_path.is_relative_to(archive_dir) and not src_path.is_relative_to(archive_dir):
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
        real_replace(str(src), str(dst))

    return cross_device_replace


async def test_exdev_copy_failure_leaves_no_partial_under_final_name(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed copy on the cross-device path must never leave a partial
    file under the final archive name, and must keep the source in the
    consume dir for a clean retry (``process_path`` logs and never raises)."""
    monkeypatch.setattr("library.consume.os.replace", _exdev_into(consumed_dir), raising=True)

    def crash_mid_copy(src: object, dst: object, **kwargs: object) -> None:
        Path(str(dst)).write_bytes(b"partial garbage")  # simulate a truncated copy
        raise OSError("simulated crash mid-copy")

    monkeypatch.setattr("library.consume.shutil.copy2", crash_mid_copy, raising=True)
    name = f"crash-{uuid.uuid4().hex[:8]}.pdf"
    (consume_root / name).write_bytes(make_pdf())

    await watcher.process_path(consume_root / name)

    assert (consume_root / name).exists()  # source intact -> retried later
    # Nothing (partial or otherwise) landed under the final archive name.
    assert archived_files(consume_root) == []


async def test_startup_migration_survives_symlink_in_legacy_tree(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-regular entries in a legacy tree are left in place with a warning
    instead of crashing the watcher on every startup."""
    legacy_file = consume_root / "consumed" / "2026" / "01" / "old.pdf"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(make_pdf())
    dangling = consume_root / "consumed" / "dangling.pdf"
    dangling.symlink_to(consume_root / "consumed" / "does-not-exist.pdf")

    with caplog.at_level(logging.WARNING, logger="library.consume"):
        await run_until_stopped(watcher)

    assert (consumed_dir / "2026" / "01" / "old.pdf").exists()  # files still migrate
    assert dangling.is_symlink()  # left in place, not ours to resolve
    assert any("non-regular entry" in r.getMessage() for r in caplog.records)

    await run_until_stopped(watcher)  # second startup must not crash either
    assert (consumed_dir / "2026" / "01" / "old.pdf").exists()


def test_from_settings_threads_archive_dirs(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(consume_dir=consume_root)
    watcher = ConsumeWatcher.from_settings(settings, session_factory)
    assert watcher._consumed_dir == consume_root.parent / "consumed"
    assert watcher._failed_dir == consume_root.parent / "failed"


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
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consumed_dir,
        failed_dir=failed_dir,
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


async def run_until_stopped(watcher: ConsumeWatcher) -> None:
    """Run the watcher's startup path (mkdir + migration + sweep) and stop."""
    stop_event = asyncio.Event()
    stop_event.set()  # awatch exits after startup once the event is set
    await asyncio.wait_for(watcher.run(stop_event), timeout=15)


async def test_startup_migrates_legacy_archive_trees(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy in-consume archive trees move to the configured dirs on startup."""
    legacy_consumed = consume_root / "consumed" / "2026" / "01" / "old.pdf"
    legacy_failed = consume_root / "failed" / "bad.pdf"
    for path in (legacy_consumed, legacy_failed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_pdf())

    await run_until_stopped(watcher)

    assert (consumed_dir / "2026" / "01" / "old.pdf").exists()
    assert (failed_dir / "bad.pdf").exists()
    assert not (consume_root / "consumed").exists()  # emptied tree removed
    assert not (consume_root / "failed").exists()
    # Migrated (pre-migration in-root) files were archived, not re-ingested.
    for name in ["old.pdf", "bad.pdf"]:
        assert await documents_named(session_factory, name) == []

    # A second startup finds nothing to migrate and logs no migration line.
    with caplog.at_level(logging.INFO, logger="library.consume"):
        await run_until_stopped(watcher)
    assert not [r for r in caplog.records if "migrated" in r.getMessage()]


async def test_startup_migration_suffixes_collisions(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    job_connector: InMemoryConnector,
) -> None:
    existing_content = make_pdf("already-at-target")
    legacy_content = make_pdf("legacy-tree")
    existing = consumed_dir / "2026" / "01" / "dup.pdf"
    legacy = consume_root / "consumed" / "2026" / "01" / "dup.pdf"
    for path, content in ((existing, existing_content), (legacy, legacy_content)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    await run_until_stopped(watcher)

    target_dir = consumed_dir / "2026" / "01"
    assert sorted(path.name for path in target_dir.iterdir()) == ["dup-1.pdf", "dup.pdf"]
    assert {path.read_bytes() for path in target_dir.iterdir()} == {
        existing_content,
        legacy_content,
    }
    assert not (consume_root / "consumed").exists()


async def test_startup_migration_noop_when_configured_dirs_are_legacy_paths(
    consume_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    """Overrides pointing at the legacy in-consume locations migrate nothing."""
    watcher = ConsumeWatcher(
        consume_root,
        session_factory,
        consumed_dir=consume_root / "consumed",
        failed_dir=consume_root / "failed",
        stability_s=0.05,
        poll_interval_s=0.05,
        stability_timeout_s=5.0,
    )
    parked_consumed = consume_root / "consumed" / "2026" / "01" / "keep.pdf"
    parked_failed = consume_root / "failed" / "keep-bad.pdf"
    for path in (parked_consumed, parked_failed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_pdf())

    await run_until_stopped(watcher)

    for path in (parked_consumed, parked_failed):
        assert path.exists(), path  # untouched in place
    for name in ["keep.pdf", "keep-bad.pdf"]:
        assert await documents_named(session_factory, name) == []


async def test_startup_migration_noop_without_legacy_dirs(
    watcher: ConsumeWatcher,
    consume_root: Path,
    consumed_dir: Path,
    failed_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="library.consume"):
        await run_until_stopped(watcher)

    assert consumed_dir.is_dir() and failed_dir.is_dir()  # created at startup
    assert not (consume_root / "consumed").exists()  # nothing invented in-root
    assert not (consume_root / "failed").exists()
    assert not [r for r in caplog.records if "migrated" in r.getMessage()]


class _StubJobApp:
    """Stands in for the Procrastinate app inside library.worker."""

    def __init__(self, work_for_s: float) -> None:
        self._work_for_s = work_for_s
        self.worker_ran = False
        self.concurrency: int | None = None

    @asynccontextmanager
    async def open_async(self) -> AsyncIterator["_StubJobApp"]:
        yield self

    async def run_worker_async(
        self, concurrency: int = 1, stalled_worker_timeout: float = 30.0
    ) -> None:
        self.worker_ran = True
        self.concurrency = concurrency
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

    settings = Settings(consume_dir=consume_root, worker_concurrency=2)
    await asyncio.wait_for(worker.run_worker_with_consume(settings), timeout=15)

    assert stub.worker_ran
    assert stub.concurrency == 2  # worker_concurrency is threaded through
    assert len(await documents_named(session_factory, name)) == 1
    assert any(path.name == name for path in archived_files(consume_root))
