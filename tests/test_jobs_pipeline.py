"""Tests for the process_document pipeline (skeleton + OCR stage wiring)."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anthropic
import httpx
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library import jobs
from library.config import get_settings
from library.embedding import EmbeddingError
from library.extraction import apply as extraction_apply
from library.extraction.extractor import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from library.extraction.extractor import CallUsage, ExtractionOutcome
from library.extraction.schema import ExtractedMetadata
from library.jobs import (
    advance_pipeline,
    job_app,
    process_document,
    purge_deleted_documents,
    sweep_stalled_jobs,
)
from library.markdown import apply as markdown_apply
from library.markdown.generator import PROMPT_VERSION as MARKDOWN_PROMPT_VERSION
from library.markdown.generator import GeneratedPage, MarkdownResult
from library.matter_classifier import PROMPT_VERSION as MATTER_CLASSIFIER_PROMPT_VERSION
from library.models import (
    Document,
    DocumentPage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    ReviewStatus,
)
from library.ocr import router as ocr_router
from library.ocr.base import OcrResult
from library.ocr.router import UnsupportedOcrInputError
from library.storage import path_for, store

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


# Every task in library.jobs, mapped to whether it retries transient failures.
# This table IS the record of the judgement call — the reason column is why, not
# decoration. A task added without an entry fails
# test_retry_policy_per_task_is_complete, so the decision cannot be skipped.
RETRY_POLICY: dict[str, tuple[bool, str]] = {
    # Retry: each depends on a network service (Anthropic, the embedder, the DB)
    # and each is idempotent, so a re-run after a blip is safe and cheap.
    "library.jobs.process_document": (True, "whole pipeline; every stage hook is idempotent"),
    "library.jobs.extract_document": (True, "Anthropic call; already_extracted guards re-spend"),
    "library.jobs.markdown_document": (True, "Anthropic vision call; re-runnable"),
    "library.jobs.classify_document_matters": (True, "Anthropic call; prompt-version guarded"),
    "library.jobs.embed_document": (True, "embedder HTTP; deletes and re-inserts chunks"),
    "library.jobs.ingest_held_email": (True, "human-triggered override; losing it is visible"),
    # No retry, deliberately.
    "library.jobs.generate_thumbnail": (
        False,
        "deterministic render of one file: a failure means a bad file, not bad "
        "luck, so retrying burns attempts on the same outcome. Best-effort by "
        "design — the defer failure is already recorded as an event.",
    ),
    "library.jobs.sweep_stalled_jobs": (False, "periodic: the next tick is the retry"),
    "library.jobs.backfill_budget_skipped": (False, "periodic: the next tick is the retry"),
    "library.jobs.purge_deleted_documents": (False, "periodic: the next tick is the retry"),
    "library.jobs.poll_email_inbox": (
        False,
        "periodic: the next tick is the retry. Also the one task carrying both "
        "queueing_lock and lock, so a retry would be a second recovery "
        "mechanism racing the schedule for no gain.",
    ),
}


def test_retry_policy_per_task_is_complete() -> None:
    """Every registered task must carry an explicit retry decision.

    Not a coverage formality: Procrastinate silently defaults to no retry, so an
    omitted decision is indistinguishable from a considered "no" and a new
    network-dependent task would quietly lose its work to any blip.
    """
    # Procrastinate registers its own builtins (remove_old_jobs, plus a
    # "builtin:"-prefixed alias); their retry policy is upstream's business.
    # Matching on our own namespace still catches any new library task.
    registered = {name for name in job_app.tasks if name.startswith("library.jobs.")}
    assert registered == set(RETRY_POLICY), (
        "tasks missing a retry decision: "
        f"{sorted(registered - set(RETRY_POLICY))}; "
        f"stale entries: {sorted(set(RETRY_POLICY) - registered)}"
    )


@pytest.mark.parametrize("task_name", sorted(RETRY_POLICY))
def test_retry_policy_per_task(task_name: str) -> None:
    """Each task's configured retry matches its recorded decision."""
    should_retry, reason = RETRY_POLICY[task_name]
    strategy = job_app.tasks[task_name].retry_strategy

    if not should_retry:
        assert strategy is None, f"{task_name} should not retry ({reason})"
        return

    assert strategy is not None, f"{task_name} should retry ({reason})"
    assert strategy is jobs.TRANSIENT_RETRY, "retrying tasks share one policy"
    assert strategy.max_attempts == 5
    assert strategy.exponential_wait == 4  # backoff, not a tight loop


def test_transient_allowlist_excludes_deterministic_failures() -> None:
    """The allowlist must never admit an error that retrying cannot fix.

    The direction matters: a denylist would retry anything unanticipated, so a
    plain bug would burn five attempts and reach `failed` a minute later with
    the same message. These are the classes that must fail on attempt one.
    """
    retried = jobs.TRANSIENT_EXCEPTIONS

    for deterministic in (
        ValueError,
        TypeError,
        KeyError,
        UnsupportedOcrInputError,
        IntegrityError,  # a constraint violation is not bad luck
        httpx.HTTPStatusError,  # a 4xx response; NOT a TransportError subclass
        anthropic.BadRequestError,  # 400: malformed request
        anthropic.AuthenticationError,  # 401: the key is wrong, not flaky
    ):
        assert not issubclass(deterministic, retried), (
            f"{deterministic.__name__} is deterministic and must not be retried"
        )

    # And the transient ones genuinely are covered.
    for transient in (
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        httpx.ConnectError,
        httpx.ReadTimeout,
        EmbeddingError,
        OperationalError,
    ):
        assert issubclass(transient, retried), f"{transient.__name__} should be retried"


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


@pytest.fixture
def textless_router(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> OcrResult:
    """An OCR router that finds no text at all (a blank or unreadable scan)."""
    result = OcrResult(
        text="",
        confidence=None,
        searchable_pdf=None,
        engine="tesseract",
        pages=1,
    )
    monkeypatch.setattr(ocr_router, "run_ocr", lambda *a, **k: result)
    return result


async def test_textless_document_reaches_indexed_but_is_flagged_for_review(
    session_factory: async_sessionmaker[AsyncSession],
    textless_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    """No text must not mean silent success.

    This is the coverage hole the flag closes: extraction is disabled in this
    suite, so it returns before ``_apply_validation`` ever runs — exactly as it
    does in production for a textless document, which takes the
    ``ExtractionSkipped("input_unusable")`` branch. Relying on extraction's
    validation would leave the one document that most needs flagging unflagged.
    """
    document_id = await make_document(session_factory, "pipeline-textless")

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # The invariant holds: no text does not stop a document being indexed.
        assert document.status == DocumentStatus.INDEXED
        assert document.ocr_text is None
        # But it is now in the review queue with a named rule.
        assert document.review_status is ReviewStatus.NEEDS_REVIEW
        rules = [finding["rule"] for finding in document.extra["validation"]["findings"]]
        assert rules == ["no_text_extracted"]

    _, events = await get_status_and_events(session_factory, document_id)
    empty = [detail for name, detail in events if name == "ocr_empty"]
    assert len(empty) == 1
    assert empty[0] == {"engine": "tesseract", "pages": 1}


async def test_textless_flag_does_not_clobber_existing_validation_on_resume(
    session_factory: async_sessionmaker[AsyncSession],
    textless_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    """A resume re-enters the OCR hook; richer findings must survive it.

    The OCR-stage flag is a floor, not the authority. A document whose
    extraction already ran full validation carries a complete finding set, and
    re-running OCR must not replace it with this single finding.
    """
    document_id = await make_document(session_factory, "pipeline-textless-resume")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        document.extra = {
            **document.extra,
            "validation": {
                "prompt_version": "pinned",
                "findings": [
                    {"rule": "empty_extraction", "field": None, "severity": "warn", "message": "x"}
                ],
                "validated_at": "2026-07-01T00:00:00+00:00",
            },
        }
        await session.commit()

    await advance_pipeline(session_factory, document_id)

    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        rules = [finding["rule"] for finding in document.extra["validation"]["findings"]]
        assert rules == ["empty_extraction"]  # preserved, not overwritten

    _, events = await get_status_and_events(session_factory, document_id)
    assert [name for name, _ in events if name == "ocr_empty"] == []


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


async def test_pipeline_defers_matter_classification_after_extract(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    document_id = await make_document(session_factory, "pipeline-matter-defer")

    await advance_pipeline(session_factory, document_id)

    matter_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.classify_document_matters"
    ]
    # ``skip_if_classified`` rides along so a pipeline resume that re-defers
    # this job cannot pay for a second classification of the same document.
    assert [job["args"] for job in matter_jobs] == [
        {"document_id": document_id, "skip_if_classified": True}
    ]


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


def test_sweep_cron_built_from_minutes() -> None:
    assert jobs.sweep_cron(5) == "*/5 * * * *"
    # Clamped to the 1-59 minute-step range like the email poller.
    assert jobs.sweep_cron(0) == "*/1 * * * *"
    assert jobs.sweep_cron(90) == "*/59 * * * *"


def test_sweep_stalled_jobs_registered_with_default_cron() -> None:
    assert sweep_stalled_jobs.name == "library.jobs.sweep_stalled_jobs"
    periodic = job_app.periodic_registry.periodic_tasks[("library.jobs.sweep_stalled_jobs", "")]
    assert periodic.cron == "*/5 * * * *"


async def test_sweep_reenqueues_stalled_process_document_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stalled ``process_document`` job is retried, filtered by heartbeat."""
    get_settings.cache_clear()
    sentinels = [object(), object(), object()]
    captured: dict[str, object] = {}
    retried: list[object] = []

    async def fake_get_stalled(**kwargs: object) -> list[object]:
        captured.update(kwargs)
        return sentinels

    async def fake_retry(job: object, **kwargs: object) -> None:
        retried.append(job)

    monkeypatch.setattr(job_app.job_manager, "get_stalled_jobs", fake_get_stalled)
    monkeypatch.setattr(job_app.job_manager, "retry_job", fake_retry)

    await sweep_stalled_jobs(timestamp=0)

    assert retried == sentinels
    assert captured["task_name"] == "library.jobs.process_document"
    assert captured["seconds_since_heartbeat"] == 60.0


async def test_sweep_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``stalled_job_sweep_minutes=0`` skips the query entirely (no work, no spend)."""
    monkeypatch.setenv("LIBRARY_STALLED_JOB_SWEEP_MINUTES", "0")
    get_settings.cache_clear()
    called = False

    async def fake_get_stalled(**kwargs: object) -> list[object]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(job_app.job_manager, "get_stalled_jobs", fake_get_stalled)
    try:
        await sweep_stalled_jobs(timestamp=0)
    finally:
        get_settings.cache_clear()

    assert called is False


# --- Recently-Deleted purge --------------------------------------------------


@pytest.fixture
def purge_worker(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire the purge task to the test database and tmp data_dir.

    ``purge_deleted_documents`` reaches for the module-level
    ``get_sessionmaker()`` (a settings-derived, process-wide engine) rather than
    an injected factory. Redirect that name to the test-bound ``session_factory``
    so the task hits the testcontainer DB, and pull in ``data_dir`` so stored
    originals land under tmp_path.
    """
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: session_factory)


async def _seed_deleted_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    deleted_at: datetime | None,
    source: DocumentSource = DocumentSource.UPLOAD,
    store_file: bool = True,
) -> tuple[int, str]:
    """Insert a document (soft-deleted when ``deleted_at`` is set) and, unless
    ``store_file`` is False, store its original file. Returns (id, sha256)."""
    content = marker.encode()
    sha = hashlib.sha256(content).hexdigest()
    if store_file:
        store(content)  # writes under get_settings().data_dir (tmp via data_dir fixture)
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type="application/pdf",
            source=source,
            original_filename=f"{marker}.pdf",
            deleted_at=deleted_at,
        )
        session.add(document)
        await session.commit()
        return document.id, sha


def test_purge_deleted_documents_registered_with_daily_cron() -> None:
    assert purge_deleted_documents.name == "library.jobs.purge_deleted_documents"
    periodic = job_app.periodic_registry.periodic_tasks[
        ("library.jobs.purge_deleted_documents", "")
    ]
    assert periodic.cron == "41 3 * * *"


async def test_purge_hard_deletes_expired_and_keeps_recent(
    session_factory: async_sessionmaker[AsyncSession], purge_worker: None
) -> None:
    """Documents deleted past the retention window are removed (row + file);
    recently-deleted and live documents survive."""
    now = datetime.now(UTC)
    expired_id, expired_sha = await _seed_deleted_document(
        session_factory, "purge-expired", deleted_at=now - timedelta(days=40)
    )
    recent_id, recent_sha = await _seed_deleted_document(
        session_factory, "purge-recent", deleted_at=now - timedelta(days=5)
    )
    live_id, live_sha = await _seed_deleted_document(session_factory, "purge-live", deleted_at=None)
    assert path_for(expired_sha).is_file()

    await purge_deleted_documents(timestamp=0)

    async with session_factory() as session:
        assert await session.get(Document, expired_id) is None  # hard-deleted
        assert await session.get(Document, recent_id) is not None  # inside window
        assert await session.get(Document, live_id) is not None  # not deleted
    assert not path_for(expired_sha).exists()  # original unlinked
    assert path_for(recent_sha).is_file()  # survivor's file untouched
    assert path_for(live_sha).is_file()


async def test_purge_is_noop_when_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    purge_worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``deleted_purge_enabled`` kill switch stops all deletion."""
    monkeypatch.setenv("LIBRARY_DELETED_PURGE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        expired_id, expired_sha = await _seed_deleted_document(
            session_factory,
            "purge-disabled",
            deleted_at=datetime.now(UTC) - timedelta(days=99),
        )
        await purge_deleted_documents(timestamp=0)
        async with session_factory() as session:
            assert await session.get(Document, expired_id) is not None
        assert path_for(expired_sha).is_file()
    finally:
        get_settings.cache_clear()


async def test_purge_tolerates_missing_file(
    session_factory: async_sessionmaker[AsyncSession], purge_worker: None
) -> None:
    """A document whose original was never materialised still purges cleanly."""
    expired_id, _sha = await _seed_deleted_document(
        session_factory,
        "purge-nofile",
        deleted_at=datetime.now(UTC) - timedelta(days=40),
        store_file=False,
    )
    await purge_deleted_documents(timestamp=0)  # must not raise on the missing file
    async with session_factory() as session:
        assert await session.get(Document, expired_id) is None


async def test_purge_removes_expired_note(
    session_factory: async_sessionmaker[AsyncSession], purge_worker: None
) -> None:
    """Notes are documents (source=NOTE) and purge by the same retention window."""
    note_id, _sha = await _seed_deleted_document(
        session_factory,
        "purge-note",
        deleted_at=datetime.now(UTC) - timedelta(days=40),
        source=DocumentSource.NOTE,
    )
    await purge_deleted_documents(timestamp=0)
    async with session_factory() as session:
        assert await session.get(Document, note_id) is None


async def test_pipeline_resumes_from_midpipeline_status(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
) -> None:
    """A document stranded mid-pipeline by a crash resumes to ``indexed`` *and
    re-runs the stage it was killed in*.

    This is the property the stalled-job sweeper relies on. ``status`` names the
    stage that was **entered**, not one that finished, so a worker killed inside
    the OCR hook leaves ``status=ocr`` with ``ocr_text`` still NULL. Reaching
    ``indexed`` is not enough — the evidence of the interrupted stage must come
    back, or the document is silently unsearchable forever.
    """
    document_id = await make_document(session_factory, "pipeline-resume")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # Simulate a hard kill *inside* the OCR hook: status advanced, work undone.
        document.status = DocumentStatus.OCR
        document.ocr_text = None
        document.page_count = None
        await session.commit()

    await advance_pipeline(session_factory, document_id)

    status, _ = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        assert document.ocr_text == "OCR says hello"
        assert document.page_count == 2


async def test_resume_from_extract_reruns_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document stranded in ``extract`` re-runs the extraction hook.

    Same defect as the OCR case, one stage along: ``status=extract`` means the
    stage was entered, so the resume must run it rather than advance past it.
    """
    calls: list[int] = []

    async def _stub_apply_extraction(
        session: AsyncSession, document: Document, settings: object
    ) -> None:
        calls.append(document.id)

    monkeypatch.setattr(jobs, "apply_extraction", _stub_apply_extraction)

    document_id = await make_document(session_factory, "pipeline-resume-extract")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.EXTRACT  # killed inside the extract hook
        await session.commit()

    await advance_pipeline(session_factory, document_id)

    status, _ = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    # The entered stage ran; without the resume hook it would have been skipped.
    assert calls == [document_id]


def _completed_extraction_outcome() -> ExtractionOutcome:
    """A minimal successful extraction outcome, stamped with the current prompt."""
    metadata = ExtractedMetadata.model_validate(
        {
            "kind_slug": "other",
            "sender_name": None,
            "recipient_name": None,
            "title": "Resume double-spend probe",
            "summary": "A document whose extraction already completed.",
            "document_date": None,
            "amount_total": None,
            "currency": None,
            "due_date": None,
            "expiry_date": None,
            "language": "eng",
            "tags": [],
            "confidence": "high",
            "reasoning_note": None,
        }
    )
    return ExtractionOutcome(
        metadata=metadata,
        model="claude-haiku-4-5",
        prompt_version=EXTRACTION_PROMPT_VERSION,
        input_mode="text",
        escalated=False,
        calls=[
            CallUsage(model="claude-haiku-4-5", input_tokens=10, output_tokens=10, cost_usd=0.1)
        ],
    )


async def test_resume_from_extract_does_not_respend_completed_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resume in the *post-hook* window must not pay for extraction twice.

    The extract hook commits its own results and its ``extraction_completed``
    event while ``status`` is still ``extract``; the advance to ``markdown``
    only lands at the top of the *next* loop iteration. A worker killed in
    between therefore leaves ``status=extract`` with the stage's work already
    done and durable — by status alone, indistinguishable from a kill *inside*
    the hook. Re-running is not merely wasted CPU here: it is a second billed
    Anthropic call for work already paid for. The completion stamp
    (``extra["extraction"]["prompt_version"]``) is what tells the two apart.
    """
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LIBRARY_EXTRACTION_DAILY_BUDGET_USD", "1000")
    monkeypatch.setenv("LIBRARY_MARKDOWN_ENABLED", "false")
    get_settings.cache_clear()

    paid_calls: list[int] = []

    async def counting_extract(document: Document, ocr_text: str, **kwargs: object) -> object:
        paid_calls.append(document.id)
        return _completed_extraction_outcome()

    monkeypatch.setattr(extraction_apply, "extract", counting_extract)

    document_id = await make_document(session_factory, "pipeline-resume-extract-paid")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # Killed AFTER the extract hook committed everything, but BEFORE the
        # loop advanced the status to `markdown`.
        document.status = DocumentStatus.EXTRACT
        document.ocr_text = "OCR says hello"
        document.extra = {
            "extraction": {
                "prompt_version": EXTRACTION_PROMPT_VERSION,
                "model": "claude-haiku-4-5",
                "fields_set": ["title", "summary"],
            }
        }
        await session.commit()

    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    assert paid_calls == []  # not one more Anthropic call
    skipped = [detail for event, detail in events if event == "extraction_skipped"]
    assert skipped == [{"reason": "already_extracted", "prompt_version": EXTRACTION_PROMPT_VERSION}]


async def test_resume_from_markdown_does_not_respend_completed_markdown(
    session_factory: async_sessionmaker[AsyncSession],
    fake_router: OcrResult,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same post-hook window, one stage along: markdown must not re-spend.

    ``markdown_completed`` (with the generator's current ``PROMPT_VERSION``) is
    committed in the same transaction as the page rows, so its presence proves
    the stage finished even though ``status`` still says ``markdown``.
    """
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LIBRARY_MARKDOWN_DAILY_BUDGET_USD", "1000")
    get_settings.cache_clear()

    paid_calls: list[int] = []

    async def counting_generate(
        document: Document, ocr_text: str, images: list[bytes], **kwargs: object
    ) -> MarkdownResult:
        paid_calls.append(document.id)
        return MarkdownResult(
            pages=[GeneratedPage(page_number=1, markdown="# Re-generated")],
            model="claude-haiku-4-5",
            prompt_version=MARKDOWN_PROMPT_VERSION,
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.1,
        )

    def fake_render(*args: object, **kwargs: object) -> list[bytes]:
        return [b"jpeg-bytes"]

    monkeypatch.setattr(markdown_apply, "generate_markdown", counting_generate)
    monkeypatch.setattr(markdown_apply, "render_page_images", fake_render)

    document_id = await make_document(session_factory, "pipeline-resume-markdown-paid")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        # Killed AFTER the markdown hook committed pages + event, BEFORE the
        # loop advanced the status to `embed`.
        document.status = DocumentStatus.MARKDOWN
        document.ocr_text = "OCR says hello"
        document.pages_markdown = "# Page 1"
        session.add(
            DocumentPage(document_id=document.id, page_number=1, markdown="# Page 1", char_count=8)
        )
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="markdown_completed",
                detail={
                    "model": "claude-haiku-4-5",
                    "prompt_version": MARKDOWN_PROMPT_VERSION,
                    "pages": 1,
                    "cost_usd": 0.1,
                },
            )
        )
        await session.commit()

    await advance_pipeline(session_factory, document_id)

    status, events = await get_status_and_events(session_factory, document_id)
    assert status == DocumentStatus.INDEXED
    assert paid_calls == []  # not one more Anthropic call
    skipped = [detail for event, detail in events if event == "markdown_skipped"]
    assert skipped == [{"reason": "already_generated", "prompt_version": MARKDOWN_PROMPT_VERSION}]


async def test_classification_job_skips_a_document_already_classified(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pipeline's re-deferred classification job must not re-spend.

    A resume in the post-hook window re-runs the extract branch, which defers
    ``classify_document_matters`` a second time. In merge mode that second call
    can only re-attach matters the document already has — the attachments are
    deduped, the Anthropic call is not. ``skip_if_classified`` (set only by the
    pipeline) is what makes the duplicate job free; ``sweep-matters``, which
    re-runs merge mode over classified documents on purpose, does not set it and
    is unaffected.
    """
    billed_calls: list[int] = []

    async def stub_classify(
        session: AsyncSession, document: Document, settings: object, *, replace: bool = False
    ) -> None:
        billed_calls.append(document.id)

    monkeypatch.setattr(jobs, "apply_matter_classification", stub_classify)
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: session_factory)

    document_id = await make_document(session_factory, "matter-already-classified")
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        document.extra = {
            "matter_classification": {
                "prompt_version": MATTER_CLASSIFIER_PROMPT_VERSION,
                "mode": "merge",
                "attached_slugs": [],
            }
        }
        await session.commit()

    await jobs.classify_document_matters(document_id=document_id, skip_if_classified=True)
    assert billed_calls == []

    # The CLI's deliberate re-sweep (no flag) still classifies.
    await jobs.classify_document_matters(document_id=document_id)
    assert billed_calls == [document_id]


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

    # ...and the loss is durable, not just a log line. The retry policy cannot
    # help here — the job was never queued, so there is nothing to retry — so
    # the document's own timeline is the only place this can surface.
    _, events = await get_status_and_events(session_factory, document_id)
    lost = [detail for name, detail in events if name == "job_defer_failed"]
    assert len(lost) == 1
    assert lost[0]["task"] == "thumbnail"
    assert "transient queue blip" in lost[0]["error"]


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
