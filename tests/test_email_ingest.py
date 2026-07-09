"""Tests for email-in ingestion (library.email_ingest + jobs wiring, W14).

Strategy: mock at the imap-tools boundary — a ``FakeMailBox`` holding
real ``MailMessage`` objects built from raw RFC822 bytes
(``MailMessage.from_bytes``) — and drive ``poll_mailbox_async`` exactly
the way the periodic task does (worker thread + ingest marshalled back
onto the loop), against the real testcontainers database.
"""

import asyncio
import io
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from email.message import EmailMessage
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from imap_tools import MailMessage
from PIL import Image
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
    _body_substance,
    _forwarded_to_addresses,
    poll_mailbox,
    poll_mailbox_async,
)
from library.email_label import LabelOutcome
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


def long_body(tag: str) -> str:
    """A >40-word body that clears the substance threshold, unique per ``tag``."""
    return (
        f"Invoice {tag}. Please find below the full breakdown of charges for this "
        "billing period. The total amount due is forty-two euros, payable within "
        "fourteen days of receipt. This message contains the complete invoice text "
        "so it stands on its own as a document and does not require any attachment "
        "to be forwarded alongside it for the record."
    )


def make_raw_mail(
    *,
    from_addr: str = "john@example.com",
    to_addr: str = "library@example.test",
    subject: str = "Invoice",
    message_id: str | None = None,
    body: str = "see attached",
    attachments: list[tuple[str, bytes, str, str]] | None = None,
) -> bytes:
    """Raw RFC822 bytes for a (possibly multipart) mail with attachments."""
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    message.set_content(body)
    for filename, content, maintype, subtype in attachments or []:
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def png_bytes(size: tuple[int, int]) -> bytes:
    """A valid PNG of the given dimensions (for the noise-gate image rules)."""
    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="PNG")
    return buffer.getvalue()


def make_inline_image_mail(
    *,
    subject: str,
    image: bytes,
    cid: str = "logo@example.com",
    extra: list[tuple[str, bytes, str, str]] | None = None,
) -> bytes:
    """Raw bytes for an HTML mail whose body embeds ``image`` inline by ``cid:``.

    The image is an ``inline`` part with a ``Content-ID`` the HTML references —
    the shape of a signature logo. ``extra`` adds ordinary attachments after it.
    """
    message = EmailMessage()
    message["From"] = "john@example.com"
    message["To"] = "library@example.test"
    message["Subject"] = subject
    message["Message-ID"] = f"<{uuid.uuid4().hex}@example.com>"
    message.set_content("See the logo below.")
    message.add_alternative(
        f'<html><body><p>See logo</p><img src="cid:{cid}"/></body></html>', subtype="html"
    )
    message.add_attachment(
        image,
        maintype="image",
        subtype="png",
        filename="logo.png",
        disposition="inline",
        cid=f"<{cid}>",
    )
    for filename, content, maintype, subtype in extra or []:
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
    # Deterministic noise gate: on by default, with conservative thresholds.
    assert settings.email_filter_noise_enabled is True
    assert settings.email_filter_tiny_image_max_bytes == 4096
    assert settings.email_filter_tiny_image_max_edge_px == 64
    # Optional LLM label pass: off by default (it spends money).
    assert settings.email_label_enabled is False
    assert settings.email_label_model == "claude-haiku-4-5"
    assert settings.email_label_daily_budget_usd == 2.0
    assert settings.email_label_body_snippet_chars == 1000


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
        html=f"<html><body><h1>Invoice {tag}</h1><p>{long_body(tag)}</p></body></html>",
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
    raw = make_body_mail(subject=subject, text=long_body(tag))
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


async def test_cover_note_body_below_threshold_not_ingested(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # "FYI see attached" with no attachment is not worth filing — but the mail is
    # still moved so it is not re-polled forever.
    tag = uuid.uuid4().hex[:8]
    subject = f"cover only {tag}"
    raw = make_body_mail(subject=subject, text="FYI see attached")
    mailbox = FakeMailBox([mail_message(raw, uid="60")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, f"{subject}.txt") == []
    assert mailbox.moved == [("60", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)


def test_quoted_reply_stripped_before_substance_check(settings: Settings) -> None:
    # A short genuine note atop a long quoted reply: once the quote is stripped,
    # only the short note remains → below threshold → not ingested. Were the quote
    # not stripped, its length would push the body over the threshold.
    tag = uuid.uuid4().hex[:8]
    quoted = "\n".join(f"> {word}" for word in (long_body(tag) + " " + long_body(tag)).split())
    text = f"Thanks, got it.\nOn Mon, Jan 1, 2026 at 9am John <j@x.com> wrote:\n{quoted}"
    raw = make_body_mail(subject=f"reply {tag}", text=text)
    mailbox = FakeMailBox([mail_message(raw, uid="61")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert calls == []  # the quoted context was stripped, leaving too little to file
    assert summary.attachments_ingested == 0


def test_body_substance_strips_mobile_footer() -> None:
    assert _body_substance("Here is the real message.\n\nSent from my iPhone") == (
        "Here is the real message."
    )


def test_body_substance_stops_at_signature_delimiter() -> None:
    assert _body_substance("The actual content.\n--\nJohn Smith\nCEO") == "The actual content."


def test_body_substance_stops_at_quote_header() -> None:
    assert _body_substance("My reply.\nOn Tue, someone wrote:\n> old stuff") == "My reply."


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
    # Above the tiny-image byte threshold so the noise gate keeps it (the point of
    # this test is the unsupported *sibling*, not the image).
    png = b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes + b"\x00" * 5000
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
    # A substantive body (clears the substance gate); the single attachment is
    # unsupported, so the body becomes the only document and carries the sibling.
    raw = make_raw_mail(
        subject=f"cover {tag}",
        body=long_body(tag),
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


def _selection_trace_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every ``email-selection`` decision-trace line captured, formatted."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("email-selection")
    ]


def test_selection_trace_logged_per_email(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # A real PDF plus an unsupported sibling: the always-on decision trace names
    # each item, its deciding stage, and its verdict — the debug/triage surface.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"real-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"trace {tag}",
        attachments=[
            (pdf_name, make_pdf(tag), "application", "pdf"),
            ("junk.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="40")])
    calls: list[IngestCandidate] = []

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    lines = _selection_trace_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert "ingested=1" in line and "dropped=1" in line
    assert f"{pdf_name}:classify:ingested" in line
    assert "junk.docx:classify:dropped(unsupported_type)" in line
    # An attachment produced a document, so the body was not needed.
    assert "<body>:body_substance:filtered(not_needed)" in line


def test_selection_trace_logged_when_no_document(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # An empty-bodied mail produces nothing — but the trace still records what
    # happened (the persisted event can't, having no document to hang on).
    tag = uuid.uuid4().hex[:8]
    raw = make_body_mail(subject=f"empty {tag}")  # no text, no html, no attachments
    mailbox = FakeMailBox([mail_message(raw, uid="42")])

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(settings, _recording_ingest([]), mailbox_factory=lambda: mailbox)

    lines = _selection_trace_lines(caplog)
    assert len(lines) == 1
    assert "items=1" in lines[0]
    assert "<body>:body_substance:filtered(blank)" in lines[0]
    assert summary.attachments_ingested == 0


async def test_selection_trace_persisted_as_event(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The decision trace is also stored as an ``email_selection`` event on the
    # produced document, so it shows in that document's history.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"traced-{tag}.pdf"
    subject = f"persist {tag}"
    raw = make_raw_mail(
        subject=subject, attachments=[(pdf_name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="41")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1
    events = await events_for(session_factory, documents[0].id, "email_selection")
    assert len(events) == 1
    detail = events[0].detail
    assert detail["email_subject"] == subject  # provenance carried
    items = detail["items"]
    assert any(
        item["filename"] == pdf_name
        and item["stage"] == "classify"
        and item["verdict"] == "ingested"
        for item in items
    )


def test_inline_signature_image_filtered(settings: Settings) -> None:
    # A real PDF plus an inline signature logo: the logo is filtered (recorded,
    # not surfaced), only the PDF is ingested. Drives poll_mailbox directly.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"real-{tag}.pdf"
    raw = make_inline_image_mail(
        subject=f"sig {tag}",
        image=png_bytes((200, 200)),  # normal-sized image; caught by inline, not size
        extra=[(pdf_name, make_pdf(tag), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="50")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [pdf_name]
    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 1
    assert summary.attachments_dropped == 0  # noise is quiet, never a user-facing drop


def test_calendar_part_filtered_as_non_document(settings: Settings) -> None:
    # A .ics part sniffs as the allowed text/plain, but its declared Content-Type
    # is text/calendar — the non_document rule catches it so it is not filed.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"real-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"cal {tag}",
        attachments=[
            (
                "invite.ics",
                b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                "text",
                "calendar",
            ),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="51")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [pdf_name]
    assert summary.attachments_filtered == 1


def test_tiny_image_filtered(settings: Settings) -> None:
    # A 1x1 tracking pixel is filtered by dimensions; the real PDF is ingested.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"real-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"tiny {tag}",
        attachments=[
            ("pixel.png", png_bytes((1, 1)), "image", "png"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="52")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [pdf_name]
    assert summary.attachments_filtered == 1


def test_normal_image_kept_despite_small_byte_size(settings: Settings) -> None:
    # Bias to ingest: a 200x200 image is a real document even though it compresses
    # below the byte threshold — dimensions decide, so it is kept, not filtered.
    tag = uuid.uuid4().hex[:8]
    img_name = f"scan-{tag}.png"
    raw = make_raw_mail(
        subject=f"scan {tag}", attachments=[(img_name, png_bytes((200, 200)), "image", "png")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="53")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [img_name]
    assert summary.attachments_filtered == 0
    assert summary.attachments_ingested == 1


def test_noise_gate_disabled_ingests_everything() -> None:
    # The escape hatch: with the gate off, the inline pixel and the .ics both
    # reach ingest exactly as before the feature.
    settings = Settings(email_host="imap.example.test", email_filter_noise_enabled=False)
    tag = uuid.uuid4().hex[:8]
    raw = make_inline_image_mail(
        subject=f"off {tag}",
        image=png_bytes((1, 1)),
        extra=[("invite.ics", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "text", "calendar")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="54")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    names = {candidate.filename for candidate in calls}
    assert "logo.png" in names  # the inline pixel is ingested when the gate is off
    assert "invite.ics" in names  # the calendar (text/plain) is ingested too
    assert summary.attachments_filtered == 0


def test_label_flags_probably_noise_but_still_ingests(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # A fake labeller marks one of two PDFs as probably_noise. Both are still
    # ingested (the label never drops); the flagged one carries the
    # email_selection hint that drives the needs_review finding.
    tag = uuid.uuid4().hex[:8]
    keep_name = f"invoice-{tag}.pdf"
    flag_name = f"banner-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"label {tag}",
        attachments=[
            (keep_name, make_pdf(f"{tag}-a"), "application", "pdf"),
            (flag_name, make_pdf(f"{tag}-b"), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="70")])
    calls: list[IngestCandidate] = []

    def fake_label(request: object) -> LabelOutcome:
        verdicts: dict[int, tuple[str, str | None]] = {}
        for item in request.items:  # type: ignore[attr-defined]
            if item.filename == flag_name:
                verdicts[item.index] = ("probably_noise", "looks like a banner")
            else:
                verdicts[item.index] = ("keep", None)
        return LabelOutcome(verdicts=verdicts, usage=None, skip_reason=None)

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(
            settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox, label=fake_label
        )

    by_name = {candidate.filename: candidate for candidate in calls}
    assert set(by_name) == {keep_name, flag_name}
    selection = by_name[flag_name].extra_document["email_selection"]  # type: ignore[index]
    assert selection["verdict"] == "probably_noise"
    assert selection["source"] == "llm_label"
    assert selection["reason"] == "looks like a banner"
    assert (by_name[keep_name].extra_document or {}).get("email_selection") is None
    assert summary.attachments_ingested == 2  # both ingested; nothing dropped
    # The trace records the flagged item at the llm_label stage.
    trace = _selection_trace_lines(caplog)[0]
    assert f"{flag_name}:llm_label:flagged_ambiguous" in trace


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
        text=long_body(tag),
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


def test_forwarded_to_addresses_extracts_original_recipient() -> None:
    """The original recipient inside a forwarded block is parsed from the body."""
    from types import SimpleNamespace

    body = (
        "FYI, please file this.\n\n"
        "---------- Forwarded message ----------\n"
        "From: Eneco <billing@eneco.nl>\n"
        "To: John Mathews <john@example.com>\n"
        "Subject: Your invoice\n\n"
        "Dear John, here is your invoice.\n"
    )
    assert _forwarded_to_addresses(SimpleNamespace(text=body)) == ["john@example.com"]


def test_forwarded_to_addresses_handles_dutch_aan_header_and_quotes() -> None:
    from types import SimpleNamespace

    body = (
        "Zie bijlage.\n\n"
        "On Mon, 1 Jun 2026, Eneco wrote:\n"
        "> Van: Eneco\n"
        "> Aan: Jan de Vries <jan@example.nl>\n"
        "> Onderwerp: Factuur\n"
    )
    assert _forwarded_to_addresses(SimpleNamespace(text=body)) == ["jan@example.nl"]


def test_forwarded_to_addresses_empty_without_forward_banner() -> None:
    from types import SimpleNamespace

    assert _forwarded_to_addresses(SimpleNamespace(text="Just a plain note, no forward.")) == []
    assert _forwarded_to_addresses(SimpleNamespace(text="")) == []


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
