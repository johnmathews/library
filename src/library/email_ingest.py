"""Email-in ingestion: poll an IMAP mailbox and ingest its attachments.

A periodic Procrastinate task (``library.jobs.poll_email_inbox``) polls
``LIBRARY_EMAIL_FOLDER`` every ``LIBRARY_EMAIL_POLL_MINUTES`` minutes and
feeds every supported attachment through the same ``ingest_file`` service
as an upload (``source=email``, no uploader), attaching the sender,
subject, and Message-ID to the recorded ingestion event. v1 ingests
attachments only — body-only mails are filed away without creating a
document (see docs/ingestion.md, "Email-in").

Idempotency is folder-based: every fully processed message is moved to
``LIBRARY_EMAIL_PROCESSED_FOLDER`` (created on first use), so a message
is never scanned twice; content dedup in ``ingest_file`` (sha256) backs
that up if the same attachment arrives in a different mail. Per-message
errors are isolated — a broken mail is logged and left in place for the
next poll, and never aborts the run.

IMAP I/O is synchronous (imap-tools); the periodic task runs
``poll_mailbox`` in a worker thread via ``poll_mailbox_async`` while the
ingest calls themselves are marshalled back onto the event loop.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from imap_tools import MailBox, MailMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library.config import Settings
from library.ingest import (
    ALLOWED_MIME_TYPES,
    IngestError,
    IngestResult,
    detect_mime,
    ingest_file,
)
from library.models import DocumentSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailPollSummary:
    """Outcome of one mailbox poll."""

    messages_seen: int = 0
    messages_processed: int = 0  # moved to the processed folder
    messages_skipped: int = 0  # allowlist-rejected or errored; left in place
    attachments_ingested: int = 0  # new documents created
    attachments_duplicate: int = 0  # content already in the library


@dataclass(frozen=True, slots=True)
class AttachmentCandidate:
    """One supported attachment, ready for ingestion."""

    content: bytes
    filename: str | None
    mime: str | None
    event_detail: dict[str, object]


#: Synchronous bridge into ``ingest_file`` (poll_mailbox runs off-loop).
IngestCallable = Callable[[AttachmentCandidate], IngestResult]


class _FolderManagerProtocol(Protocol):
    def exists(self, folder: str) -> bool: ...

    def create(self, folder: str) -> Any: ...


class MailboxProtocol(Protocol):
    """The slice of ``imap_tools.MailBox`` the poller uses (fakeable in tests)."""

    folder: _FolderManagerProtocol

    def fetch(self, criteria: str = ..., *, mark_seen: bool = ...) -> Iterator[MailMessage]: ...

    def move(self, uid_list: str, destination_folder: str) -> Any: ...


def _connect(settings: Settings) -> AbstractContextManager[Any]:
    """Open a TLS IMAP connection and select the configured folder."""
    if settings.email_host is None:
        raise ValueError("LIBRARY_EMAIL_HOST is not configured")
    if settings.email_username is None or settings.email_password is None:
        raise ValueError("LIBRARY_EMAIL_USERNAME/LIBRARY_EMAIL_PASSWORD are not configured")
    return MailBox(settings.email_host, settings.email_port).login(
        settings.email_username.get_secret_value(),
        settings.email_password.get_secret_value(),
        initial_folder=settings.email_folder,
    )


def _message_id(message: MailMessage) -> str | None:
    """The raw Message-ID header, if present."""
    raw = message.obj["Message-ID"]
    return raw.strip() if raw else None


def _ingest_attachments(
    message: MailMessage, ingest: IngestCallable, max_bytes: int
) -> tuple[int, int]:
    """Ingest every supported attachment of one message.

    Returns ``(new_documents, duplicates)``. Unsupported or oversize
    attachments are skipped with a log line; content-level rejections
    (``IngestError``) skip the attachment without failing the message.
    Anything else (database down, ...) propagates so the caller leaves
    the message in place for the next poll.
    """
    detail: dict[str, object] = {
        "email_from": message.from_,
        "email_subject": message.subject,
        "email_message_id": _message_id(message),
    }
    new = duplicates = 0
    for attachment in message.attachments:
        content = attachment.payload
        if not content:
            continue
        if len(content) > max_bytes:
            logger.warning(
                "email: attachment %r is %d bytes (limit %d); skipped",
                attachment.filename,
                len(content),
                max_bytes,
            )
            continue
        mime = detect_mime(content, attachment.content_type)
        if mime not in ALLOWED_MIME_TYPES:
            logger.info(
                "email: attachment %r has unsupported type %s; skipped",
                attachment.filename,
                mime,
            )
            continue
        candidate = AttachmentCandidate(
            content=content,
            filename=attachment.filename or None,
            mime=attachment.content_type,
            event_detail=detail,
        )
        try:
            result = ingest(candidate)
        except IngestError as exc:
            logger.warning("email: attachment %r rejected (%s)", attachment.filename, exc)
            continue
        if result.duplicate:
            duplicates += 1
        else:
            new += 1
    return new, duplicates


def poll_mailbox(
    settings: Settings,
    ingest: IngestCallable,
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
) -> EmailPollSummary:
    """Poll the configured mailbox once and ingest attachment documents.

    Fetches every message in ``email_folder`` (``ALL``, not unseen-only —
    the seen flag is fragile when a human also reads the mailbox) and, per
    message: rejects senders outside ``email_allowed_senders`` (when
    non-empty; the mail stays in place, visible to the operator), ingests
    each supported attachment via ``ingest``, then moves the message to
    ``email_processed_folder`` — the move is what makes polling
    idempotent. Errors are isolated per message: the mail is left in
    place for the next poll and the run continues.

    No-op (empty summary) when ``email_host`` is unset. ``mailbox_factory``
    exists for tests; the default connects per ``settings``.
    """
    if settings.email_host is None:
        logger.debug("email: LIBRARY_EMAIL_HOST unset; poller disabled")
        return EmailPollSummary()
    factory = mailbox_factory or (lambda: _connect(settings))
    allowed = frozenset(settings.email_allowed_senders)
    seen = processed = skipped = ingested = duplicates = 0
    with factory() as mailbox:
        if not mailbox.folder.exists(settings.email_processed_folder):
            mailbox.folder.create(settings.email_processed_folder)
        # Materialise before moving: moving mid-fetch confuses some servers.
        messages = list(mailbox.fetch("ALL", mark_seen=False))
        for message in messages:
            seen += 1
            try:
                sender = (message.from_ or "").strip().lower()
                if allowed and sender not in allowed:
                    logger.warning(
                        "email: message %r from %r not in allowlist; left in place",
                        message.subject,
                        sender,
                    )
                    skipped += 1
                    continue
                new, dups = _ingest_attachments(message, ingest, settings.max_upload_bytes)
                ingested += new
                duplicates += dups
                mailbox.move(message.uid, settings.email_processed_folder)
                processed += 1
            except Exception:
                logger.exception(
                    "email: failed to process message %r; left in place for the next poll",
                    message.subject,
                )
                skipped += 1
    summary = EmailPollSummary(
        messages_seen=seen,
        messages_processed=processed,
        messages_skipped=skipped,
        attachments_ingested=ingested,
        attachments_duplicate=duplicates,
    )
    logger.info("email: poll finished: %s", summary)
    return summary


async def _ingest_candidate(
    session_factory: async_sessionmaker[AsyncSession], candidate: AttachmentCandidate
) -> IngestResult:
    async with session_factory() as session:
        return await ingest_file(
            session,
            content=candidate.content,
            filename=candidate.filename,
            mime=candidate.mime,
            source=DocumentSource.EMAIL,
            uploader_id=None,
            extra_event_detail=dict(candidate.event_detail),
        )


async def poll_mailbox_async(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
) -> EmailPollSummary:
    """Run ``poll_mailbox`` in a worker thread, ingesting on this event loop.

    The synchronous IMAP work must not block the Procrastinate worker, so
    it runs via ``asyncio.to_thread``; each ingest call is marshalled back
    onto the calling loop (``run_coroutine_threadsafe``) so the database
    session and job-queue connector stay on their home loop.
    """
    loop = asyncio.get_running_loop()

    def ingest_on_loop(candidate: AttachmentCandidate) -> IngestResult:
        future = asyncio.run_coroutine_threadsafe(
            _ingest_candidate(session_factory, candidate), loop
        )
        return future.result()

    return await asyncio.to_thread(
        poll_mailbox, settings, ingest_on_loop, mailbox_factory=mailbox_factory
    )
