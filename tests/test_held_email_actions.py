"""Tests for held-email resolution (W12): ingest-anyway override + dismiss.

Same strategy as ``test_email_ingest``: mock at the imap-tools boundary with
the (now folder-aware) ``FakeMailBox``, drive the real ``poll_mailbox_async``
to produce a genuine hold (row + message in the Held folder), then resolve it
via ``ingest_held_email_async`` / ``dismiss_held_email`` against the real
testcontainers database. The shared database is session-scoped, so every test
uses unique subjects/Message-IDs and never asserts absolute totals.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library import jobs
from library.config import Settings, get_settings
from library.email_ingest import (
    dismiss_held_email,
    ingest_held_email_async,
    poll_mailbox_async,
)
from library.models import HeldEmail, HeldEmailStatus
from tests.test_email_ingest import (
    HELD_FOLDER,
    PROCESSED_FOLDER,
    FakeAnthropic,
    FakeMailBox,
    _make_user,
    documents_named,
    events_for,
    held_rows,
    mail_message,
    make_body_mail,
    make_pdf,
    make_raw_mail,
    png_bytes,
    skip_trace_rows,
)

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
def settings() -> Settings:
    """Poller settings with the feature enabled (connection is always faked)."""
    return Settings(email_host="imap.example.test")


@pytest.fixture
def patched_anthropic(monkeypatch: pytest.MonkeyPatch) -> FakeAnthropic:
    """Replace the poller's AsyncAnthropic with a recording fake client."""
    fake = FakeAnthropic()
    monkeypatch.setattr("library.email_ingest.AsyncAnthropic", fake.constructor)
    return fake


def label_settings(**overrides: object) -> Settings:
    """Poller settings with the LLM label pass enabled (client always faked)."""
    return Settings(
        email_host="imap.example.test",
        email_label_enabled=True,
        anthropic_api_key="test-key",  # type: ignore[arg-type]  # env form
        **overrides,  # type: ignore[arg-type]
    )


async def _hold_body_only_email(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    mailbox: FakeMailBox,
    message_id: str,
) -> HeldEmail:
    """Run one poll expected to hold the mailbox's single message; return the row."""
    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)
    assert summary.messages_held == 1
    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    return rows[0]


async def _reload(
    session_factory: async_sessionmaker[AsyncSession], held_email_id: int
) -> HeldEmail:
    async with session_factory() as session:
        row = await session.get(HeldEmail, held_email_id)
        assert row is not None
        return row


def _process_jobs_for(connector: InMemoryConnector, document_id: int) -> list[dict[str, object]]:
    return [
        job
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.process_document"
        and job["args"] == {"document_id": document_id}
    ]


# --- Ingest anyway ---------------------------------------------------------


async def test_ingest_anyway_below_substance_files_body_and_moves_to_processed(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The headline override: a below-substance cover note was held; a human
    # says "ingest anyway" — the substance gate is bypassed, the body files as
    # a document through the normal pipeline, the row resolves, and the message
    # moves Held → Processed.
    tag = uuid.uuid4().hex[:8]
    subject = f"override thin {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text=f"FYI see attached ({tag})", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="200")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    documents = await documents_named(session_factory, f"{subject}.txt")
    assert len(documents) == 1  # the gate was bypassed: the body IS the document
    document = documents[0]
    assert len(_process_jobs_for(job_connector, document.id)) == 1  # normal pipeline
    resolved = await _reload(session_factory, row.id)
    assert resolved.status is HeldEmailStatus.INGESTED
    assert resolved.resolved_by_id == resolver_id
    assert resolved.resolved_at is not None
    assert resolved.document_ids == [document.id]
    assert resolved.last_error is None
    # The decision trace is persisted on the produced document, like the poll's.
    selection_events = await events_for(session_factory, document.id, "email_selection")
    assert len(selection_events) == 1
    items = selection_events[0].detail["items"]
    assert any(
        item["stage"] == "email_verdict"
        and item["verdict"] == "ingested"
        and item["reason"] == "manual_override"
        for item in items
    )
    # Held → Processed: the message left the Held folder.
    assert mailbox.moved == [("200", HELD_FOLDER), ("200", PROCESSED_FOLDER)]
    assert mailbox.folders[HELD_FOLDER] == []
    assert len(mailbox.folders[PROCESSED_FOLDER]) == 1


async def test_ingest_anyway_llm_hold_ingests_attachments_without_label_call(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # Overriding an llm_hold: the attachments go through the normal
    # deterministic gates (the tiny pixel is still filtered) but the label pass
    # is NOT consulted again — the human already overruled it.
    patched_anthropic.email_verdict = "hold"
    patched_anthropic.email_reason = "newsletter blast"
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"override-{tag}.pdf"
    pixel_name = f"pixel-{tag}.png"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=f"override llm {tag}",
        message_id=message_id,
        attachments=[
            (pdf_name, make_pdf(tag), "application", "pdf"),
            (pixel_name, png_bytes((16, 16)), "image", "png"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="201")])
    row = await _hold_body_only_email(label_settings(), session_factory, mailbox, message_id)
    assert row.verdict == "llm_hold"
    assert len(patched_anthropic.calls) == 1  # the poll's own label call
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        label_settings(), session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    assert len(patched_anthropic.calls) == 1  # NO new label call for the override
    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1  # the real attachment ingested…
    assert await documents_named(session_factory, pixel_name) == []  # …the pixel stayed filtered
    resolved = await _reload(session_factory, row.id)
    assert resolved.status is HeldEmailStatus.INGESTED
    assert resolved.document_ids == [documents[0].id]
    assert mailbox.moved[-1] == ("201", PROCESSED_FOLDER)


async def test_ingest_anyway_overrides_decoration_image_filter(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A mail whose only attachment was quietly filtered as decoration_image
    # (logo name + small bytes + 200px shape) and whose thin body held it as
    # below_substance. "Ingest anyway" bypasses the decoration heuristic — the
    # human's intent wins — so the image files as a document; hard gates
    # (tiny_image etc.) are covered by the llm_hold override test above.
    tag = uuid.uuid4().hex[:8]
    logo_name = f"logo-{tag}.png"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=f"override deco {tag}",
        message_id=message_id,
        attachments=[(logo_name, png_bytes((200, 200)), "image", "png")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="210")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    assert row.verdict == "below_substance"
    # The poll's trace shows the attachment WAS filtered by the decoration rule.
    assert any(item["reason"] == "decoration_image" for item in row.trace["items"])
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    documents = await documents_named(session_factory, logo_name)
    assert len(documents) == 1  # the decoration rule yielded to the override
    resolved = await _reload(session_factory, row.id)
    assert resolved.status is HeldEmailStatus.INGESTED
    assert resolved.resolved_by_id == resolver_id
    assert resolved.document_ids == [documents[0].id]
    assert resolved.last_error is None
    assert mailbox.moved[-1] == ("210", PROCESSED_FOLDER)
    assert mailbox.folders[HELD_FOLDER] == []


async def test_ingest_anyway_hard_gate_filter_writes_skip_trace_row(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # W4 on the override path: ingest-anyway bypasses the decoration heuristics
    # but the HARD gates still apply — a tiny pixel is still filtered — so the
    # override run writes its own durable skip-trace row, exactly like a poll.
    # Shape: the poll holds the mail (pixel filtered + thin body =
    # below_substance); the override re-fetches it, still filters the pixel,
    # and files the body (substance gate bypassed).
    tag = uuid.uuid4().hex[:8]
    pixel_name = f"pixel-{tag}.png"
    subject = f"override skiptrace {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=subject,
        message_id=message_id,
        attachments=[(pixel_name, png_bytes((16, 16)), "image", "png")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="211")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    assert row.verdict == "below_substance"
    assert await skip_trace_rows(session_factory, subject) == []  # held: no trace row
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    resolved = await _reload(session_factory, row.id)
    assert resolved.status is HeldEmailStatus.INGESTED
    assert await documents_named(session_factory, pixel_name) == []  # hard gate held
    rows = await skip_trace_rows(session_factory, subject)
    assert len(rows) == 1
    assert rows[0].message_id == message_id
    assert any(
        item["filename"] == pixel_name and item["reason"] == "tiny_image"
        for item in rows[0].decisions
    )


async def test_ingest_anyway_message_missing_sets_last_error_and_stays_held(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The message vanished from the Held folder (human moved/deleted it by
    # hand): the row records the failure and stays held — never a false
    # "ingested" resolution.
    tag = uuid.uuid4().hex[:8]
    subject = f"override missing {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text=f"FYI see attached ({tag})", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="202")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    mailbox.folders[HELD_FOLDER].clear()  # the message is gone
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    assert await documents_named(session_factory, f"{subject}.txt") == []
    reloaded = await _reload(session_factory, row.id)
    assert reloaded.status is HeldEmailStatus.HELD  # still held, retryable
    assert reloaded.resolved_at is None
    assert reloaded.last_error is not None and "not found" in reloaded.last_error
    assert mailbox.moved == [("202", HELD_FOLDER)]  # nothing moved by the override


async def test_ingest_anyway_double_fire_second_run_noops(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A double-fired override (queue retry, double click) must not ingest
    # twice: the second run sees status=ingested and does nothing at all.
    tag = uuid.uuid4().hex[:8]
    subject = f"override twice {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text=f"FYI see attached ({tag})", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="203")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    resolver_id = await _make_user(session_factory, username=f"resolver-{tag}")

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )
    first = await _reload(session_factory, row.id)
    moves_after_first = list(mailbox.moved)

    await ingest_held_email_async(
        settings, session_factory, row.id, resolver_id, mailbox_factory=lambda: mailbox
    )

    assert len(await documents_named(session_factory, f"{subject}.txt")) == 1  # no new rows
    second = await _reload(session_factory, row.id)
    assert second.status is HeldEmailStatus.INGESTED
    assert second.resolved_at == first.resolved_at  # untouched by the no-op
    assert second.document_ids == first.document_ids
    assert mailbox.moved == moves_after_first  # the mailbox was never opened again


def test_ingest_held_email_task_registered() -> None:
    task = jobs.job_app.tasks["library.jobs.ingest_held_email"]
    assert task is jobs.ingest_held_email


# --- Dismiss ----------------------------------------------------------------


async def test_dismiss_flips_status_without_touching_imap(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # Dismiss is DB-only (D5): the row resolves, the message stays in the Held
    # folder forever — the bytes remain recoverable after a wrong dismiss.
    tag = uuid.uuid4().hex[:8]
    subject = f"dismiss {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text=f"FYI see attached ({tag})", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="204")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    resolver_id = await _make_user(session_factory, username=f"dismisser-{tag}")

    async with session_factory() as session:
        dismissed = await dismiss_held_email(session, row.id, resolver_id)

    assert dismissed.status is HeldEmailStatus.DISMISSED
    assert dismissed.resolved_by_id == resolver_id
    assert dismissed.resolved_at is not None
    assert dismissed.document_ids == []
    reloaded = await _reload(session_factory, row.id)
    assert reloaded.status is HeldEmailStatus.DISMISSED
    # The FakeMailBox is untouched beyond the original hold move.
    assert mailbox.moved == [("204", HELD_FOLDER)]
    assert len(mailbox.folders[HELD_FOLDER]) == 1  # bytes still there
    assert await documents_named(session_factory, f"{subject}.txt") == []


async def test_dismiss_of_non_held_row_raises_value_error(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    tag = uuid.uuid4().hex[:8]
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        subject=f"dismiss twice {tag}", text=f"FYI see attached ({tag})", message_id=message_id
    )
    mailbox = FakeMailBox([mail_message(raw, uid="205")])
    row = await _hold_body_only_email(settings, session_factory, mailbox, message_id)
    resolver_id = await _make_user(session_factory, username=f"dismisser-{tag}")

    async with session_factory() as session:
        await dismiss_held_email(session, row.id, resolver_id)

    async with session_factory() as session:
        with pytest.raises(ValueError, match="already dismissed"):
            await dismiss_held_email(session, row.id, resolver_id)
        with pytest.raises(ValueError, match="does not exist"):
            await dismiss_held_email(session, 987_654_321, resolver_id)
