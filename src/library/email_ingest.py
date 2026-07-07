"""Email-in ingestion: poll an IMAP mailbox and ingest its attachments.

A periodic Procrastinate task (``library.jobs.poll_email_inbox``) polls
``LIBRARY_EMAIL_FOLDER`` every ``LIBRARY_EMAIL_POLL_MINUTES`` minutes and
feeds every supported attachment through the same ``ingest_file`` service
as an upload (``source=email``; the uploader is resolved from the sender
via ``resolve_sender_owner``), attaching the sender, subject, and
Message-ID to the recorded ingestion event. When a message yields **no**
attachment document (none attached, or none of a supported type), the email
body itself is ingested instead — the HTML body converted to Markdown
(``text/markdown``), or the plain-text body (``text/plain``) when there is no
HTML — so "the email is the invoice" works too (see docs/ingestion.md,
"Email-in"). Only a truly empty-bodied mail is filed away without a document.

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
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Any, Protocol

from bs4 import BeautifulSoup
from imap_tools import MailBox, MailMessage
from markdownify import markdownify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library.config import Settings
from library.extraction.apply import match_user_by_email
from library.ingest import (
    ALLOWED_MIME_TYPES,
    IngestError,
    IngestResult,
    detect_mime,
    ingest_file,
)
from library.models import DocumentSource, User
from library.notifications import dispatch_attachments_dropped_notification

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailPollSummary:
    """Outcome of one mailbox poll."""

    messages_seen: int = 0
    messages_processed: int = 0  # moved to the processed folder
    messages_skipped: int = 0  # allowlist-rejected or errored; left in place
    attachments_ingested: int = 0  # new documents created
    attachments_duplicate: int = 0  # content already in the library
    attachments_dropped: int = 0  # rejected (oversize/unsupported/error); surfaced


@dataclass(frozen=True, slots=True)
class SkippedAttachment:
    """One attachment that could not be turned into a document.

    ``reason`` is a stable code: ``empty`` (no payload — usually inline cruft,
    not surfaced to the user), ``oversize``, ``unsupported_type``, or ``error``
    (an unexpected failure during ingest). ``detail`` is a human sentence.
    """

    filename: str | None
    reason: str
    detail: str


#: Drop reasons a human actually cares about — surfaced on the sibling document
#: (as a review reason) and in the "attachments couldn't be added" push. An
#: ``empty`` payload is logged and counted but deliberately not surfaced, so a
#: stray inline part never flags a real document.
_USER_FACING_DROP_REASONS: frozenset[str] = frozenset({"oversize", "unsupported_type", "error"})


def _dropped_siblings_payload(skipped: list[SkippedAttachment]) -> list[dict[str, str | None]]:
    """The user-facing subset of ``skipped``, shaped for storage/notification."""
    return [
        {"filename": item.filename, "reason": item.reason, "detail": item.detail}
        for item in skipped
        if item.reason in _USER_FACING_DROP_REASONS
    ]


@dataclass(frozen=True, slots=True)
class IngestCandidate:
    """One unit of email content ready for ingestion: an attachment or the body."""

    content: bytes
    filename: str | None
    mime: str | None
    event_detail: dict[str, object]
    #: Extra keys seeded onto ``Document.extra`` at creation (e.g. the dropped
    #: siblings of the same email, so validation can flag them). Merged with the
    #: channel's own ``email_to`` hint in ``_ingest_candidate``.
    extra_document: dict[str, object] | None = None


#: Synchronous bridge into ``ingest_file`` (poll_mailbox runs off-loop).
IngestCallable = Callable[[IngestCandidate], IngestResult]

#: Synchronous bridge to notify a message's owner that attachments were dropped
#: (poll_mailbox runs off-loop; the async wrapper marshals this onto the loop).
DropNotifier = Callable[[str | None, str | None, list[SkippedAttachment]], None]


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


def _to_addresses(message: MailMessage) -> list[str]:
    """The lowercased ``To:`` addresses (imap-tools already strips display names).

    ``MailMessage.to`` is a tuple of bare addresses (a ``John <a@b.com>`` header
    is parsed to ``a@b.com``); blanks are dropped. ``getattr`` guards a message
    stub without the attribute so provenance capture never itself raises.
    """
    return [
        address.strip().lower()
        for address in (getattr(message, "to", None) or ())
        if address and address.strip()
    ]


def _event_detail(message: MailMessage) -> dict[str, object]:
    """Sender/subject/Message-ID (+ To:) provenance recorded against every ingest."""
    detail: dict[str, object] = {
        "email_from": message.from_,
        "email_subject": message.subject,
        "email_message_id": _message_id(message),
    }
    to_addresses = _to_addresses(message)
    if to_addresses:
        detail["email_to"] = to_addresses
    return detail


def _classify_attachments(
    message: MailMessage, max_bytes: int
) -> tuple[list[IngestCandidate], list[SkippedAttachment]]:
    """Split a message's attachments into ingestable candidates and skips.

    Side-effect free (it only sniffs bytes) so it can run *before* any
    ingestion — the poller needs the full skip list up front to stamp the
    survivors with their dropped siblings. Every rejected attachment becomes a
    :class:`SkippedAttachment` instead of vanishing with a bare ``continue``.
    """
    detail = _event_detail(message)
    candidates: list[IngestCandidate] = []
    skipped: list[SkippedAttachment] = []
    for attachment in message.attachments:
        content = attachment.payload
        name = attachment.filename or None
        if not content:
            skipped.append(SkippedAttachment(name, "empty", "attachment had no content"))
            continue
        if len(content) > max_bytes:
            skipped.append(
                SkippedAttachment(
                    name, "oversize", f"{len(content)} bytes exceeds the {max_bytes}-byte limit"
                )
            )
            continue
        mime = detect_mime(content, attachment.content_type)
        if mime not in ALLOWED_MIME_TYPES:
            skipped.append(
                SkippedAttachment(name, "unsupported_type", f"unsupported file type ({mime})")
            )
            continue
        candidates.append(
            IngestCandidate(
                content=content,
                filename=name,
                mime=attachment.content_type,
                event_detail=detail,
            )
        )
    return candidates, skipped


def _ingest_attachments(
    message: MailMessage, ingest: IngestCallable, max_bytes: int
) -> tuple[int, int, list[SkippedAttachment]]:
    """Ingest every supported attachment of one message (two passes).

    Pass 1 classifies attachments without side effects; pass 2 ingests the
    survivors, stamping each with the email's dropped siblings so validation can
    surface them as a review reason. Returns ``(new_documents, duplicates,
    skipped)``. A per-attachment failure — a content rejection (``IngestError``)
    or any other exception — is recorded as a skip and never aborts the message,
    so one bad attachment can no longer take its siblings down with it.
    """
    candidates, skipped = _classify_attachments(message, max_bytes)
    # Only the classify-pass drops can be stamped onto survivors at creation
    # (ingest-pass errors below are not yet known and would race the async
    # processing job); that is exactly the common "unsupported sibling" case.
    dropped_siblings = _dropped_siblings_payload(skipped)
    new = duplicates = 0
    for candidate in candidates:
        if dropped_siblings:
            candidate = replace(
                candidate, extra_document={"email_siblings_dropped": dropped_siblings}
            )
        try:
            result = ingest(candidate)
        except IngestError as exc:
            logger.warning("email: attachment %r rejected (%s)", candidate.filename, exc)
            skipped.append(SkippedAttachment(candidate.filename, "error", str(exc)))
            continue
        except Exception as exc:  # one bad attachment must not abort its siblings
            logger.exception("email: attachment %r failed to ingest; skipped", candidate.filename)
            skipped.append(SkippedAttachment(candidate.filename, "error", str(exc)))
            continue
        if result.duplicate:
            duplicates += 1
        else:
            new += 1
    return new, duplicates, skipped


def _body_filename(subject: str | None, extension: str) -> str:
    """A safe synthetic filename for a body document.

    The suffix (``.md``/``.txt``) is load-bearing: ``detect_mime`` reads it to
    classify the UTF-8 body (see ``library.ingest``). Path separators and
    control characters are collapsed so the name is safe to store.
    """
    stem = re.sub(r"[/\\\r\n\t]+", "-", (subject or "").strip())[:200].rstrip(". ")
    return f"{stem or 'email'}.{extension}"


def _html_to_markdown(html: str) -> str:
    """Convert an HTML email body to Markdown, dropping script/style noise.

    Markdown is a first-class type downstream (extraction passthrough,
    ``chunk_markdown``, and the viewer renders it), so invoice tables/formatting
    survive where raw ``text/html`` — which the pipeline cannot process — would
    not. ``script``/``style`` subtrees are removed first so their contents don't
    leak into the text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return markdownify(str(soup), heading_style="ATX").strip()


def _body_candidate(message: MailMessage, max_bytes: int) -> IngestCandidate | None:
    """Build a candidate from the email body: HTML (as Markdown) preferred, else text.

    The HTML body is converted to Markdown and stored as ``text/markdown``; a
    plain-text-only body is stored as ``text/plain``. Returns ``None`` when the
    message has no non-blank body (nothing to ingest), when the HTML converts to
    nothing usable, or when the chosen body exceeds ``max_bytes`` (skipped with a
    log line). Called only when no attachment produced a document.
    """
    if (message.html or "").strip():
        markdown = _html_to_markdown(message.html)
        if not markdown:
            return None
        body, mime, extension = markdown, "text/markdown", "md"
    elif (message.text or "").strip():
        body, mime, extension = message.text, "text/plain", "txt"
    else:
        return None
    content = body.encode("utf-8")
    if len(content) > max_bytes:
        logger.warning(
            "email: body of message %r is %d bytes (limit %d); skipped",
            message.subject,
            len(content),
            max_bytes,
        )
        return None
    return IngestCandidate(
        content=content,
        filename=_body_filename(message.subject, extension),
        mime=mime,
        event_detail=_event_detail(message),
    )


def _log_dropped(subject: str | None, skipped: list[SkippedAttachment]) -> None:
    """One WARNING per message summarising the user-facing attachment drops."""
    dropped = [item for item in skipped if item.reason in _USER_FACING_DROP_REASONS]
    if dropped:
        logger.warning(
            "email: message %r dropped %d attachment(s): %s",
            subject,
            len(dropped),
            "; ".join(f"{item.filename or '<unnamed>'} ({item.reason})" for item in dropped),
        )


def poll_mailbox(
    settings: Settings,
    ingest: IngestCallable,
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
    notify: DropNotifier | None = None,
) -> EmailPollSummary:
    """Poll the configured mailbox once and ingest its documents.

    Fetches every message in ``email_folder`` (``ALL``, not unseen-only —
    the seen flag is fragile when a human also reads the mailbox) and, per
    message: rejects senders outside ``email_allowed_senders`` (when
    non-empty; the mail stays in place, visible to the operator), ingests
    each supported attachment via ``ingest`` and — when no attachment
    produced a document — the email body itself (HTML preferred, else plain
    text), then moves the message to ``email_processed_folder`` — the move is
    what makes polling idempotent. Errors are isolated per message: the mail
    is left in place for the next poll and the run continues.

    Attachments that cannot become documents are no longer dropped silently:
    each is recorded, the survivors of the same email are stamped with them (so
    validation can flag the document), and ``notify`` — when supplied — is
    called once per message with drops so the owner gets a push. Nothing is
    lost quietly.

    No-op (empty summary) when ``email_host`` is unset. ``mailbox_factory`` and
    ``notify`` exist for wiring/tests; the default connects per ``settings``.
    """
    if settings.email_host is None:
        logger.debug("email: LIBRARY_EMAIL_HOST unset; poller disabled")
        return EmailPollSummary()
    factory = mailbox_factory or (lambda: _connect(settings))
    allowed = frozenset(settings.email_allowed_senders)
    seen = processed = skipped = ingested = duplicates = dropped = 0
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
                new, dups, dropped_attachments = _ingest_attachments(
                    message, ingest, settings.max_upload_bytes
                )
                if new == 0 and dups == 0:
                    body = _body_candidate(message, settings.max_upload_bytes)
                    if body is not None:
                        # The body is now this email's only document, so it must
                        # carry the dropped siblings too — otherwise the
                        # "attachments couldn't be added" review reason would be
                        # absent exactly when every attachment was dropped.
                        dropped_siblings = _dropped_siblings_payload(dropped_attachments)
                        if dropped_siblings:
                            body = replace(
                                body, extra_document={"email_siblings_dropped": dropped_siblings}
                            )
                        try:
                            result = ingest(body)
                        except IngestError as exc:
                            logger.warning(
                                "email: body of message %r rejected (%s)", message.subject, exc
                            )
                        else:
                            if result.duplicate:
                                dups += 1
                            else:
                                new += 1
                ingested += new
                duplicates += dups
                # Surface every user-facing drop: log a summary, count it, and
                # push the owner. Done after ingest so ingest-pass errors count
                # too, and before the move so it never blocks idempotency.
                user_facing = [
                    item for item in dropped_attachments if item.reason in _USER_FACING_DROP_REASONS
                ]
                if user_facing:
                    _log_dropped(message.subject, dropped_attachments)
                    dropped += len(user_facing)
                    if notify is not None:
                        notify(message.from_, message.subject, dropped_attachments)
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
        attachments_dropped=dropped,
    )
    logger.info("email: poll finished: %s", summary)
    return summary


async def resolve_sender_owner(
    session: AsyncSession, sender: str | None, *, default_owner_username: str | None = None
) -> int | None:
    """Resolve an email sender address to the owning user's id.

    Matches the (lowercased) sender against any user's
    ``preferences.notifications.email_forward_addresses`` (via the shared
    ``match_user_by_email`` helper); on no match, falls back to
    ``default_owner_username`` (if configured); otherwise ``None`` (the document
    stays unowned, as before this feature).
    """
    user_id = await match_user_by_email(session, sender or "")
    if user_id is not None:
        return user_id
    if default_owner_username:
        return (
            await session.execute(select(User.id).where(User.username == default_owner_username))
        ).scalar_one_or_none()
    return None


async def _ingest_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    candidate: IngestCandidate,
    *,
    default_owner_username: str | None = None,
) -> IngestResult:
    async with session_factory() as session:
        owner_id = await resolve_sender_owner(
            session,
            candidate.event_detail.get("email_from"),  # type: ignore[arg-type]
            default_owner_username=default_owner_username,
        )
        # Carry the To: addresses onto Document.extra so extraction can use them
        # as the recipient fallback (only-fill-when-empty; see extraction.apply),
        # merged with any per-candidate extra (e.g. the email's dropped siblings,
        # so validation can flag them). Candidate keys win on overlap.
        email_to = candidate.event_detail.get("email_to")
        extra_document: dict[str, object] = {}
        if email_to:
            extra_document["email_to"] = email_to
        if candidate.extra_document:
            extra_document.update(candidate.extra_document)
        return await ingest_file(
            session,
            content=candidate.content,
            filename=candidate.filename,
            mime=candidate.mime,
            source=DocumentSource.EMAIL,
            uploader_id=owner_id,
            extra_event_detail=dict(candidate.event_detail),
            extra_document=extra_document or None,
        )


async def _notify_dropped(
    session_factory: async_sessionmaker[AsyncSession],
    sender: str | None,
    subject: str | None,
    skipped: list[SkippedAttachment],
    *,
    default_owner_username: str | None,
    document_url_base: str | None,
) -> None:
    """Resolve the sender's owner and push a best-effort "attachments dropped" alert.

    Fired once per message with user-facing drops. Resolves the owner exactly
    like an attachment would (so the right person is told), then delegates to the
    notifications module, which respects the owner's opt-in and never raises.
    """
    dropped = [item for item in skipped if item.reason in _USER_FACING_DROP_REASONS]
    if not dropped:
        return
    async with session_factory() as session:
        owner_id = await resolve_sender_owner(
            session, sender, default_owner_username=default_owner_username
        )
    await dispatch_attachments_dropped_notification(
        session_factory,
        owner_id,
        subject=subject,
        filenames=[item.filename for item in dropped],
        document_url_base=document_url_base,
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
    session and job-queue connector stay on their home loop. The sender is
    resolved to an owning user there too (``resolve_sender_owner``).
    """
    loop = asyncio.get_running_loop()
    default_owner = settings.email_default_owner
    url_base = settings.public_base_url

    def ingest_on_loop(candidate: IngestCandidate) -> IngestResult:
        future = asyncio.run_coroutine_threadsafe(
            _ingest_candidate(session_factory, candidate, default_owner_username=default_owner),
            loop,
        )
        return future.result()

    def notify_on_loop(
        sender: str | None, subject: str | None, skipped: list[SkippedAttachment]
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _notify_dropped(
                session_factory,
                sender,
                subject,
                skipped,
                default_owner_username=default_owner,
                document_url_base=url_base,
            ),
            loop,
        )
        future.result()

    return await asyncio.to_thread(
        poll_mailbox,
        settings,
        ingest_on_loop,
        mailbox_factory=mailbox_factory,
        notify=notify_on_loop,
    )
