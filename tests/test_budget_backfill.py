"""Tests for budget-skip visibility + the daily auto-backfill task (W5)."""

import hashlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library import budget_backfill, jobs
from library.budget_backfill import budget_skipped_count, budget_skipped_document_ids
from library.config import get_settings
from library.jobs import backfill_budget_skipped, job_app
from library.models import Document, DocumentSource, IngestionEvent

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Document))
        await session.commit()
        yield session


async def _seed(session: AsyncSession, marker: str, events: list[tuple[str, dict]]) -> int:
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
    )
    session.add(document)
    await session.commit()
    for event, detail in events:
        session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
        await session.commit()  # sequential commits keep id order = chronological
    return document.id


async def test_budget_skipped_detects_latest_extraction_and_markdown(
    session: AsyncSession,
) -> None:
    ext = await _seed(session, "ext-budget", [("extraction_skipped", {"reason": "budget"})])
    md = await _seed(session, "md-budget", [("markdown_skipped", {"reason": "budget"})])
    # A later success clears the skip → not reported.
    recovered = await _seed(
        session,
        "recovered",
        [("extraction_skipped", {"reason": "budget"}), ("extraction_completed", {})],
    )
    # A non-budget skip (disabled) is not a budget skip.
    disabled = await _seed(session, "disabled", [("extraction_skipped", {"reason": "disabled"})])

    ids = await budget_skipped_document_ids(session)

    assert set(ids) == {ext, md}
    assert recovered not in ids
    assert disabled not in ids
    assert await budget_skipped_count(session) == 2


async def test_backfill_task_registered_daily() -> None:
    assert backfill_budget_skipped.name == "library.jobs.backfill_budget_skipped"
    periodic = job_app.periodic_registry.periodic_tasks[
        ("library.jobs.backfill_budget_skipped", "")
    ]
    assert periodic.cron == "17 3 * * *"


async def test_backfill_reenqueues_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When enabled, each budget-skipped doc is re-enqueued for extract + markdown."""
    monkeypatch.setenv("LIBRARY_BUDGET_BACKFILL_ENABLED", "true")
    get_settings.cache_clear()

    async def fake_ids(_session: object) -> list[int]:
        return [11, 22]

    class _DummySession:
        async def __aenter__(self) -> "_DummySession":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    extracted: list[int] = []
    marked: list[int] = []

    async def fake_extract(*, document_id: int) -> None:
        extracted.append(document_id)

    async def fake_markdown(*, document_id: int) -> None:
        marked.append(document_id)

    monkeypatch.setattr(budget_backfill, "budget_skipped_document_ids", fake_ids)
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: lambda: _DummySession())
    monkeypatch.setattr(jobs.extract_document, "defer_async", fake_extract)
    monkeypatch.setattr(jobs.markdown_document, "defer_async", fake_markdown)

    try:
        await backfill_budget_skipped(timestamp=0)
    finally:
        get_settings.cache_clear()

    assert extracted == [11, 22]
    assert marked == [11, 22]


async def test_backfill_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (disabled) never queries or spends."""
    monkeypatch.delenv("LIBRARY_BUDGET_BACKFILL_ENABLED", raising=False)
    get_settings.cache_clear()
    called = False

    async def fake_ids(_session: object) -> list[int]:
        nonlocal called
        called = True
        return [1]

    monkeypatch.setattr(budget_backfill, "budget_skipped_document_ids", fake_ids)
    try:
        await backfill_budget_skipped(timestamp=0)
    finally:
        get_settings.cache_clear()

    assert called is False
