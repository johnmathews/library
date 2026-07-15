"""Tests for email-in ingestion (library.email_ingest + jobs wiring, W14).

Strategy: mock at the imap-tools boundary — a ``FakeMailBox`` holding
real ``MailMessage`` objects built from raw RFC822 bytes
(``MailMessage.from_bytes``) — and drive ``poll_mailbox_async`` exactly
the way the periodic task does (worker thread + ingest marshalled back
onto the loop), against the real testcontainers database.
"""

import asyncio
import hashlib
import io
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace, TracebackType
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
    HoldRecord,
    IngestCallable,
    IngestCandidate,
    SkippedAttachment,
    _body_substance,
    _connect,
    _decoration_signals,
    _forwarded_to_addresses,
    poll_mailbox,
    poll_mailbox_async,
)
from library.email_label import EmailLabelResult, ItemLabel, LabelOutcome
from library.extraction.apply import todays_spend_usd
from library.ingest import IngestResult, ingest_file
from library.models import (
    Document,
    DocumentSource,
    EmailSelectionTrace,
    HeldEmail,
    HeldEmailStatus,
    IngestionEvent,
    ReviewStatus,
)
from tests.docx_fixtures import make_docx

pytestmark = pytest.mark.integration

PROCESSED_FOLDER = "Library/Processed"
HELD_FOLDER = "Library/Held"


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


def noisy_png_bytes(size: tuple[int, int]) -> bytes:
    """A PNG of the given dimensions that stays LARGE (incompressible noise).

    Random pixels defeat PNG's compression, so an 800x600 image lands well
    above the decoration byte ceiling — for negatives where the size signal
    must genuinely be off.
    """
    buffer = io.BytesIO()
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    image.save(buffer, format="PNG")
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
    date: str | None = None,
) -> bytes:
    """Raw RFC822 bytes for a body-only mail (no attachments).

    ``text`` and/or ``html`` populate the alternative parts; passing neither
    yields a genuinely empty-bodied message. ``date`` sets an RFC 2822 ``Date:``
    header (omitted by default, like the other fixtures).
    """
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    if date is not None:
        message["Date"] = date
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
    """In-memory stand-in for ``MailBox.folder`` (folder-aware, W12)."""

    def __init__(self, mailbox: "FakeMailBox") -> None:
        self._mailbox = mailbox
        self.created: list[str] = []

    def exists(self, folder: str) -> bool:
        return folder in self._mailbox.folders

    def create(self, folder: str) -> tuple[object, ...]:
        self._mailbox.folders.setdefault(folder, [])
        self.created.append(folder)
        return ()

    def set(self, folder: str) -> tuple[object, ...]:
        if folder not in self._mailbox.folders:
            raise OSError(f"folder {folder!r} does not exist")
        self._mailbox.active_folder = folder
        return ()


#: The Message-ID header search criteria imap-tools renders for
#: ``AND(header=H("Message-ID", value))`` — what ingest-anyway fetches with.
_MESSAGE_ID_CRITERIA_RE = re.compile(r'HEADER "Message-ID" "([^"]+)"')


class FakeMailBox:
    """Mock at the imap-tools boundary: fetch/move/folder over per-folder lists.

    Folder-aware (W12): messages live in ``folders[name]`` lists; ``folder.set``
    switches the active folder (like selecting an IMAP folder) and ``move``
    relocates a message between them, so a held message is still fetchable from
    the Held folder by a later ingest-anyway. ``fetch`` supports ``"ALL"`` and
    the Message-ID header search (substring match, like a real IMAP HEADER
    search). ``messages`` reads/writes the *active* folder's list, keeping the
    original single-folder call sites working unchanged.

    ``fail_move_uids``: uids whose ``move`` raises (the message stays in place),
    for exercising move-failure retry semantics. Clear the set to let a later
    poll's move succeed.
    """

    def __init__(
        self, messages: list[MailMessage], *, fail_move_uids: set[str] | None = None
    ) -> None:
        self.folders: dict[str, list[MailMessage]] = {"INBOX": list(messages)}
        self.active_folder = "INBOX"
        self.folder = FakeFolderManager(self)
        self.moved: list[tuple[str, str]] = []
        self.fail_move_uids: set[str] = set(fail_move_uids or ())

    @property
    def messages(self) -> list[MailMessage]:
        return self.folders[self.active_folder]

    @messages.setter
    def messages(self, value: list[MailMessage]) -> None:
        self.folders[self.active_folder] = list(value)

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
        current = list(self.folders[self.active_folder])
        if str(criteria) == "ALL":
            return iter(current)
        match = _MESSAGE_ID_CRITERIA_RE.search(str(criteria))
        assert match is not None, f"unsupported fetch criteria {criteria!r}"
        needle = match.group(1)
        return iter([message for message in current if needle in (message.obj["Message-ID"] or "")])

    def move(self, uid_list: str, destination_folder: str) -> None:
        if uid_list in self.fail_move_uids:
            raise OSError("move failed")
        source = self.folders[self.active_folder]
        moving = [message for message in source if message.uid == uid_list]
        self.folders[self.active_folder] = [
            message for message in source if message.uid != uid_list
        ]
        self.folders.setdefault(destination_folder, []).extend(moving)
        self.moved.append((uid_list, destination_folder))


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


async def held_rows(
    session_factory: async_sessionmaker[AsyncSession], message_id: str
) -> list[HeldEmail]:
    """Every ``held_emails`` row for one Message-ID (the DB is shared; scope by id)."""
    async with session_factory() as session:
        result = await session.execute(select(HeldEmail).where(HeldEmail.message_id == message_id))
        return list(result.scalars().all())


async def skip_trace_rows(
    session_factory: async_sessionmaker[AsyncSession], subject: str
) -> list[EmailSelectionTrace]:
    """Every ``email_selection_traces`` row for one subject (shared DB; scope by tag)."""
    async with session_factory() as session:
        result = await session.execute(
            select(EmailSelectionTrace).where(EmailSelectionTrace.subject == subject)
        )
        return list(result.scalars().all())


#: One manifest line as ``library.email_label._manifest`` renders it; ``kind=`` is
#: optional so the fake survives the v1 → v2 manifest evolution.
_MANIFEST_ITEM_RE = re.compile(r"\[(\d+)\](?: kind=\w+)? filename=(?:'([^']*)'|None)")


class FakeAnthropic:
    """Stands in for ``library.email_ingest.AsyncAnthropic``.

    Mirrors ``tests/test_email_label.py::_FakeClient`` — ``messages.parse``
    returns a ``SimpleNamespace(parsed_output=EmailLabelResult(...), usage=...)``
    and records every call — plus the async-context-manager lifetime the poller
    manages via its ``AsyncExitStack``. Every manifest item is labelled ``keep``
    unless its filename contains a ``noise_markers`` substring, which yields
    ``probably_noise`` instead.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.api_keys: list[str] = []
        self.entered = 0
        self.exited = 0
        self.noise_markers: list[str] = []
        #: Whole-email verdict returned with every response ("file" | "hold").
        self.email_verdict: str = "file"
        self.email_reason: str | None = None
        self.messages = SimpleNamespace(parse=self._parse)

    def constructor(self, *, api_key: str) -> Self:
        """The ``AsyncAnthropic(api_key=...)`` call the poller makes."""
        self.api_keys.append(api_key)
        return self

    async def __aenter__(self) -> Self:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.exited += 1

    async def _parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        content = kwargs["messages"][0]["content"]  # type: ignore[index]
        items: list[ItemLabel] = []
        for match in _MANIFEST_ITEM_RE.finditer(str(content)):
            index, filename = int(match.group(1)), match.group(2) or ""
            if any(marker in filename for marker in self.noise_markers):
                items.append(
                    ItemLabel(index=index, verdict="probably_noise", reason="looks decorative")
                )
            else:
                items.append(ItemLabel(index=index, verdict="keep"))
        return SimpleNamespace(
            parsed_output=EmailLabelResult(
                items=items,
                email_verdict=self.email_verdict,  # type: ignore[arg-type]
                email_reason=self.email_reason,
            ),
            usage=SimpleNamespace(input_tokens=120, output_tokens=40),
        )


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


def test_email_settings_defaults() -> None:
    settings = Settings()
    assert settings.email_host is None  # feature off by default
    assert settings.email_port == 993
    assert settings.email_imap_timeout_seconds == 60.0
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
    # Hold-for-review: on by default (holding is strictly safer than a silent
    # drop); email_hold_enabled=False is the rollback lever.
    assert settings.email_hold_enabled is True
    assert settings.email_held_folder == "Library/Held"
    assert settings.email_hold_below_substance is True
    assert settings.email_hold_unknown_senders is True


def test_email_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_EMAIL_HOST", "imap.example.com")
    monkeypatch.setenv("LIBRARY_EMAIL_USERNAME", "library@example.com")
    monkeypatch.setenv("LIBRARY_EMAIL_PASSWORD", "app-password")
    monkeypatch.setenv("LIBRARY_EMAIL_POLL_MINUTES", "5")
    monkeypatch.setenv("LIBRARY_EMAIL_IMAP_TIMEOUT_SECONDS", "30")
    # Comma-separated allowlist, normalised to lowercase, blanks dropped.
    monkeypatch.setenv("LIBRARY_EMAIL_ALLOWED_SENDERS", " John@Example.com, jane@example.com ,")
    settings = Settings()
    assert settings.email_host == "imap.example.com"
    assert settings.email_imap_timeout_seconds == 30.0
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
    # An allowlist-rejected sender is HELD: durable row + moved to the Held
    # folder, nothing ingested — visible and recoverable instead of lingering
    # in the inbox forever.
    settings = Settings(
        email_host="imap.example.test",
        email_allowed_senders="john@example.com",  # type: ignore[arg-type]  # env form
    )
    name = f"spam-{uuid.uuid4().hex[:8]}.pdf"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        from_addr="stranger@evil.example",
        message_id=message_id,
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="7")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, name) == []
    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].status is HeldEmailStatus.HELD
    assert rows[0].verdict == "sender_unknown"
    assert rows[0].sender == "stranger@evil.example"
    assert rows[0].imap_folder == HELD_FOLDER
    assert rows[0].imap_uid == "7"
    assert rows[0].owner_id is None  # unknown sender matches no user
    assert mailbox.moved == [("7", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)


async def test_allowlist_reject_left_in_place_when_hold_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The rollback lever: with the master switch off, an allowlist reject
    # behaves exactly as before the hold feature — left in place, no row.
    settings = Settings(
        email_host="imap.example.test",
        email_allowed_senders="john@example.com",  # type: ignore[arg-type]  # env form
        email_hold_enabled=False,
    )
    name = f"spam-{uuid.uuid4().hex[:8]}.pdf"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        from_addr="stranger@evil.example",
        message_id=message_id,
        attachments=[(name, make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="7")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, name) == []
    assert await held_rows(session_factory, message_id) == []
    assert mailbox.moved == []  # left in place, visible to the operator
    assert summary == EmailPollSummary(messages_seen=1, messages_skipped=1)


def test_allowlist_reject_left_in_place_when_trigger_disabled() -> None:
    # The per-trigger flag alone (master switch still on) also restores the
    # pre-hold behavior for unknown senders.
    settings = Settings(
        email_host="imap.example.test",
        email_allowed_senders="john@example.com",  # type: ignore[arg-type]  # env form
        email_hold_unknown_senders=False,
    )
    raw = make_raw_mail(
        from_addr="stranger@evil.example",
        attachments=[(f"spam-{uuid.uuid4().hex[:8]}.pdf", make_pdf(), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="7")])
    holds: list[HoldRecord] = []

    summary = poll_mailbox(
        settings,
        ingest=lambda candidate: pytest.fail("must not ingest"),
        mailbox_factory=lambda: mailbox,
        persist_hold=holds.append,
    )

    assert holds == []
    assert mailbox.moved == []
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


async def test_cover_note_body_below_threshold_held_for_review(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # "FYI see attached" with no attachment produces no document — the email is
    # HELD (durable row + Held folder) so a human can ingest-anyway or dismiss,
    # instead of it silently filing away to Processed.
    tag = uuid.uuid4().hex[:8]
    subject = f"cover only {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text="FYI see attached", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="60")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, f"{subject}.txt") == []
    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].status is HeldEmailStatus.HELD
    assert rows[0].verdict == "below_substance"
    assert rows[0].reason is not None and rows[0].reason.startswith("below_substance:")
    assert rows[0].subject == subject
    assert rows[0].imap_folder == HELD_FOLDER
    # The trace snapshots the decision list, including the whole-email verdict.
    items = rows[0].trace["items"]
    assert any(
        item["kind"] == "email" and item["stage"] == "email_verdict" and item["verdict"] == "held"
        for item in items
    )
    assert any(item["kind"] == "body" and item["verdict"] == "filtered" for item in items)
    assert mailbox.moved == [("60", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)


async def test_below_substance_body_processed_when_hold_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # Master switch off: the cover note files away to Processed exactly as
    # before the hold feature — no row, no Held folder.
    settings = Settings(email_host="imap.example.test", email_hold_enabled=False)
    tag = uuid.uuid4().hex[:8]
    subject = f"cover only {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text="FYI see attached", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="60")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert await documents_named(session_factory, f"{subject}.txt") == []
    assert await held_rows(session_factory, message_id) == []
    assert mailbox.moved == [("60", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)


def test_below_substance_body_processed_when_trigger_disabled() -> None:
    # The per-trigger flag alone restores the pre-hold behavior for thin bodies.
    settings = Settings(email_host="imap.example.test", email_hold_below_substance=False)
    tag = uuid.uuid4().hex[:8]
    raw = make_body_mail(subject=f"cover only {tag}", text="FYI see attached")
    mailbox = FakeMailBox([mail_message(raw, uid="60")])
    holds: list[HoldRecord] = []

    summary = poll_mailbox(
        settings, _recording_ingest([]), mailbox_factory=lambda: mailbox, persist_hold=holds.append
    )

    assert holds == []
    assert mailbox.moved == [("60", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)


async def test_held_row_carries_owner_and_received_at_for_matching_sender(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The held row resolves its owner from the sender exactly like a document
    # would, and captures the Date: header, so the review queue can scope/sort.
    tag = uuid.uuid4().hex[:8]
    sender = f"jane-{tag}@example.org"
    owner_id = await _make_user(
        session_factory, username=f"held-owner-{tag}", forward_addresses=[sender]
    )
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        from_addr=f"Jane <{sender}>",
        subject=f"held owner {tag}",
        text="FYI see attached",
        message_id=message_id,
        date="Mon, 13 Jul 2026 09:30:00 +0200",
    )
    mailbox = FakeMailBox([mail_message(raw, uid="61")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].owner_id == owner_id
    assert rows[0].received_at is not None
    assert rows[0].received_at == datetime(2026, 7, 13, 7, 30, tzinfo=UTC)
    assert summary.messages_held == 1


async def test_nothing_ingested_drops_hold_email(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only user-facing drops (an unsupported attachment) and a blank body:
    # nothing was ingested, so the email is held — the drop is recoverable from
    # review instead of filed out of sight. No drop push fires for a held
    # message (the review queue is the surface).
    tag = uuid.uuid4().hex[:8]
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=f"drops only {tag}",
        message_id=message_id,
        body="",
        attachments=[
            ("form.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword")
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="140")])
    notified: list[object] = []

    async def spy_dispatch(*args: object, **kwargs: object) -> None:
        notified.append(kwargs)

    monkeypatch.setattr(
        "library.email_ingest.dispatch_attachments_dropped_notification", spy_dispatch
    )

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].verdict == "nothing_ingested"
    assert rows[0].reason == "no attachment or body produced a document"
    items = rows[0].trace["items"]
    assert any(item["filename"] == "form.docx" and item["verdict"] == "dropped" for item in items)
    assert notified == []  # the push waits: the whole email is in review
    assert mailbox.moved == [("140", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1, attachments_dropped=1)


def test_nothing_ingested_processed_and_notified_when_hold_disabled() -> None:
    # Master switch off: the drops-only email files to Processed and the drop
    # push fires, exactly as before the hold feature.
    settings = Settings(email_host="imap.example.test", email_hold_enabled=False)
    tag = uuid.uuid4().hex[:8]
    raw = make_raw_mail(
        subject=f"drops only {tag}",
        body="",
        attachments=[
            ("form.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword")
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="141")])
    holds: list[HoldRecord] = []
    notified: list[list[str | None]] = []

    def spy_notify(
        sender: str | None, subject: str | None, skipped: list[SkippedAttachment]
    ) -> None:
        notified.append([item.filename for item in skipped])

    summary = poll_mailbox(
        settings,
        _recording_ingest([]),
        mailbox_factory=lambda: mailbox,
        notify=spy_notify,
        persist_hold=holds.append,
    )

    assert holds == []
    assert notified == [["form.docx"]]
    assert mailbox.moved == [("141", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1, attachments_dropped=1)


async def test_hold_move_failure_retried_without_duplicate_row(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # Row-before-move: a Held-folder move that fails once leaves the message in
    # place WITH its row already written. The retry poll finds the open row
    # (skip-if-exists on the partial unique index), does not duplicate it, and
    # completes the move.
    tag = uuid.uuid4().hex[:8]
    subject = f"retry hold {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(subject=subject, text="FYI see attached", message_id=message_id)
    mailbox = FakeMailBox([mail_message(raw, uid="142")], fail_move_uids={"142"})

    summary_one = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox
    )

    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1  # the durable row landed before the failed move
    assert rows[0].verdict == "below_substance"
    assert mailbox.moved == []
    assert len(mailbox.messages) == 1  # message stays for the next poll
    assert summary_one.messages_held == 0
    assert summary_one.messages_skipped == 1

    mailbox.fail_move_uids.clear()  # the server recovers
    summary_two = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox
    )

    rows_after = await held_rows(session_factory, message_id)
    assert len(rows_after) == 1  # skip-if-exists: no duplicate row
    assert rows_after[0].id == rows[0].id
    assert mailbox.moved == [("142", HELD_FOLDER)]
    assert summary_two.messages_held == 1


async def test_duplicate_only_email_still_processed_not_held(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # An email whose every item is already in the library files to Processed —
    # a re-send is not review-worthy (the content is safe), so no hold.
    tag = uuid.uuid4().hex[:8]
    name = f"dup-{tag}.pdf"
    content = make_pdf(tag)
    first = make_raw_mail(
        subject=f"first {tag}", attachments=[(name, content, "application", "pdf")]
    )
    mailbox_one = FakeMailBox([mail_message(first, uid="143")])
    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox_one)

    message_id = f"<{uuid.uuid4().hex}@example.com>"
    resend = make_raw_mail(
        subject=f"resend {tag}",
        message_id=message_id,
        attachments=[(name, content, "application", "pdf")],
    )
    mailbox_two = FakeMailBox([mail_message(resend, uid="144")])

    summary = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox_two
    )

    assert await held_rows(session_factory, message_id) == []
    assert mailbox_two.moved == [("144", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_duplicate=1
    )


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


async def test_skip_trace_row_written_when_everything_filtered(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # W4: an email whose ONLY attachment is a quiet noise skip (inline signature
    # logo) and whose body is below substance produces ZERO documents — the
    # durable email_selection_traces row is then the only discoverable audit of
    # the skip. Hold disabled so the message actually processes (a HELD email's
    # audit lives on its held_emails row instead).
    poll_settings = Settings(email_host="imap.example.test", email_hold_enabled=False)
    tag = uuid.uuid4().hex[:8]
    subject = f"skiptrace-all {tag}"
    raw = make_inline_image_mail(subject=subject, image=png_bytes((200, 200)))
    mailbox = FakeMailBox([mail_message(raw, uid="70")])

    summary = await poll_mailbox_async(
        poll_settings, session_factory, mailbox_factory=lambda: mailbox
    )

    assert summary.messages_processed == 1
    assert summary.attachments_ingested == 0
    assert summary.attachments_filtered == 1
    rows = await skip_trace_rows(session_factory, subject)
    assert len(rows) == 1
    row = rows[0]
    assert row.message_id  # provenance: the row is findable without the logs
    assert row.from_address == "john@example.com"
    assert row.created_at is not None
    assert any(
        item["filename"] == "logo.png"
        and item["verdict"] == "filtered"
        and item["reason"] == "signature_image"
        for item in row.decisions
    )


async def test_skip_trace_row_and_selection_event_for_partial_skip(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # W4, doc-159 shape: one decoration logo filtered + one real PDF ingested.
    # BOTH audit surfaces exist — the per-document email_selection event
    # (unchanged) and the per-email skip-trace row carrying the FULL decision
    # list (the ingested sibling included, so the row reads as a whole email).
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"skiptrace-{tag}.pdf"
    subject = f"skiptrace-partial {tag}"
    raw = make_raw_mail(
        subject=subject,
        attachments=[
            ("image001.png", png_bytes((200, 200)), "image", "png"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="71")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 1
    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1
    events = await events_for(session_factory, documents[0].id, "email_selection")
    assert len(events) == 1  # the existing per-document event is unchanged
    rows = await skip_trace_rows(session_factory, subject)
    assert len(rows) == 1
    decisions = rows[0].decisions
    (logo_item,) = [item for item in decisions if item["filename"] == "image001.png"]
    assert logo_item["verdict"] == "filtered"
    assert logo_item["reason"] == "decoration_image"
    assert logo_item["detail"]  # the human sentence rides along
    assert any(item["filename"] == pdf_name and item["verdict"] == "ingested" for item in decisions)


async def test_no_skip_trace_row_when_nothing_skipped(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # W4 negative: an email with no filtered/dropped item writes NO trace row —
    # the body's bookkeeping "not_needed" decision must not count as a skip.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"clean-{tag}.pdf"
    subject = f"skiptrace-clean {tag}"
    raw = make_raw_mail(
        subject=subject, attachments=[(pdf_name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="72")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 0
    assert await skip_trace_rows(session_factory, subject) == []


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
    # Bias to ingest: byte size ALONE never drops a decodable image. An 800x600
    # scan with a non-decoration filename compresses below the decoration byte
    # ceiling, but that is its only decoration signal — so it is kept.
    tag = uuid.uuid4().hex[:8]
    img_name = f"scan-{tag}.png"
    raw = make_raw_mail(
        subject=f"scan {tag}", attachments=[(img_name, png_bytes((800, 600)), "image", "png")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="53")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [img_name]
    assert summary.attachments_filtered == 0
    assert summary.attachments_ingested == 1


async def test_decoration_logo_attachment_filtered_pdf_ingested(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Doc-159 repro: a forwarded mail carries a company logo as a REGULAR (not
    # inline) attachment — Outlook's image001.png, 200x200, a few KB — plus the
    # real PDF. The logo trips >= 2 decoration signals (filename + size + shape)
    # and is quietly filtered; the PDF ingests; the trace names the signals.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"forwarded-{tag}.pdf"
    logo = png_bytes((200, 200))  # ~doc-159's 6KB logo: small bytes, 200px, decodable
    raw = make_raw_mail(
        subject=f"deco {tag}",
        attachments=[
            ("image001.png", logo, "image", "png"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="55")])

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = await poll_mailbox_async(
            settings, session_factory, mailbox_factory=lambda: mailbox
        )

    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 1
    assert summary.attachments_dropped == 0  # quiet noise, never a user-facing drop
    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1
    assert await documents_named(session_factory, "image001.png") == []
    # The log trace records the quiet filter; the persisted trace names the
    # fired signals so the audit trail is self-explanatory.
    trace = _selection_trace_lines(caplog)[0]
    assert "image001.png:classify:filtered(decoration_image)" in trace
    events = await events_for(session_factory, documents[0].id, "email_selection")
    assert len(events) == 1
    (logo_item,) = [i for i in events[0].detail["items"] if i["filename"] == "image001.png"]
    assert logo_item["verdict"] == "filtered"
    assert logo_item["reason"] == "decoration_image"
    for signal in ("filename", "size", "shape"):
        assert signal in logo_item["detail"]


def test_decoration_size_signal_alone_ingests(settings: Settings) -> None:
    # Single-signal negative: an 800x600 photo with a plain filename compresses
    # below the decoration byte ceiling — size is its ONLY signal, so it ingests.
    tag = uuid.uuid4().hex[:8]
    img_name = f"photo-{tag}.png"
    raw = make_raw_mail(
        subject=f"photo {tag}", attachments=[(img_name, png_bytes((800, 600)), "image", "png")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="56")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [img_name]
    assert summary.attachments_filtered == 0


def test_decoration_filename_signal_alone_ingests(settings: Settings) -> None:
    # Single-signal negative: a large high-res "logo" — 800x600 and above the
    # byte ceiling — has ONLY the filename signal, so it ingests (a real scan
    # someone happened to name logo.png must never be lost).
    tag = uuid.uuid4().hex[:8]
    img_name = f"logo-{tag}.png"
    content = noisy_png_bytes((800, 600))
    assert len(content) > 65536  # the size signal is genuinely off
    raw = make_raw_mail(subject=f"biglogo {tag}", attachments=[(img_name, content, "image", "png")])
    mailbox = FakeMailBox([mail_message(raw, uid="57")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [img_name]
    assert summary.attachments_filtered == 0


def test_decoration_banner_shape_filtered(settings: Settings) -> None:
    # A 600x80 header strip: banner shape (>= 4:1, short edge <= 128px) plus
    # small bytes = two signals, so it is filtered; the sibling PDF ingests.
    # "header" is deliberately NOT a decoration word — the filename signal is off.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"real-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"banner {tag}",
        attachments=[
            ("header.png", png_bytes((600, 80)), "image", "png"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="58")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert [candidate.filename for candidate in calls] == [pdf_name]
    assert summary.attachments_filtered == 1


def test_decoration_signals_semantics() -> None:
    # The three signals, unit-tested pure: filename (decoration words + Outlook
    # imageNNN stems), size (<= byte ceiling), shape (small edge or banner).
    settings = Settings(email_host="imap.example.test")
    dims = (800, 600)  # shape signal off: long edge > 384, aspect < 4
    big = b"x" * 65537  # size signal off: one byte over the ceiling

    def signals(
        filename: str | None, content: bytes, dimensions: tuple[int, int] | None
    ) -> dict[str, bool]:
        return _decoration_signals(filename, content, dimensions, settings)

    # Filename signal: decoration words and Outlook auto-embed stems only.
    for name in ("image001.png", "image07.png", "Company-Logo.PNG", "email-signature.jpg"):
        assert signals(name, big, dims) == {"filename": True, "size": False, "shape": False}
    for name in ("image1.png", "image1234.png", "photo.png", "imagery.png", None):
        assert signals(name, big, dims) == {"filename": False, "size": False, "shape": False}
    # Size signal: at the ceiling fires, one byte over does not.
    assert signals("a.png", b"x" * 65536, dims)["size"] is True
    assert signals("a.png", big, dims)["size"] is False
    # Shape signal: small longest edge, or banner (>= 4:1 with short edge <= 128).
    assert signals("a.png", big, (384, 384))["shape"] is True
    assert signals("a.png", big, (385, 300))["shape"] is False
    assert signals("a.png", big, (600, 80))["shape"] is True  # banner
    assert signals("a.png", big, (600, 129))["shape"] is False  # too tall for a banner
    assert signals("a.png", big, (512, 128))["shape"] is True  # 4:1 exactly, short edge 128
    assert signals("a.png", big, None)["shape"] is False  # no dimensions, no shape claim


def test_noise_gate_disabled_ingests_everything() -> None:
    # The escape hatch: with the gate off, the inline pixel, the .ics, and the
    # decoration-shaped logo all reach ingest exactly as before the feature.
    settings = Settings(email_host="imap.example.test", email_filter_noise_enabled=False)
    tag = uuid.uuid4().hex[:8]
    raw = make_inline_image_mail(
        subject=f"off {tag}",
        image=png_bytes((1, 1)),
        extra=[
            ("invite.ics", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "text", "calendar"),
            ("image002.png", png_bytes((200, 200)), "image", "png"),  # decoration-shaped
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="54")])
    calls: list[IngestCandidate] = []

    summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    names = {candidate.filename for candidate in calls}
    assert "logo.png" in names  # the inline pixel is ingested when the gate is off
    assert "invite.ics" in names  # the calendar (text/plain) is ingested too
    assert "image002.png" in names  # the decoration image is ingested too
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


async def test_llm_noise_corroborated_by_one_signal_skips_image(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Doc-159 fix: the LLM says probably-noise about an image AND one
    # deterministic decoration signal agrees (an 800x600 solid PNG compresses
    # under the byte ceiling — size is its ONLY signal, so the deterministic
    # >=2-signal gate alone kept it). Corroborated verdicts SKIP instead of
    # ingest-and-flag; the sibling PDF is untouched.
    tag = uuid.uuid4().hex[:8]
    img_name = f"photo-{tag}.png"  # no decoration word; shape off (800 > 384, aspect < 4)
    pdf_name = f"invoice-{tag}.pdf"
    patched_anthropic.noise_markers.append("photo-")
    image = png_bytes((800, 600))
    assert len(image) <= 65536  # the size signal genuinely fires…
    raw = make_raw_mail(
        subject=f"corroborate {tag}",
        attachments=[
            (img_name, image, "image", "png"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="140")])

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = await poll_mailbox_async(
            label_settings(), session_factory, mailbox_factory=lambda: mailbox
        )

    assert summary.attachments_ingested == 1  # only the PDF
    assert summary.attachments_filtered == 1  # the corroborated skip is counted
    assert summary.attachments_dropped == 0  # quiet, never a user-facing drop
    assert await documents_named(session_factory, img_name) == []  # no document created
    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1
    assert documents[0].review_status is not ReviewStatus.NEEDS_REVIEW  # sibling unaffected
    trace = _selection_trace_lines(caplog)[0]
    assert f"{img_name}:llm_label:filtered(llm_noise_corroborated)" in trace
    events = await events_for(session_factory, documents[0].id, "email_selection")
    assert len(events) == 1
    (img_item,) = [i for i in events[0].detail["items"] if i["filename"] == img_name]
    assert img_item["verdict"] == "filtered"
    assert img_item["reason"] == "llm_noise_corroborated"
    assert "looks decorative" in img_item["detail"]  # the LLM's reasoning snippet…
    assert "size" in img_item["detail"]  # …plus the fired signal name


async def test_llm_noise_zero_signals_ingests_and_flags(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # The LLM says probably-noise but NO deterministic decoration signal agrees
    # (large noisy 800x600, non-decoration name): today's disposition is
    # unchanged — ingest-and-flag (email_item_ambiguous), never a skip.
    tag = uuid.uuid4().hex[:8]
    img_name = f"zerosig-{tag}.png"
    patched_anthropic.noise_markers.append("zerosig-")
    content = noisy_png_bytes((800, 600))
    assert len(content) > 65536  # the size signal is genuinely off
    raw = make_raw_mail(
        subject=f"zero signals {tag}", attachments=[(img_name, content, "image", "png")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="141")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 0
    documents = await documents_named(session_factory, img_name)
    assert len(documents) == 1
    document = documents[0]
    assert document.extra["email_selection"]["verdict"] == "probably_noise"
    assert document.review_status is ReviewStatus.NEEDS_REVIEW
    rules = [finding["rule"] for finding in document.extra["validation"]["findings"]]
    assert "email_item_ambiguous" in rules


def test_llm_noise_non_image_still_ingests_and_flags(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # A probably-noise NON-image is never corroborated (only images can be
    # decoration) even when its byte size would trip the size signal:
    # unchanged ingest-and-flag.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"smallpdf-{tag}.pdf"
    content = make_pdf(tag)
    assert len(content) <= 65536  # would fire the size signal, were it an image
    raw = make_raw_mail(
        subject=f"non-image {tag}", attachments=[(pdf_name, content, "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="142")])
    calls: list[IngestCandidate] = []

    def fake_label(request: object) -> LabelOutcome:
        verdicts = {
            item.index: ("probably_noise", "looks like marketing")  # type: ignore[attr-defined]
            for item in request.items  # type: ignore[attr-defined]
            if item.kind == "attachment"  # type: ignore[attr-defined]
        }
        return LabelOutcome(verdicts=verdicts, usage=None, skip_reason=None)

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(
            settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox, label=fake_label
        )

    assert [candidate.filename for candidate in calls] == [pdf_name]
    selection = calls[0].extra_document["email_selection"]  # type: ignore[index]
    assert selection["verdict"] == "probably_noise"
    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 0
    trace = _selection_trace_lines(caplog)[0]
    assert f"{pdf_name}:llm_label:flagged_ambiguous" in trace


def test_llm_noise_corroboration_off_with_noise_gate_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # email_filter_noise_enabled=False disables the corroborated skip along with
    # the deterministic gate: a decoration-shaped probably-noise image still
    # lands as ingest-and-flag, exactly the pre-gate behavior.
    settings = Settings(email_host="imap.example.test", email_filter_noise_enabled=False)
    tag = uuid.uuid4().hex[:8]
    img_name = f"logo-{tag}.png"  # filename + size + shape would all fire
    raw = make_raw_mail(
        subject=f"gate off {tag}",
        attachments=[(img_name, png_bytes((200, 200)), "image", "png")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="143")])
    calls: list[IngestCandidate] = []

    def fake_label(request: object) -> LabelOutcome:
        verdicts = {
            item.index: ("probably_noise", "looks decorative")  # type: ignore[attr-defined]
            for item in request.items  # type: ignore[attr-defined]
            if item.kind == "attachment"  # type: ignore[attr-defined]
        }
        return LabelOutcome(verdicts=verdicts, usage=None, skip_reason=None)

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(
            settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox, label=fake_label
        )

    assert [candidate.filename for candidate in calls] == [img_name]
    selection = calls[0].extra_document["email_selection"]  # type: ignore[index]
    assert selection["verdict"] == "probably_noise"
    assert summary.attachments_ingested == 1
    assert summary.attachments_filtered == 0
    trace = _selection_trace_lines(caplog)[0]
    assert f"{img_name}:llm_label:flagged_ambiguous" in trace


async def test_flagged_document_needs_review_at_ingest_without_extraction(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A probably_noise label verdict must surface as needs_review AT INGEST —
    # extraction never runs here (jobs stay parked in the InMemoryConnector),
    # so the flag cannot depend on the extraction pipeline. Regression for the
    # flag being invisible while LIBRARY_EXTRACTION_ENABLED=false.
    settings = Settings(
        email_host="imap.example.test",
        email_label_enabled=True,
        anthropic_api_key="test-key",  # type: ignore[arg-type]  # env form
    )
    tag = uuid.uuid4().hex[:8]
    name = f"noise-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"flagged {tag}", attachments=[(name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="80")])

    async def fake_label(
        session: object,
        client: object,
        label_settings: object,
        *,
        subject: str | None,
        sender: str | None,
        body_snippet: str,
        items: list[object],
    ) -> LabelOutcome:
        verdicts = {
            item.index: ("probably_noise", "looks like marketing")  # type: ignore[attr-defined]
            for item in items
        }
        return LabelOutcome(verdicts=verdicts, usage=None, skip_reason=None)

    monkeypatch.setattr("library.email_ingest.label_email_items", fake_label)

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    document = documents[0]
    assert document.review_status is ReviewStatus.NEEDS_REVIEW
    validation = document.extra["validation"]
    assert validation["prompt_version"]
    assert validation["validated_at"]
    rules = [finding["rule"] for finding in validation["findings"]]
    assert rules == ["email_item_ambiguous"]
    assert "looks like marketing" in validation["findings"][0]["message"]
    # Extraction genuinely never ran: no extraction output on the document, and
    # the pipeline job is still waiting (nothing executes InMemoryConnector jobs).
    assert document.extra.get("extraction") is None
    process_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.process_document"
        and job["args"] == {"document_id": document.id}
    ]
    assert len(process_jobs) == 1


async def test_dropped_sibling_sets_needs_review_at_ingest(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # The surviving attachment of a mixed email carries its dropped sibling and
    # must be flagged needs_review at ingest, before/without extraction.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"survivor-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"mixed ingest {tag}",
        attachments=[
            (
                "rejected.docx",
                b"\x00\xffPK-not-really" + uuid.uuid4().bytes,
                "application",
                "msword",
            ),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="81")])

    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, pdf_name)
    assert len(documents) == 1
    document = documents[0]
    assert document.review_status is ReviewStatus.NEEDS_REVIEW
    findings = document.extra["validation"]["findings"]
    dropped = [f for f in findings if f["rule"] == "email_attachments_dropped"]
    assert len(dropped) == 1
    assert "rejected.docx" in dropped[0]["message"]


async def test_concurrent_same_content_ingest_returns_duplicate(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The dedup race: both ingests pass the sha256 pre-check, one wins the
    # insert. Deterministic reproduction: the test session's flush first lands
    # a conflicting row via a second session, then delegates to the real flush,
    # which hits the unique constraint. The loser must recover into a normal
    # duplicate result, not an error.
    content = make_pdf()
    sha256 = hashlib.sha256(content).hexdigest()
    name = f"race-{uuid.uuid4().hex[:8]}.pdf"

    async with session_factory() as session:
        real_flush = session.flush
        raced = False

        async def racing_flush(*args: object, **kwargs: object) -> None:
            nonlocal raced
            if not raced:
                raced = True
                async with session_factory() as rival:
                    await ingest_file(
                        rival, content=content, filename=name, source=DocumentSource.EMAIL
                    )
            await real_flush(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(session, "flush", racing_flush)
        result = await ingest_file(
            session, content=content, filename=name, source=DocumentSource.EMAIL
        )

    assert result.duplicate is True
    async with session_factory() as check:
        rows = list(
            (await check.execute(select(Document).where(Document.sha256 == sha256))).scalars().all()
        )
    assert len(rows) == 1
    assert result.document.id == rows[0].id
    duplicate_events = await events_for(session_factory, rows[0].id, "duplicate_upload")
    assert len(duplicate_events) == 1
    assert duplicate_events[0].detail["filename"] == name


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
    # Overlap guard: at most one queued poll (queueing_lock — the periodic
    # deferrer skips AlreadyEnqueued ticks) and never two running at once (lock).
    assert periodic.task.queueing_lock == "poll_email_inbox"
    assert periodic.task.lock == "poll_email_inbox"


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
    notifications: dict[str, object] | None = None,
) -> int:
    from library.models import User

    async with session_factory() as session:
        notification_prefs: dict[str, object] = {}
        if forward_addresses is not None:
            notification_prefs["email_forward_addresses"] = forward_addresses
        if notifications is not None:
            notification_prefs.update(notifications)
        prefs: dict[str, object] = (
            {"notifications": notification_prefs} if notification_prefs else {}
        )
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


# --- IMAP socket timeout + bounded loop bridges (W3) ---


def test_connect_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # The configured socket timeout must reach the MailBox constructor (keyword),
    # where it bounds connect and every subsequent IMAP command.
    captured: dict[str, object] = {}

    class SpyMailBox:
        def __init__(self, host: str, port: int, *, timeout: float | None = None) -> None:
            captured.update(host=host, port=port, timeout=timeout)

        def login(self, username: str, password: str, initial_folder: str) -> "SpyMailBox":
            captured.update(username=username, initial_folder=initial_folder)
            return self

    monkeypatch.setattr("library.email_ingest.MailBox", SpyMailBox)
    settings = Settings(
        email_host="imap.example.test",
        email_username="library@example.test",  # type: ignore[arg-type]  # env form
        email_password="app-password",  # type: ignore[arg-type]  # env form
        email_imap_timeout_seconds=17.5,
    )

    _connect(settings)

    assert captured["timeout"] == 17.5
    assert captured["host"] == "imap.example.test"
    assert captured["port"] == 993
    assert captured["username"] == "library@example.test"
    assert captured["initial_folder"] == "INBOX"


def test_connect_requires_host_and_credentials() -> None:
    with pytest.raises(ValueError, match="LIBRARY_EMAIL_HOST"):
        _connect(Settings(email_host=None))
    with pytest.raises(ValueError, match="LIBRARY_EMAIL_USERNAME/LIBRARY_EMAIL_PASSWORD"):
        _connect(Settings(email_host="imap.example.test"))


def test_poll_survives_mailbox_factory_raising_oserror(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # A dead/wedged server (socket timeout surfaces as OSError) aborts the poll
    # with a WARNING and an empty summary — never an exception out of the task.
    def broken_factory() -> FakeMailBox:
        raise OSError("connection reset by peer")

    with caplog.at_level(logging.WARNING, logger="library.email_ingest"):
        summary = poll_mailbox(
            settings,
            ingest=lambda candidate: pytest.fail("must not ingest"),
            mailbox_factory=broken_factory,
        )

    assert summary == EmailPollSummary()
    assert any("poll aborted" in record.getMessage() for record in caplog.records)


async def test_label_bridge_timeout_fails_open(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A label call that never completes on the loop must not wedge the poll
    # thread: the bridge times out and fails open — the attachment is ingested
    # unflagged and the message is still moved.
    settings = label_settings()
    tag = uuid.uuid4().hex[:8]
    name = f"slowlabel-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"slow label {tag}", attachments=[(name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="90")])

    async def hang(*args: object, **kwargs: object) -> LabelOutcome:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr("library.email_ingest._label_email_on_loop", hang)
    # Generous enough that a REAL bridge call (the ingest) never trips it under
    # full-suite load, while the 30 s hang above still does.
    monkeypatch.setattr("library.email_ingest._LOOP_BRIDGE_TIMEOUT_SECONDS", 2.0)

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1  # fail-open: still ingested…
    assert documents[0].extra.get("email_selection") is None  # …and unflagged
    assert documents[0].review_status is not ReviewStatus.NEEDS_REVIEW
    assert mailbox.moved == [("90", PROCESSED_FOLDER)]
    assert summary.attachments_ingested == 1
    assert summary.messages_processed == 1


async def test_notify_bridge_timeout_does_not_wedge_message(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The drop notification is best-effort by contract: a push that never
    # completes on the loop times out with a WARNING and the message is still
    # processed and moved.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"good-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"stuck notify {tag}",
        attachments=[
            ("bad.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="91")])

    async def hang(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr("library.email_ingest._notify_dropped", hang)
    # Generous enough that a REAL bridge call (the ingest) never trips it under
    # full-suite load, while the 30 s hang above still does.
    monkeypatch.setattr("library.email_ingest._LOOP_BRIDGE_TIMEOUT_SECONDS", 2.0)

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert len(await documents_named(session_factory, pdf_name)) == 1
    assert mailbox.moved == [("91", PROCESSED_FOLDER)]
    assert summary.messages_processed == 1
    assert summary.attachments_dropped == 1


# --- Label budget integrity (W5) ---


async def test_label_budget_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # One labelled poll records its spend; a second poll whose budget is at or
    # below today's spend must observably skip the labeller (zero client calls,
    # attachment ingested unflagged). The DB is shared across tests, so only the
    # spend DELTA is asserted, never an absolute total.
    tag = uuid.uuid4().hex[:8]
    name = f"budget-{tag}.pdf"
    patched_anthropic.noise_markers.append("budget-")  # a labelled poll observably flags
    raw = make_raw_mail(
        subject=f"budget {tag}", attachments=[(name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="100")])
    async with session_factory() as session:
        spend_before = await todays_spend_usd(session, "email_label_completed")

    await poll_mailbox_async(label_settings(), session_factory, mailbox_factory=lambda: mailbox)

    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].extra["email_selection"]["verdict"] == "probably_noise"  # labeller ran
    label_events = await events_for(session_factory, documents[0].id, "email_label_completed")
    assert len(label_events) == 1
    cost = label_events[0].detail["cost_usd"]
    assert cost > 0
    async with session_factory() as session:
        spend_after = await todays_spend_usd(session, "email_label_completed")
    assert spend_after - spend_before == pytest.approx(cost)
    assert len(patched_anthropic.calls) == 1

    # Second poll, budget below today's spend: the gate binds.
    name_two = f"budget-{tag}-second.pdf"
    raw_two = make_raw_mail(
        subject=f"budget second {tag}",
        attachments=[(name_two, make_pdf(f"{tag}-2"), "application", "pdf")],
    )
    mailbox_two = FakeMailBox([mail_message(raw_two, uid="101")])

    await poll_mailbox_async(
        label_settings(email_label_daily_budget_usd=spend_after / 2),
        session_factory,
        mailbox_factory=lambda: mailbox_two,
    )

    assert len(patched_anthropic.calls) == 1  # no new call — the budget gate bound
    documents_two = await documents_named(session_factory, name_two)
    assert len(documents_two) == 1  # fail-open: still ingested…
    assert documents_two[0].extra.get("email_selection") is None  # …and unflagged
    assert documents_two[0].review_status is not ReviewStatus.NEEDS_REVIEW


async def test_held_email_spend_counts_toward_label_budget(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A held email's label call is billed into the held row's trace, not an
    # email_label_completed event — the budget gate must count it anyway, or a
    # stream of held newsletters could run the label pass indefinitely past the
    # cap. The DB is shared across tests, so only spend DELTAS are asserted.
    patched_anthropic.email_verdict = "hold"
    patched_anthropic.email_reason = "newsletter blast"
    tag = uuid.uuid4().hex[:8]
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=f"held budget {tag}",
        message_id=message_id,
        attachments=[(f"digest-{tag}.pdf", make_pdf(tag), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="160")])

    await poll_mailbox_async(label_settings(), session_factory, mailbox_factory=lambda: mailbox)

    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    held_cost = rows[0].trace["label_usage"]["cost_usd"]
    assert held_cost > 0
    assert len(patched_anthropic.calls) == 1
    async with session_factory() as session:
        filed_spend = await todays_spend_usd(session, "email_label_completed")

    # Poll 2, fresh mail. The budget sits ABOVE today's filed (event) spend but
    # BELOW filed + held spend: the gate binds only if held spend counts.
    patched_anthropic.email_verdict = "file"
    patched_anthropic.email_reason = None
    patched_anthropic.noise_markers.append("heldbudget-")  # a labelled poll would flag
    name_two = f"heldbudget-{tag}.pdf"
    raw_two = make_raw_mail(
        subject=f"held budget second {tag}",
        attachments=[(name_two, make_pdf(f"{tag}-2"), "application", "pdf")],
    )
    mailbox_two = FakeMailBox([mail_message(raw_two, uid="161")])

    with caplog.at_level(logging.INFO, logger="library.email_label"):
        await poll_mailbox_async(
            label_settings(email_label_daily_budget_usd=filed_spend + held_cost / 2),
            session_factory,
            mailbox_factory=lambda: mailbox_two,
        )

    assert len(patched_anthropic.calls) == 1  # no new call — the budget gate bound
    assert any("daily budget" in record.message for record in caplog.records)  # budget skip
    documents_two = await documents_named(session_factory, name_two)
    assert len(documents_two) == 1  # fail-open: still ingested…
    assert documents_two[0].extra.get("email_selection") is None  # …and unflagged
    assert documents_two[0].review_status is not ReviewStatus.NEEDS_REVIEW


async def test_label_spend_recorded_on_all_duplicate_resend(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # Poll 1 (labeller off) files the document. Poll 2 re-sends the same bytes
    # with the labeller on: everything is a duplicate, yet the call cost money —
    # the spend event must anchor on the existing (duplicate) document instead
    # of vanishing (the old deliberate under-count).
    tag = uuid.uuid4().hex[:8]
    name = f"resend-{tag}.pdf"
    content = make_pdf(tag)
    first = make_raw_mail(
        subject=f"first {tag}", attachments=[(name, content, "application", "pdf")]
    )
    mailbox_one = FakeMailBox([mail_message(first, uid="110")])
    await poll_mailbox_async(
        Settings(email_host="imap.example.test"),
        session_factory,
        mailbox_factory=lambda: mailbox_one,
    )
    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    anchor = documents[0]
    assert await events_for(session_factory, anchor.id, "email_label_completed") == []
    selection_before = len(await events_for(session_factory, anchor.id, "email_selection"))

    resend = make_raw_mail(
        subject=f"resend {tag}", attachments=[(f"copy-{name}", content, "application", "pdf")]
    )
    mailbox_two = FakeMailBox([mail_message(resend, uid="111")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox_two
    )

    assert summary.attachments_duplicate == 1
    assert summary.attachments_ingested == 0
    assert len(patched_anthropic.calls) == 1
    label_events = await events_for(session_factory, anchor.id, "email_label_completed")
    assert len(label_events) == 1
    assert label_events[0].detail["cost_usd"] > 0
    # No new document, so no NEW email_selection event — that still needs a new
    # row (poll 1 already wrote the anchor's own).
    selection_after = len(await events_for(session_factory, anchor.id, "email_selection"))
    assert selection_after == selection_before


# --- Production label wiring + uncovered branches (W7) ---


async def test_label_wiring_end_to_end_under_poll_mailbox_async(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # The real production wiring: poll_mailbox_async constructs the Anthropic
    # client (faked), holds it open for the whole poll (AsyncExitStack), and the
    # label verdicts flow through to the produced documents and budget event.
    tag = uuid.uuid4().hex[:8]
    keep_name = f"invoice-{tag}.pdf"
    flag_name = f"banner-{tag}.pdf"
    patched_anthropic.noise_markers.append("banner-")
    raw = make_raw_mail(
        subject=f"wired {tag}",
        attachments=[
            (keep_name, make_pdf(f"{tag}-a"), "application", "pdf"),
            (flag_name, make_pdf(f"{tag}-b"), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="120")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    assert summary.attachments_ingested == 2  # the label never drops
    assert patched_anthropic.api_keys == ["test-key"]
    assert patched_anthropic.entered == 1 and patched_anthropic.exited == 1
    assert len(patched_anthropic.calls) == 1
    flagged = (await documents_named(session_factory, flag_name))[0]
    assert flagged.extra["email_selection"] == {
        "verdict": "probably_noise",
        "reason": "looks decorative",
        "source": "llm_label",
    }
    kept = (await documents_named(session_factory, keep_name))[0]
    assert kept.extra.get("email_selection") is None
    # The budget event landed on the anchor (first produced) document.
    label_events = await events_for(session_factory, kept.id, "email_label_completed")
    assert len(label_events) == 1
    assert label_events[0].detail["cost_usd"] > 0
    assert label_events[0].detail["model"] == "claude-haiku-4-5"


async def test_label_not_wired_without_api_key(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # email_label_enabled=True but no API key: the client must never be
    # constructed and everything ingests unflagged.
    settings = Settings(email_host="imap.example.test", email_label_enabled=True)
    tag = uuid.uuid4().hex[:8]
    name = f"nokey-{tag}.pdf"
    patched_anthropic.noise_markers.append("nokey-")  # would flag, were it called
    raw = make_raw_mail(
        subject=f"no key {tag}", attachments=[(name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="121")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert patched_anthropic.api_keys == []  # constructor never called
    assert patched_anthropic.calls == []
    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    assert documents[0].extra.get("email_selection") is None
    assert documents[0].review_status is not ReviewStatus.NEEDS_REVIEW
    assert summary.attachments_ingested == 1


async def test_notify_dropped_integration(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The full async notify path (_notify_dropped): the sender is resolved to
    # the owning user and the dispatcher is called once with the drop.
    tag = uuid.uuid4().hex[:8]
    sender = f"jane-{tag}@example.org"
    owner_id = await _make_user(
        session_factory, username=f"notify-{tag}", forward_addresses=[sender]
    )
    pdf_name = f"kept-{tag}.pdf"
    raw = make_raw_mail(
        from_addr=f"Jane <{sender}>",
        subject=f"notify {tag}",
        attachments=[
            ("secret.docx", b"\x00\xffPK-nope" + uuid.uuid4().bytes, "application", "msword"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="122")])
    recorded: list[tuple[int | None, str | None, list[str | None]]] = []

    async def spy_dispatch(
        session_factory_: object,
        owner: int | None,
        *,
        subject: str | None,
        filenames: list[str | None],
        document_url_base: str | None,
    ) -> None:
        recorded.append((owner, subject, filenames))

    monkeypatch.setattr(
        "library.email_ingest.dispatch_attachments_dropped_notification", spy_dispatch
    )

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert recorded == [(owner_id, f"notify {tag}", ["secret.docx"])]
    assert summary.attachments_dropped == 1
    assert mailbox.moved == [("122", PROCESSED_FOLDER)]


def test_oversize_attachment_dropped_and_notified(caplog: pytest.LogCaptureFixture) -> None:
    # An attachment over max_upload_bytes is a user-facing drop: recorded with
    # reason "oversize", counted, and pushed to the owner. Hold disabled: with
    # holds on, a drops-only email is held instead (test_nothing_ingested_*);
    # this test pins the notify path itself.
    settings = Settings(
        email_host="imap.example.test", max_upload_bytes=512, email_hold_enabled=False
    )
    tag = uuid.uuid4().hex[:8]
    big_name = f"huge-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"oversize {tag}",
        attachments=[(big_name, make_pdf(tag) + b"\x00" * 1024, "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="123")])
    calls: list[IngestCandidate] = []
    notified: list[list[str | None]] = []

    def spy_notify(
        sender: str | None, subject: str | None, skipped: list[SkippedAttachment]
    ) -> None:
        notified.append([item.filename for item in skipped])

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(
            settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox, notify=spy_notify
        )

    assert calls == []  # nothing reached ingest
    assert summary.attachments_dropped == 1
    assert notified == [[big_name]]
    assert mailbox.moved == [("123", PROCESSED_FOLDER)]
    assert f"{big_name}:classify:dropped(oversize)" in _selection_trace_lines(caplog)[0]


def test_oversize_body_skipped_with_trace_reason(caplog: pytest.LogCaptureFixture) -> None:
    # A body that clears the substance gate but exceeds max_upload_bytes is
    # skipped with the oversize reason in the trace, and the message still moves.
    settings = Settings(email_host="imap.example.test", max_upload_bytes=100)
    tag = uuid.uuid4().hex[:8]
    raw = make_body_mail(subject=f"big body {tag}", text=long_body(tag))
    mailbox = FakeMailBox([mail_message(raw, uid="124")])
    calls: list[IngestCandidate] = []

    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = poll_mailbox(settings, _recording_ingest(calls), mailbox_factory=lambda: mailbox)

    assert calls == []
    assert summary == EmailPollSummary(messages_seen=1, messages_processed=1)
    assert mailbox.moved == [("124", PROCESSED_FOLDER)]
    assert "<body>:body_substance:filtered(oversize)" in _selection_trace_lines(caplog)[0]


async def _soft_delete(session_factory: async_sessionmaker[AsyncSession], document_id: int) -> None:
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        document.deleted_at = datetime.now(UTC)
        await session.commit()


async def test_deleted_duplicate_attachment_dropped_not_recreated(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    # Re-forwarding content whose document was soft-deleted must not recreate
    # it: ingest raises DeletedDuplicateError (an IngestError), recorded as a
    # dropped attachment while the message still completes. Hold disabled to
    # pin the not-recreated semantics; with holds on such an email is held
    # (its drop is user-facing and nothing ingested).
    settings = Settings(email_host="imap.example.test", email_hold_enabled=False)
    tag = uuid.uuid4().hex[:8]
    name = f"deleted-{tag}.pdf"
    content = make_pdf(tag)
    first = make_raw_mail(
        subject=f"orig {tag}", attachments=[(name, content, "application", "pdf")]
    )
    mailbox_one = FakeMailBox([mail_message(first, uid="125")])
    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox_one)
    documents = await documents_named(session_factory, name)
    assert len(documents) == 1
    await _soft_delete(session_factory, documents[0].id)

    resend = make_raw_mail(
        subject=f"resend {tag}", attachments=[(name, content, "application", "pdf")]
    )
    mailbox_two = FakeMailBox([mail_message(resend, uid="126")])

    summary = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox_two
    )

    assert len(await documents_named(session_factory, name)) == 1  # not recreated
    assert summary.attachments_ingested == 0
    assert summary.attachments_duplicate == 0
    assert summary.attachments_dropped == 1  # surfaced as an error drop
    assert mailbox_two.moved == [("126", PROCESSED_FOLDER)]


async def test_deleted_duplicate_body_dropped_not_recreated(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The body variant of the deleted-duplicate rejection: the body ingest's
    # IngestError branch records a dropped body decision and the message moves.
    tag = uuid.uuid4().hex[:8]
    subject = f"deleted body {tag}"
    text = long_body(tag)
    mailbox_one = FakeMailBox([mail_message(make_body_mail(subject=subject, text=text), uid="127")])
    await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox_one)
    documents = await documents_named(session_factory, f"{subject}.txt")
    assert len(documents) == 1
    await _soft_delete(session_factory, documents[0].id)

    mailbox_two = FakeMailBox([mail_message(make_body_mail(subject=subject, text=text), uid="128")])
    with caplog.at_level(logging.INFO, logger="library.email_ingest"):
        summary = await poll_mailbox_async(
            settings, session_factory, mailbox_factory=lambda: mailbox_two
        )

    assert len(await documents_named(session_factory, f"{subject}.txt")) == 1  # not recreated
    assert summary.attachments_ingested == 0
    assert mailbox_two.moved == [("128", PROCESSED_FOLDER)]
    assert f"{subject}.txt:body_substance:dropped(rejected)" in _selection_trace_lines(caplog)[0]


# --- Notify only after a successful move (W6) ---


async def test_move_failure_message_retried_without_duplicates_or_repeat_notification(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A move that fails once must leave the message for the next poll WITHOUT
    # notifying (the retry would notify again). The retry re-ingests as
    # duplicates (no new rows) and sends the one and only push after its move.
    tag = uuid.uuid4().hex[:8]
    pdf_name = f"retrymove-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"retry move {tag}",
        attachments=[
            ("bad.docx", b"\x00\xffPK-nope" + tag.encode(), "application", "msword"),
            (pdf_name, make_pdf(tag), "application", "pdf"),
        ],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="130")], fail_move_uids={"130"})
    recorded: list[tuple[str | None, list[str | None]]] = []

    async def spy_dispatch(
        session_factory_: object,
        owner: int | None,
        *,
        subject: str | None,
        filenames: list[str | None],
        document_url_base: str | None,
    ) -> None:
        recorded.append((subject, filenames))

    monkeypatch.setattr(
        "library.email_ingest.dispatch_attachments_dropped_notification", spy_dispatch
    )

    with caplog.at_level(logging.ERROR, logger="library.email_ingest"):
        summary_one = await poll_mailbox_async(
            settings, session_factory, mailbox_factory=lambda: mailbox
        )

    assert len(await documents_named(session_factory, pdf_name)) == 1  # ingested
    assert recorded == []  # NO notification before a successful move
    assert mailbox.moved == []
    assert len(mailbox.messages) == 1  # message stays for the next poll
    assert summary_one.messages_skipped == 1
    assert summary_one.messages_processed == 0
    assert any("will be reprocessed next poll" in record.getMessage() for record in caplog.records)

    mailbox.fail_move_uids.clear()  # the server recovers
    summary_two = await poll_mailbox_async(
        settings, session_factory, mailbox_factory=lambda: mailbox
    )

    assert len(await documents_named(session_factory, pdf_name)) == 1  # no duplicate row
    assert summary_two.attachments_ingested == 0
    assert summary_two.attachments_duplicate == 1
    assert summary_two.messages_processed == 1
    assert mailbox.moved == [("130", PROCESSED_FOLDER)]
    assert len(recorded) == 1  # exactly one notification across both polls
    assert recorded[0] == (f"retry move {tag}", ["bad.docx"])


# --- Message-level LLM evaluation + llm_hold (W11) ---


async def test_llm_hold_holds_email_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # The LLM's whole-email "hold" verdict: nothing is ingested at all, the
    # durable row carries the LLM's reason AND the label billing (there is no
    # document to anchor the budget event on), and the message moves to Held.
    patched_anthropic.email_verdict = "hold"
    patched_anthropic.email_reason = "newsletter blast"
    tag = uuid.uuid4().hex[:8]
    name = f"newsletter-{tag}.pdf"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_raw_mail(
        subject=f"weekly digest {tag}",
        message_id=message_id,
        attachments=[(name, make_pdf(tag), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="150")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    assert await documents_named(session_factory, name) == []  # zero ingests
    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].verdict == "llm_hold"
    assert rows[0].reason == "newsletter blast"
    assert rows[0].status is HeldEmailStatus.HELD
    # The label pass billed; its usage rides in the held row's trace.
    label_usage = rows[0].trace["label_usage"]
    assert label_usage["cost_usd"] > 0
    assert label_usage["model"] == "claude-haiku-4-5"
    assert label_usage["prompt_version"] == "email-label-v2"
    items = rows[0].trace["items"]
    assert any(
        item["kind"] == "email" and item["stage"] == "email_verdict" and item["verdict"] == "held"
        for item in items
    )
    assert len(patched_anthropic.calls) == 1
    assert mailbox.moved == [("150", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)


def test_llm_hold_makes_zero_ingest_calls(settings: Settings) -> None:
    # The hold branch runs BEFORE any ingest: the ingest callable is never
    # invoked for a held email (sync wiring, recording fakes).
    tag = uuid.uuid4().hex[:8]
    raw = make_raw_mail(
        subject=f"held outright {tag}",
        attachments=[(f"promo-{tag}.pdf", make_pdf(tag), "application", "pdf")],
    )
    mailbox = FakeMailBox([mail_message(raw, uid="151")])
    calls: list[IngestCandidate] = []
    holds: list[HoldRecord] = []

    def fake_label(request: object) -> LabelOutcome:
        return LabelOutcome({}, None, None, email_verdict="hold", email_reason="marketing")

    summary = poll_mailbox(
        settings,
        _recording_ingest(calls),
        mailbox_factory=lambda: mailbox,
        label=fake_label,
        persist_hold=holds.append,
    )

    assert calls == []  # nothing reached ingest
    assert len(holds) == 1
    assert holds[0].verdict == "llm_hold"
    assert holds[0].reason == "marketing"
    assert holds[0].imap_uid == "151"
    assert mailbox.moved == [("151", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)


def test_llm_hold_verdict_ignored_when_hold_disabled() -> None:
    # Rollback lever vs the LLM verdict: with holds off, a "hold" response is
    # inert — the email ingests and files exactly as today.
    settings = Settings(email_host="imap.example.test", email_hold_enabled=False)
    tag = uuid.uuid4().hex[:8]
    name = f"promo-{tag}.pdf"
    raw = make_raw_mail(
        subject=f"held? {tag}", attachments=[(name, make_pdf(tag), "application", "pdf")]
    )
    mailbox = FakeMailBox([mail_message(raw, uid="152")])
    calls: list[IngestCandidate] = []
    holds: list[HoldRecord] = []

    def fake_label(request: object) -> LabelOutcome:
        verdicts = {item.index: ("keep", None) for item in request.items}  # type: ignore[attr-defined]
        return LabelOutcome(verdicts, None, None, email_verdict="hold", email_reason="marketing")

    summary = poll_mailbox(
        settings,
        _recording_ingest(calls),
        mailbox_factory=lambda: mailbox,
        label=fake_label,
        persist_hold=holds.append,
    )

    assert [c.filename for c in calls] == [name]
    assert holds == []
    assert mailbox.moved == [("152", PROCESSED_FOLDER)]
    assert summary == EmailPollSummary(
        messages_seen=1, messages_processed=1, attachments_ingested=1
    )


def test_label_error_on_would_be_held_email_ingests_as_today(settings: Settings) -> None:
    # The loss-proof: a label failure fails open to email_verdict="file", so a
    # newsletter-shaped body-only email that WOULD have been held ingests
    # exactly as today — an outage can never hold (or lose) mail.
    tag = uuid.uuid4().hex[:8]
    subject = f"digest {tag}"
    raw = make_body_mail(subject=subject, text=long_body(tag))
    mailbox = FakeMailBox([mail_message(raw, uid="153")])
    calls: list[IngestCandidate] = []
    holds: list[HoldRecord] = []

    def failing_label(request: object) -> LabelOutcome:
        return LabelOutcome({}, None, "error")  # the fail-open shape the bridge returns

    summary = poll_mailbox(
        settings,
        _recording_ingest(calls),
        mailbox_factory=lambda: mailbox,
        label=failing_label,
        persist_hold=holds.append,
    )

    assert [c.filename for c in calls] == [f"{subject}.txt"]  # the body filed
    assert holds == []
    assert mailbox.moved == [("153", PROCESSED_FOLDER)]
    assert summary.attachments_ingested == 1
    assert summary.messages_held == 0


async def test_body_only_email_reaches_labeller_as_body_item(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # The label call is message-level: a body-only email (no attachments) is
    # judged too, with the body presented as a kind="body" manifest item.
    tag = uuid.uuid4().hex[:8]
    subject = f"body only label {tag}"
    raw = make_body_mail(subject=subject, text=long_body(tag))
    mailbox = FakeMailBox([mail_message(raw, uid="154")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    assert len(patched_anthropic.calls) == 1
    manifest = str(patched_anthropic.calls[0]["messages"][0]["content"])  # type: ignore[index]
    assert "kind=body" in manifest
    assert f"filename='{subject}.txt'" in manifest
    assert len(await documents_named(session_factory, f"{subject}.txt")) == 1
    assert summary.attachments_ingested == 1
    assert summary.messages_processed == 1


async def test_flagged_body_document_needs_review_at_ingest(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # A probably_noise verdict on the BODY item flags the body document exactly
    # like a flagged attachment: extra["email_selection"] stamped at creation →
    # needs_review at ingest (no extraction involved), but still ingested.
    tag = uuid.uuid4().hex[:8]
    subject = f"noisebody-{tag}"
    patched_anthropic.noise_markers.append("noisebody-")
    raw = make_body_mail(subject=subject, text=long_body(tag))
    mailbox = FakeMailBox([mail_message(raw, uid="155")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    documents = await documents_named(session_factory, f"{subject}.txt")
    assert len(documents) == 1  # the label never drops
    document = documents[0]
    assert document.extra["email_selection"] == {
        "verdict": "probably_noise",
        "reason": "looks decorative",
        "source": "llm_label",
    }
    assert document.review_status is ReviewStatus.NEEDS_REVIEW
    rules = [finding["rule"] for finding in document.extra["validation"]["findings"]]
    assert "email_item_ambiguous" in rules
    # The budget event anchors on the body document (the only produced one).
    label_events = await events_for(session_factory, document.id, "email_label_completed")
    assert len(label_events) == 1
    assert summary.attachments_ingested == 1


# --- Email-held push notification (W17) ---


_PUSHOVER_PREFS: dict[str, object] = {
    "enabled": True,
    "pushover_app_token": "app-token",
    "pushover_user_key": "user-key",
    "events": ["email_held"],
}


async def test_email_held_notification_fired_once_for_opted_in_owner(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hold fires exactly one push to the resolved owner, through the REAL
    # dispatcher (opt-in resolution included) — only the Pushover HTTP call is
    # faked, at the send_pushover boundary.
    from library import notifications

    tag = uuid.uuid4().hex[:8]
    sender = f"jane-{tag}@example.org"
    await _make_user(
        session_factory,
        username=f"held-push-{tag}",
        forward_addresses=[sender],
        notifications=_PUSHOVER_PREFS,
    )
    sends: list[dict[str, object]] = []

    async def fake_send(**kwargs: object) -> notifications.PushoverResult:
        sends.append(kwargs)
        return notifications.PushoverResult(ok=True, request_id="r")

    monkeypatch.setattr(notifications, "send_pushover", fake_send)
    subject = f"held push {tag}"
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        from_addr=f"Jane <{sender}>",
        subject=subject,
        text="FYI see attached",
        message_id=message_id,
    )
    mailbox = FakeMailBox([mail_message(raw, uid="170")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert summary.messages_held == 1
    assert len(await held_rows(session_factory, message_id)) == 1
    assert len(sends) == 1
    assert sends[0]["title"] == "Email held for review"
    assert subject in str(sends[0]["message"])


async def test_email_held_notification_not_sent_without_opt_in(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An owner who never opted into email_held gets nothing — the hold itself
    # is unaffected.
    from library import notifications

    tag = uuid.uuid4().hex[:8]
    sender = f"jane-{tag}@example.org"
    await _make_user(
        session_factory,
        username=f"held-nopush-{tag}",
        forward_addresses=[sender],
        notifications={**_PUSHOVER_PREFS, "events": ["processing_error"]},
    )
    sends: list[dict[str, object]] = []

    async def fake_send(**kwargs: object) -> notifications.PushoverResult:
        sends.append(kwargs)
        return notifications.PushoverResult(ok=True, request_id="r")

    monkeypatch.setattr(notifications, "send_pushover", fake_send)
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        from_addr=f"Jane <{sender}>",
        subject=f"held nopush {tag}",
        text="FYI see attached",
        message_id=message_id,
    )
    mailbox = FakeMailBox([mail_message(raw, uid="171")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    assert summary.messages_held == 1
    assert len(await held_rows(session_factory, message_id)) == 1
    assert sends == []


async def test_email_held_notification_failure_does_not_fail_hold(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A notification path that RAISES must never fail the hold: the row is
    # committed, the message still moves to Held, and the poll counts the hold.
    async def exploding_dispatch(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("pushover is down")

    monkeypatch.setattr("library.email_ingest.dispatch_email_held_notification", exploding_dispatch)
    tag = uuid.uuid4().hex[:8]
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        subject=f"held broken push {tag}", text="FYI see attached", message_id=message_id
    )
    mailbox = FakeMailBox([mail_message(raw, uid="172")])

    summary = await poll_mailbox_async(settings, session_factory, mailbox_factory=lambda: mailbox)

    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].status is HeldEmailStatus.HELD
    assert mailbox.moved == [("172", HELD_FOLDER)]
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)


async def test_below_substance_hold_records_label_usage(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
    patched_anthropic: FakeAnthropic,
) -> None:
    # A deterministic hold on an email the labeller also judged (and billed
    # for): the spend has no document to anchor on, so it rides in the held
    # row's trace — the below-substance body IS presented as a body item.
    tag = uuid.uuid4().hex[:8]
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    raw = make_body_mail(
        subject=f"thin but judged {tag}", text="FYI see attached", message_id=message_id
    )
    mailbox = FakeMailBox([mail_message(raw, uid="156")])

    summary = await poll_mailbox_async(
        label_settings(), session_factory, mailbox_factory=lambda: mailbox
    )

    assert len(patched_anthropic.calls) == 1  # the thin body was still judged
    manifest = str(patched_anthropic.calls[0]["messages"][0]["content"])  # type: ignore[index]
    assert "kind=body" in manifest
    rows = await held_rows(session_factory, message_id)
    assert len(rows) == 1
    assert rows[0].verdict == "below_substance"
    assert rows[0].trace["label_usage"]["cost_usd"] > 0
    assert summary == EmailPollSummary(messages_seen=1, messages_held=1)
