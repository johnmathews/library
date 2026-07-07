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
from library.docx import DOCX_MIME
from library.email_ingest import (
    EmailPollSummary,
    IngestCallable,
    IngestCandidate,
    SkippedAttachment,
    poll_mailbox,
    poll_mailbox_async,
)
from library.ingest import IngestResult
from library.models import Document, DocumentSource, IngestionEvent
from tests.docx_fixtures import make_docx

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
    to_addr: str = "library@example.test",
    subject: str = "Invoice",
    message_id: str | None = None,
    attachments: list[tuple[str, bytes, str, str]] | None = None,
) -> bytes:
    """Raw RFC822 bytes for a (possibly multipart) mail with attachments."""
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    message.set_content("see attached")
    for filename, content, maintype, subtype in attachments or []:
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def make_body_mail(
    *,
    from_addr: str = "john@example.com",
    to_addr: str = "library@example.test",
    subject: str = "Invoice",
    message_id: str | None = None,
    text: str | None = None,
    html: str | None = None,
) -> bytes:
    """Raw RFC822 bytes for a body-only mail (no attachments).

    ``text`` and/or ``html`` populate the alternative parts; passing neither
    yields a genuinely empty-bodied message.
    """
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    if text is not None:
        message.set_content(text)
    if html is not None:
        if text is not None:
            message.add_alternative(html, subtype="html")
        else:
            message.set_content(html, subtype="html")
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


def test_html_to_markdown_preserves_tables_and_drops_noise() -> None:
    from library.markdown.html import html_to_markdown

    html = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<h1>Invoice</h1>"
        "<table><tr><th>Item</th><th>Total</th></tr>"
        "<tr><td>Widget</td><td>42</td></tr></table>"
        "<script>alert(1)</script></body></html>"
    )
    md = html_to_markdown(html)
    assert "# Invoice" in md  # heading survives
    assert "| Item | Total |" in md and "| Widget | 42 |" in md  # table survives
    assert "alert(1)" not in md  # <script> contents dropped
    assert "color:red" not in md  # <style> contents dropped


async def test_html_body_creates_document_when_no_attachment(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    tag = uuid.uuid4().hex[:8]
    subject = f"html invoice {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        from_addr="Jane Voorbeeld <jane@example.org>",
        subject=subject,
        message_id=message_id,
        html=f"<html><body><h1>Invoice {tag}</h1><p>Total 42</p></body></html>",
    )
    mailbox = FakeMailBox([mail_message(raw, uid="3")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    # HTML body is converted to Markdown and stored as a first-class text type.
    documents = await documents_named(session_factory, f"{subject}.md")
    assert len(documents) == 1
    document = documents[0]
    assert document.mime_type == "text/markdown"
    assert document.source is DocumentSource.EMAIL
    # Body ingestion carries the same provenance as an attachment would.
    events = await events_for(session_factory, document.id, "received")
    assert events[0].detail["email_from"] == "jane@example.org"
    assert events[0].detail["email_subject"] == subject
    assert events[0].detail["email_message_id"] == message_id
    assert mailbox.moved == [("3", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


async def test_text_body_creates_document_when_no_html(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    tag = uuid.uuid4().hex[:8]
    subject = f"plain invoice {tag}"
    raw = make_body_mail(subject=subject, text=f"Plain-text invoice {tag}, total 42")
    mailbox = FakeMailBox([mail_message(raw, uid="3")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, f"{subject}.txt")
    assert len(documents) == 1
    assert documents[0].mime_type == "text/plain"
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


async def test_body_not_ingested_when_attachment_produces_document(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A cover-note body alongside a real attachment must not spawn a 2nd document.
    tag = uuid.uuid4().hex[:8]
    subject = f"cover note {tag}"
    name = f"real-{tag}.pdf"
    raw = make_raw_mail(subject=subject, attachments=[(name, make_pdf(), "application", "pdf")])
    mailbox = FakeMailBox([mail_message(raw, uid="3")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert len(await documents_named(session_factory, name)) == 1
    assert await documents_named(session_factory, f"{subject}.txt") == []
    assert await documents_named(session_factory, f"{subject}.md") == []
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


async def test_empty_body_mail_moved_without_ingest(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    tag = uuid.uuid4().hex[:8]
    subject = f"empty {tag}"
    raw = make_body_mail(subject=subject)  # no text, no html, no attachments
    mailbox = FakeMailBox([mail_message(raw, uid="3")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, f"{subject}.txt") == []
    assert await documents_named(session_factory, f"{subject}.md") == []
    assert mailbox.moved == [("3", PROCESSED_FOLDER)]  # still filed away
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)


async def test_docx_attachment_becomes_document(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A forwarded .docx is converted to Markdown at ingest and becomes a
    # document (previously dropped-but-flagged). The .docx is the stored
    # original; its derived Markdown drives the pipeline.
    tag = uuid.uuid4().hex[:8]
    name = f"form-{tag}.docx"
    raw = make_raw_mail(
        subject="Enrolment form",
        attachments=[
            (
                name,
                make_docx(heading="Enrolment Form", marker=tag),
                "application",
                "vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="7")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].mime_type == DOCX_MIME
    assert documents[0].source is DocumentSource.EMAIL
    assert mailbox.moved == [("7", PROCESSED_FOLDER)]
    assert summary.attachments_ingested == 1


async def test_docx_attachment_suppresses_body_ingestion(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A docx attachment that now converts successfully counts as producing a
    # document, so the cover-note body must not spawn a second document.
    tag = uuid.uuid4().hex[:8]
    name = f"form-{tag}.docx"
    raw = make_raw_mail(
        subject="Please complete the attached form",
        attachments=[
            (
                name,
                make_docx(heading="Enrolment Form", marker=tag),
                "application",
                "vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="8")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert len(await documents_named(session_factory, name)) == 1
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


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


async def test_multiple_supported_attachments_each_become_a_document(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The doc-135 shape (minus the drop bug): one email, several supported
    # attachments — every one must become its own document. This path was
    # previously untested.
    tag = uuid.uuid4().hex[:8]
    names = [f"a-{tag}.pdf", f"b-{tag}.pdf", f"c-{tag}.pdf"]
    raw = make_raw_mail(
        subject=f"three invoices {tag}",
        attachments=[
            (name, make_pdf(f"{tag}-{i}"), "application", "pdf") for i, name in enumerate(names)
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="30")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    for name in names:
        assert len(await documents_named(session_factory, name)) == 1
    assert mailbox.moved == [("30", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=3
    )


def _recording_ingest(
    calls: list[IngestCandidate], *, fail_for: str | None = None
) -> IngestCallable:
    """A fake ``ingest`` for driving ``poll_mailbox`` directly (no DB/loop).

    Records every candidate it receives; raises an unexpected error for the
    candidate named ``fail_for`` (to exercise sibling isolation). ``poll_mailbox``
    only reads ``result.duplicate``, so the fake document is a sentinel.
    """

    def ingest(candidate: IngestCandidate) -> IngestResult:
        calls.append(candidate)
        if fail_for is not None and candidate.filename == fail_for:
            raise RuntimeError("boom")
        return IngestResult(document=object(), duplicate=False)  # type: ignore[arg-type]

    return ingest


def test_dropped_sibling_stamped_on_survivor_and_notified(settings: Settings) -> None:
    # A supported PNG plus an unsupported sibling (drives poll_mailbox directly
    # with a fake ingest — no DB). The PNG candidate is stamped with the dropped
    # sibling, the summary counts the drop, and notify is called once.
    tag = uuid.uuid4().hex[:8]
    png_name = f"photo-{tag}.png"
    png = b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes + b"\x00" * 32
    raw = make_raw_mail(
        subject=f"mixed {tag}",
        attachments=[
            ("secret.docx", b"\x00\xffPK-not-really" + uuid.uuid4().bytes, "application", "msword"),
            (png_name, png, "image", "png"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="31")])
    calls: list[IngestCandidate] = []
    notified: list[tuple[str | None, str | None, list[str | None]]] = []

    def spy_notify(
        sender: str | None, subject: str | None, skipped: list[SkippedAttachment]
    ) -> None:
        assert all(isinstance(s, SkippedAttachment) for s in skipped)
        notified.append((sender, subject, [s.filename for s in skipped]))

    summary = poll_mailbox(
        settings,
        _recording_ingest(calls),
        mailbox_factory=lambda: mailbox,
        notify=spy_notify,
    )

    # The PNG is the only ingested candidate, and it carries the dropped sibling.
    assert [c.filename for c in calls] == [png_name]
    dropped = calls[0].extra_document["email_siblings_dropped"]  # type: ignore[index]
    assert [d["filename"] for d in dropped] == ["secret.docx"]
    assert dropped[0]["reason"] == "unsupported_type"
    assert summary.attachments_ingested == 1
    assert summary.attachments_dropped == 1
    assert mailbox.moved == [("31", PROCESSED_FOLDER)]
    # Notified once, with the dropped filename.
    assert len(notified) == 1
    assert notified[0][2] == ["secret.docx"]


def test_body_fallback_document_carries_dropped_siblings(settings: Settings) -> None:
    # A mail whose only attachment is unsupported but which has a cover-note body:
    # the body becomes the document and must still carry the dropped sibling, so
    # the email_attachments_dropped review reason fires on it.
    tag = uuid.uuid4().hex[:8]
    # make_raw_mail always sets a plain-text body ("see attached"); the single
    # attachment is unsupported, so the body becomes the only document.
    raw = make_raw_mail(
        subject=f"cover {tag}",
        attachments=[
            ("form.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword")
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="33")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    # The only ingested candidate is the body, and it carries the dropped sibling.
    assert len(calls) == 1
    assert calls[0].extra_document["email_siblings_dropped"][0]["filename"] == "form.docx"  # type: ignore[index]
    assert summary.attachments_ingested == 1  # the body
    assert summary.attachments_dropped == 1


def test_attachment_error_does_not_abort_siblings(settings: Settings) -> None:
    # If ingesting one attachment raises an *unexpected* (non-IngestError)
    # exception, its siblings must still be ingested and the message still moved
    # (no permanent-retry wedge). Regression for the whole-message abort.
    tag = uuid.uuid4().hex[:8]
    good_name = f"good-{tag}.pdf"
    bad_name = f"bad-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"one bad {tag}",
        attachments=[
            (bad_name, make_pdf(f"bad-{tag}"), "application", "pdf"),
            (good_name, make_pdf(f"good-{tag}"), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="32")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(
        settings,
        _recording_ingest(calls, fail_for=bad_name),
        mailbox_factory=lambda: mailbox,
    )

    # Both were attempted; the good one succeeded, the bad one is counted as a drop.
    assert {c.filename for c in calls} == {good_name, bad_name}
    assert mailbox.moved == [("32", PROCESSED_FOLDER)]  # not wedged
    assert summary.attachments_ingested == 1
    assert summary.attachments_dropped == 1


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


async def test_body_email_stores_matching_to_address_in_document_extra(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # A body-only mail whose To: matches a user's forward address stores that
    # address (lowercased) under document.extra["email_to"] for the recipient
    # fallback in extraction.apply. A display-name form is parsed to the address.
    tag = uuid.uuid4().hex[:8]
    to_address = f"family-{tag}@example.test"
    await _make_user(
        session_factory,
        username=f"recip-{tag}",
        forward_addresses=[to_address],
    )
    subject = f"plain to-match {tag}"
    raw = make_body_mail(
        to_addr=f"Family Inbox <{to_address.upper()}>",
        subject=subject,
        text=f"Plain-text invoice {tag}, total 42",
    )
    mailbox = FakeMailBox([mail_message(raw, uid="21")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, f"{subject}.txt")
    assert len(documents) == 1
    assert documents[0].extra["email_to"] == [to_address.lower()]
    # And the same provenance is recorded on the received event.
    events = await events_for(session_factory, documents[0].id, "received")
    assert events[0].detail["email_to"] == [to_address.lower()]


async def test_attachment_email_stores_matching_to_address_in_document_extra(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # An attachment mail (not just a body-only mail) whose To: matches a user's
    # forward address stores that address (lowercased) under
    # document.extra["email_to"], so the extraction recipient fallback works for
    # attachments exactly as it does for email bodies.
    tag = uuid.uuid4().hex[:8]
    to_address = f"family-{tag}@example.test"
    await _make_user(
        session_factory,
        username=f"attrecip-{tag}",
        forward_addresses=[to_address],
    )
    name = f"attach-to-{tag}.pdf"
    raw = make_raw_mail(
        to_addr=f"Family Inbox <{to_address.upper()}>",
        subject=f"attach to-match {tag}",
        attachments=[(name, make_pdf(tag), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="22")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].extra["email_to"] == [to_address.lower()]
    # And the same provenance is recorded on the received event.
    events = await events_for(session_factory, documents[0].id, "received")
    assert events[0].detail["email_to"] == [to_address.lower()]


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
