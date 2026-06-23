"""Tests for email-in ingestion (library.email_ingest + jobs wiring, W14).

Strategy: mock at the imap-tools boundary — a ``FakeMailBox`` holding
real ``MailMessage`` objects built from raw RFC822 bytes
(``MailMessage.from_bytes``) — and drive ``poll_mailbox_async`` exactly
the way the periodic task does (worker thread + ingest marshalled back
onto the loop), against the real testcontainers database.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from email.message import EmailMessage
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from imap_tools import MailMessage
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
from library.email_ingest import EmailPollSummary, poll_mailbox, poll_mailbox_async
from library.models import Document, DocumentSource, IngestionEvent

pytestmark = pytest.mark.integration

PROCESSED_FOLDER = "Library/Processed"


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


def make_pdf(marker: str | None = None) -> bytes:
    """Unique, sniffable-as-PDF content (api_database_url is shared)."""
    return b"%PDF-1.4\n% " + (marker or uuid.uuid4().hex).encode() + b"\n%%EOF\n"


def make_raw_mail(
    *,
    from_addr: str = "john@example.com",
    subject: str = "Invoice",
    message_id: str | None = None,
    attachments: list[tuple[str, bytes, str, str]] | None = None,
) -> bytes:
    """Raw RFC822 bytes for a (possibly multipart) mail with attachments."""
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = "library@example.test"
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    message.set_content("see attached")
    for filename, content, maintype, subtype in attachments or []:
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def mail_message(raw: bytes, uid: str) -> MailMessage:
    """A real imap-tools MailMessage with the (fetch-derived) uid injected."""
    message = MailMessage.from_bytes(raw)
    message.__dict__["uid"] = uid  # pre-populate the cached_property
    return message


class FakeFolderManager:
    """In-memory stand-in for ``MailBox.folder``."""

    def __init__(self, folders: set[str]) -> None:
        self.folders = set(folders)
        self.created: list[str] = []

    def exists(self, folder: str) -> bool:
        return folder in self.folders

    def create(self, folder: str) -> tuple[object, ...]:
        self.folders.add(folder)
        self.created.append(folder)
        return ()


class FakeMailBox:
    """Mock at the imap-tools boundary: fetch/move/folder over a message list."""

    def __init__(self, messages: list[MailMessage]) -> None:
        self.messages = list(messages)
        self.folder = FakeFolderManager({"INBOX"})
        self.moved: list[tuple[str, str]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def fetch(self, criteria: str = "ALL", *, mark_seen: bool = True) -> Iterator[MailMessage]:
        assert mark_seen is False  # the poller must not touch seen flags
        return iter(list(self.messages))

    def move(self, uid_list: str, destination_folder: str) -> None:
        self.moved.append((uid_list, destination_folder))
        self.messages = [message for message in self.messages if message.uid != uid_list]


async def documents_named(
    session_factory: async_sessionmaker[AsyncSession], filename: str
) -> list[Document]:
    async with session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.original_filename == filename)
        )
        return list(result.scalars().all())


async def events_for(
    session_factory: async_sessionmaker[AsyncSession], document_id: int, event: str
) -> list[IngestionEvent]:
    async with session_factory() as session:
        result = await session.execute(
            select(IngestionEvent).where(
                IngestionEvent.document_id == document_id, IngestionEvent.event == event
            )
        )
        return list(result.scalars().all())


def test_email_settings_defaults() -> None:
    settings = Settings()
    assert settings.email_host is None  # feature off by default
    assert settings.email_port == 993
    assert settings.email_username is None
    assert settings.email_password is None
    assert settings.email_folder == "INBOX"
    assert settings.email_processed_folder == "Library/Processed"
    assert settings.email_poll_minutes == 10
    assert settings.email_allowed_senders == []


def test_email_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_EMAIL_HOST", "imap.example.com")
    monkeypatch.setenv("LIBRARY_EMAIL_USERNAME", "library@example.com")
    monkeypatch.setenv("LIBRARY_EMAIL_PASSWORD", "app-password")
    monkeypatch.setenv("LIBRARY_EMAIL_POLL_MINUTES", "5")
    # Comma-separated allowlist, normalised to lowercase, blanks dropped.
    monkeypatch.setenv("LIBRARY_EMAIL_ALLOWED_SENDERS", " John@Example.com, jane@example.com ,")
    settings = Settings()
    assert settings.email_host == "imap.example.com"
    assert settings.email_username is not None
    assert settings.email_username.get_secret_value() == "library@example.com"
    assert settings.email_password is not None
    assert settings.email_password.get_secret_value() == "app-password"
    assert settings.email_poll_minutes == 5
    assert settings.email_allowed_senders == ["john@example.com", "jane@example.com"]


async def test_attachment_creates_document_and_moves_message(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    name = f"invoice-{uuid.uuid4().hex[:8]}.pdf"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        from_addr="Jane Voorbeeld <jane@example.org>",
        subject="Energy invoice",
        message_id=message_id,
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="1")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    document = documents[0]
    assert document.source is DocumentSource.EMAIL
    assert document.uploader_id is None
    # The received event carries the sender hint for extraction/audit.
    events = await events_for(session_factory, document.id, "received")
    assert len(events) == 1
    assert events[0].detail["source"] == "email"
    assert events[0].detail["email_from"] == "jane@example.org"
    assert events[0].detail["email_subject"] == "Energy invoice"
    assert events[0].detail["email_message_id"] == message_id
    # Processing was enqueued through the normal pipeline.
    process_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.process_document"
        and job["args"] == {"document_id": document.id}
    ]
    assert len(process_jobs) == 1
    # The message was moved to the processed folder (created on demand).
    assert mailbox.folder.created == [PROCESSED_FOLDER]
    assert mailbox.moved == [("1", PROCESSED_FOLDER)]
    assert mailbox.messages == []
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


async def test_allowlist_rejects_unknown_sender(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    settings = Settings(
        email_host="imap.example.test",
        email_allowed_senders="john@example.com",  # type: ignore[arg-type]  # env form
    )
    name = f"spam-{uuid.uuid4().hex[:8]}.pdf"
    raw = make_raw_mail(
        from_addr="stranger@evil.example",
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="7")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, name) == []
    assert mailbox.moved == []  # left in place, visible to the operator
    assert summary == EmailPollSummary(messages_seen=1, messages_skipped=1)


async def test_allowlist_accepts_listed_sender_case_insensitively(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    settings = Settings(
        email_host="imap.example.test",
        email_allowed_senders="John@Example.com",  # type: ignore[arg-type]  # env form
    )
    name = f"ok-{uuid.uuid4().hex[:8]}.pdf"
    raw = make_raw_mail(
        from_addr="JOHN@example.COM",
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="8")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert len(await documents_named(session_factory, name)) == 1
    assert summary.messages_processed == 1


async def test_duplicate_attachment_moved_without_new_document(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    content = make_pdf()
    first_name = f"first-{uuid.uuid4().hex[:8]}.pdf"
    second_name = f"second-{uuid.uuid4().hex[:8]}.pdf"
    first = mail_message(
        make_raw_mail(attachments=[(first_name, content, "application", "pdf")]), uid="1"
    )
    second = mail_message(
        make_raw_mail(
            subject="Re: Invoice", attachments=[(second_name, content, "application", "pdf")]
        ),
        uid="2",
    )

    mailbox = FakeMailBox([first])
    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)
    # Second run: same content arrives again in a different mail.
    mailbox_two = FakeMailBox([second])
    summary = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox_two
    )

    documents = await documents_named(session_factory, first_name)
    assert len(documents) == 1
    assert await documents_named(session_factory, second_name) == []
    # The duplicate is recorded against the existing document with the
    # second mail's provenance, and the message still counts as processed.
    events = await events_for(session_factory, documents[0].id, "duplicate_upload")
    assert len(events) == 1
    assert events[0].detail["source"] == "email"
    assert events[0].detail["email_subject"] == "Re: Invoice"
    assert mailbox_two.moved == [("2", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_duplicate=1
    )


async def test_body_only_mail_moved_without_ingest(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    raw = make_raw_mail(subject="just words, no files")
    mailbox = FakeMailBox([mail_message(raw, uid="3")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert mailbox.moved == [("3", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)


async def test_unsupported_attachment_skipped_message_still_moved(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    pdf_name = f"good-{uuid.uuid4().hex[:8]}.pdf"
    raw = make_raw_mail(
        attachments=[
            ("report.docx", b"\x00\xffPK-not-really" + uuid.uuid4().bytes, "application", "msword"),
            (pdf_name, make_pdf(), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="4")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert len(await documents_named(session_factory, pdf_name)) == 1
    assert await documents_named(session_factory, "report.docx") == []
    assert mailbox.moved == [("4", PROCESSED_FOLDER)]
    assert summary.attachments_ingested == 1


async def test_broken_message_isolated_from_rest_of_run(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    class BrokenMessage:
        """A message whose body parsing explodes."""

        uid = "13"
        from_ = "john@example.com"
        subject = "corrupted"

        @property
        def attachments(self) -> list[object]:
            raise RuntimeError("boom")

        @property
        def obj(self) -> dict[str, str]:
            return {"Message-ID": "<broken@example.com>"}

    good_name = f"good-{uuid.uuid4().hex[:8]}.pdf"
    good = mail_message(
        make_raw_mail(attachments=[(good_name, make_pdf(), "application", "pdf")]), uid="14"
    )
    mailbox = FakeMailBox([BrokenMessage(), good])  # type: ignore[list-item]

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    # The broken mail is skipped (left in place); the good one still lands.
    assert len(await documents_named(session_factory, good_name)) == 1
    assert mailbox.moved == [("14", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=2, messages_processed=1, messages_skipped=1, attachments_ingested=1
    )


def test_poller_disabled_when_host_unset() -> None:
    def explode() -> FakeMailBox:
        raise AssertionError("mailbox must not be opened when the poller is off")

    summary = poll_mailbox(
        Settings(email_host=None),
        ingest=lambda candidate: pytest.fail("must not ingest"),
        mailbox_factory=explode,
    )
    assert summary == EmailPollSummary()


def test_email_poll_cron_built_from_minutes() -> None:
    assert jobs.email_poll_cron(10) == "*/10 * * * *"
    assert jobs.email_poll_cron(1) == "*/1 * * * *"
    # Out-of-range values clamp into cron's minute field.
    assert jobs.email_poll_cron(0) == "*/1 * * * *"
    assert jobs.email_poll_cron(90) == "*/59 * * * *"


def test_periodic_task_registered_with_default_cron() -> None:
    periodic = jobs.job_app.periodic_registry.periodic_tasks[("library.jobs.poll_email_inbox", "")]
    assert periodic.cron == "*/10 * * * *"
    assert periodic.task is jobs.poll_email_inbox


async def test_periodic_task_noops_when_host_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIBRARY_EMAIL_HOST", raising=False)
    get_settings.cache_clear()
    try:
        called = False

        def fail_to_thread(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("poll must not run when LIBRARY_EMAIL_HOST is unset")

        monkeypatch.setattr(asyncio, "to_thread", fail_to_thread)
        await jobs.poll_email_inbox(timestamp=0)
        assert called is False
    finally:
        get_settings.cache_clear()


# --- Sender → user attribution (W4) ---


async def _make_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    forward_addresses: list[str] | None = None,
) -> int:
    from library.models import User

    async with session_factory() as session:
        prefs: dict[str, object] = {}
        if forward_addresses is not None:
            prefs = {"notifications": {"email_forward_addresses": forward_addresses}}
        user = User(username=username, password_hash="x", preferences=prefs)
        session.add(user)
        await session.commit()
        return user.id


async def test_email_attributes_document_to_forwarding_user(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    owner_id = await _make_user(
        session_factory,
        username=f"owner-{uuid.uuid4().hex[:8]}",
        forward_addresses=["jane@example.org"],
    )
    name = f"owned-{uuid.uuid4().hex[:8]}.pdf"
    raw = make_raw_mail(
        from_addr="Jane Voorbeeld <jane@example.org>",  # display name + addr; case differs below
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="1")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].uploader_id == owner_id


async def test_email_unknown_sender_falls_back_to_default_owner(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    default_username = f"default-{uuid.uuid4().hex[:8]}"
    default_id = await _make_user(session_factory, username=default_username)
    settings = Settings(email_host="imap.example.test", email_default_owner=default_username)
    name = f"fallback-{uuid.uuid4().hex[:8]}.pdf"
    raw = make_raw_mail(
        from_addr="stranger@nowhere.test",
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="1")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].uploader_id == default_id


async def test_resolve_sender_owner_matches_case_insensitively(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from library.email_ingest import resolve_sender_owner

    owner_id = await _make_user(
        session_factory,
        username=f"ci-{uuid.uuid4().hex[:8]}",
        forward_addresses=["me@example.com"],
    )
    async with session_factory() as session:
        # Mixed-case + whitespace still matches the stored lowercased address.
        assert await resolve_sender_owner(session, "  Me@Example.COM ") == owner_id
        # Unknown sender, no default → None.
        assert await resolve_sender_owner(session, "nobody@example.com") is None
