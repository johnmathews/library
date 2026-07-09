"""Email-in ingestion: poll an IMAP mailbox and ingest its attachments.

A periodic Procrastinate task (``library.jobs.poll_email_inbox``) polls
``LIBRARY_EMAIL_FOLDER`` every ``LIBRARY_EMAIL_POLL_MINUTES`` minutes and
feeds each *useful* attachment through the same ``ingest_file`` service as an
upload (``source=email``; the uploader is resolved from the sender via
``resolve_sender_owner``), attaching the sender, subject, and Message-ID to the
recorded ingestion event. Which items are worth filing is decided by the
selection gates — a deterministic noise gate (``_noise_reason``: inline
signature images, tiny pixels/icons, calendar/vCard/PKCS7/TNEF parts), an
optional per-email LLM label pass (``library.email_label``, off by default), and
a body-substance gate (``_body_substance``) — all recorded in a per-email
decision trace (``_log_selection_trace`` + an ``email_selection`` event). When no
attachment produces a document, the email body itself is ingested *if it clears
the substance gate* — HTML converted to Markdown (``text/markdown``), else plain
text (``text/plain``) — so "the email is the invoice" works too. See
docs/ingestion.md, "Email item selection". The overriding invariant: **never lose
a real document** — nothing is deleted; an item is ingested, ingested-and-flagged
(``needs_review``), or recorded as a recoverable/quiet drop.

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
import io
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, AsyncExitStack
from dataclasses import dataclass, replace
from typing import Any, Protocol

from anthropic import AsyncAnthropic
from imap_tools import MailBox, MailMessage
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library.config import Settings
from library.email_label import LabelItem, LabelOutcome, LabelUsage, label_email_items
from library.extraction.apply import match_user_by_email
from library.ingest import (
    ALLOWED_MIME_TYPES,
    IngestError,
    IngestResult,
    detect_mime,
    ingest_file,
)
from library.markdown.html import html_to_markdown
from library.models import DocumentSource, IngestionEvent, User
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
    attachments_filtered: int = 0  # deterministic noise (signature/tiny/non-document); quiet


@dataclass(frozen=True, slots=True)
class SkippedAttachment:
    """One attachment that could not be turned into a document.

    ``reason`` is a stable code: ``empty`` (no payload — usually inline cruft,
    not surfaced to the user), ``oversize``, ``unsupported_type``, ``error``
    (an unexpected failure during ingest), or one of the deterministic noise
    filters (``signature_image``, ``tiny_image``, ``non_document_type`` — see
    ``_noise_reason``). ``detail`` is a human sentence. ``size``/``mime`` are the
    attachment's byte length and detected type, carried for the decision trace.
    """

    filename: str | None
    reason: str
    detail: str
    size: int | None = None
    mime: str | None = None


#: Drop reasons a human actually cares about — surfaced on the sibling document
#: (as a review reason) and in the "attachments couldn't be added" push. Every
#: other reason (``empty`` and the deterministic noise filters) is recorded in
#: the decision trace but deliberately not surfaced, so a stray inline part or a
#: signature logo never flags a real document.
_USER_FACING_DROP_REASONS: frozenset[str] = frozenset({"oversize", "unsupported_type", "error"})

#: Deterministic-noise skip reasons produced by ``_noise_reason``. Recorded in the
#: decision trace and counted (``attachments_filtered``) but never surfaced.
_NOISE_REASONS: frozenset[str] = frozenset({"signature_image", "tiny_image", "non_document_type"})

#: Content-Types that are never a filed document — email/client protocol cruft.
#: Matched against the part's *declared* Content-Type: calendar/vCard bytes are
#: UTF-8 and sniff as ``text/plain`` (an allowed type), so the sniffed mime alone
#: cannot catch them.
_NON_DOCUMENT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/calendar",
        "text/vcard",
        "text/x-vcard",
        "application/pkcs7-signature",
        "application/x-pkcs7-signature",
        "application/ms-tnef",
    }
)


def _dropped_siblings_payload(skipped: list[SkippedAttachment]) -> list[dict[str, str | None]]:
    """The user-facing subset of ``skipped``, shaped for storage/notification."""
    return [
        {"filename": item.filename, "reason": item.reason, "detail": item.detail}
        for item in skipped
        if item.reason in _USER_FACING_DROP_REASONS
    ]


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    """What the selection pipeline decided about one email item, and why.

    The full per-email list is the *decision trace*: it answers "what happened
    to each of ``[body, att1 … attN]``, at which stage, and why" for debugging,
    triage, and tuning. Emitted as one greppable log line per email
    (``_log_selection_trace``) and, when a document is produced, persisted as an
    ``email_selection`` :class:`IngestionEvent` on it (visible in the document's
    history). See docs/ingestion.md, "Email item selection".

    ``kind`` is ``attachment`` or ``body``. ``stage`` is the stage that reached
    the verdict: ``classify`` (deterministic attachment gate), ``body_substance``
    (body prose gate), or ``llm_label`` (the optional per-email LLM pass).
    ``verdict`` is one of ``ingested``, ``duplicate``, ``dropped`` (a user-facing
    rejection), ``filtered`` (recorded-but-quiet noise), or ``flagged_ambiguous``
    (ingested but flagged as possible noise). A skipped LLM label pass is not a
    per-item verdict — it surfaces as an ``email-label: … skipped`` log line and
    ``flagged=0`` in the trace, not a ``SelectionDecision``.
    """

    kind: str
    filename: str | None
    mime: str | None
    size: int | None
    stage: str
    verdict: str
    reason: str | None = None

    def as_detail(self) -> dict[str, object]:
        """JSON-serialisable form for the persisted ``email_selection`` event."""
        return {
            "kind": self.kind,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "stage": self.stage,
            "verdict": self.verdict,
            "reason": self.reason,
        }

    def render(self) -> str:
        """Compact single-token rendering for the trace log line."""
        token = f"{self.filename or '<' + self.kind + '>'}:{self.stage}:{self.verdict}"
        return f"{token}({self.reason})" if self.reason else token


def _skip_verdict(reason: str) -> str:
    """A skipped attachment is a user-facing ``dropped`` or a quiet ``filtered``."""
    return "dropped" if reason in _USER_FACING_DROP_REASONS else "filtered"


def _skip_decision(item: SkippedAttachment) -> SelectionDecision:
    """The classify-stage decision for one skipped attachment."""
    return SelectionDecision(
        kind="attachment",
        filename=item.filename,
        mime=item.mime,
        size=item.size,
        stage="classify",
        verdict=_skip_verdict(item.reason),
        reason=item.reason,
    )


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

#: Synchronous bridge to persist the per-email decision trace as an
#: ``email_selection`` event on each produced document, plus (when present) one
#: ``email_label_completed`` budget event. The async wrapper marshals it onto the
#: loop. Given the new document ids, the trace detail, and the label detail.
TracePersister = Callable[[list[int], dict[str, object], dict[str, object] | None], None]


@dataclass(frozen=True, slots=True)
class LabelRequest:
    """One email's attachments (plus context) presented to the LLM label pass."""

    subject: str | None
    sender: str | None
    body_snippet: str
    items: list[LabelItem]


#: Synchronous bridge into the per-email LLM label pass (poll_mailbox runs
#: off-loop; the async wrapper marshals this onto the loop). Present only when
#: ``email_label_enabled`` and an API key are configured; otherwise ``None``.
LabelCallable = Callable[[LabelRequest], LabelOutcome]


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


#: A ``To:``/``Aan:`` header line inside a quoted/forwarded block.
_QUOTED_TO_HEADER_RE = re.compile(r"^\s*>*\s*(?:to|aan)\s*:\s*(.+)$", re.IGNORECASE)
#: A bare email address, for pulling recipients out of a quoted header line.
_EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _forwarded_to_addresses(message: MailMessage) -> list[str]:
    """The ``To:`` addresses of the *original* message inside a forwarded body.

    A forwarded document's outer ``To:`` is the library dropbox; the person the
    document was really sent to appears in the quoted header block after a
    "--- Forwarded message ---" banner. This scans the plain-text body from the
    first forward/quote banner for a ``To:``/``Aan:`` header and returns the
    email addresses found there (lowercased, in order). Best-effort and bounded:
    only the lines shortly after the banner are inspected. Empty when the body
    has no forwarded header — nothing is invented.
    """
    body = message.text or ""
    if not body:
        return []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not (_FORWARD_BANNER_RE.match(line) or _QUOTE_HEADER_RE.match(line)):
            continue
        # Scan the header block immediately after the banner (bounded window).
        for header in lines[index + 1 : index + 12]:
            match = _QUOTED_TO_HEADER_RE.match(header)
            if match:
                found = [addr.lower() for addr in _EMAIL_ADDR_RE.findall(match.group(1))]
                if found:
                    return found
    return []


def _event_detail(message: MailMessage) -> dict[str, object]:
    """Sender/subject/Message-ID (+ To:) provenance recorded against every ingest."""
    detail: dict[str, object] = {
        "email_from": message.from_,
        "email_subject": message.subject,
        "email_message_id": _message_id(message),
    }
    # Prefer the ORIGINAL recipient (from a forwarded header) over the outer To:
    # (the dropbox) — first match wins in resolve_recipient_from_email, so the
    # earliest known recipient in the chain is chosen. Deduped, order preserved.
    to_addresses: list[str] = []
    for address in [*_forwarded_to_addresses(message), *_to_addresses(message)]:
        if address not in to_addresses:
            to_addresses.append(address)
    if to_addresses:
        detail["email_to"] = to_addresses
    return detail


def _declared_content_type(attachment: Any) -> str:
    """The part's declared Content-Type, lowercased and stripped of parameters."""
    raw = getattr(attachment, "content_type", None) or ""
    return raw.split(";", 1)[0].strip().lower()


def _is_image(content_type: str, mime: str | None) -> bool:
    """True when either the declared or the sniffed type is an image."""
    return content_type.startswith("image/") or bool(mime and mime.startswith("image/"))


def _image_longest_edge(content: bytes) -> int | None:
    """The longest edge in px (header-only decode); ``None`` if undecodable.

    ``Image.open`` reads only the header, so this stays cheap. A decode failure
    returns ``None`` and the caller keeps the attachment (bias to ingest).
    """
    try:
        with Image.open(io.BytesIO(content)) as image:
            return max(image.size)
    except Exception:
        return None


def _noise_reason(
    attachment: Any, content: bytes, mime: str | None, html: str, settings: Settings
) -> tuple[str, str] | None:
    """A deterministic ``(reason, detail)`` when an attachment is unambiguous noise.

    Conservative by design (bias to ingest): only a non-document protocol part, an
    inline/CID signature image, or a sub-threshold tiny image trips a rule. A
    normal-sized image is kept; an image we cannot decode is kept unless it is also
    below the byte-size floor (the tiny-image fallback). Returns ``None`` to keep.
    """
    if not settings.email_filter_noise_enabled:
        return None
    content_type = _declared_content_type(attachment)
    if content_type in _NON_DOCUMENT_CONTENT_TYPES:
        return "non_document_type", f"non-document part ({content_type or 'unknown type'})"
    if _is_image(content_type, mime):
        disposition = (getattr(attachment, "content_disposition", None) or "").strip().lower()
        content_id = (getattr(attachment, "content_id", None) or "").strip().strip("<>")
        if disposition == "inline" or (content_id and f"cid:{content_id}" in html):
            return "signature_image", "inline/embedded image (signature or logo)"
        # Dimensions are the authoritative "is this an icon/pixel" signal, so
        # prefer them; a legitimate small-but-normal-sized image is kept. Byte
        # size is only the fallback for an image we cannot decode.
        edge = _image_longest_edge(content)
        if edge is not None:
            if edge <= settings.email_filter_tiny_image_max_edge_px:
                return "tiny_image", f"longest edge {edge}px is at/under the tiny-image threshold"
        elif len(content) < settings.email_filter_tiny_image_max_bytes:
            return "tiny_image", f"undecodable image, {len(content)} bytes below the threshold"
    return None


def _classify_attachments(
    message: MailMessage, max_bytes: int, settings: Settings
) -> tuple[list[IngestCandidate], list[SkippedAttachment]]:
    """Split a message's attachments into ingestable candidates and skips.

    Side-effect free (it only sniffs bytes) so it can run *before* any
    ingestion — the poller needs the full skip list up front to stamp the
    survivors with their dropped siblings. Every rejected attachment becomes a
    :class:`SkippedAttachment` instead of vanishing with a bare ``continue``.
    """
    detail = _event_detail(message)
    # The HTML body, lowercased once, so the signature-image rule can test each
    # attachment's Content-ID against the body's ``cid:`` references cheaply.
    html = (message.html or "").lower()
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
                    name,
                    "oversize",
                    f"{len(content)} bytes exceeds the {max_bytes}-byte limit",
                    size=len(content),
                    mime=attachment.content_type,
                )
            )
            continue
        mime = detect_mime(content, attachment.content_type)
        # Deterministic noise gate — before the allowed-type check, so a noise
        # image is filtered with its noise reason rather than ingested, and a
        # calendar/vCard part (which sniffs as the allowed text/plain) is caught.
        noise = _noise_reason(attachment, content, mime, html, settings)
        if noise is not None:
            reason, noise_detail = noise
            skipped.append(
                SkippedAttachment(name, reason, noise_detail, size=len(content), mime=mime)
            )
            continue
        if mime not in ALLOWED_MIME_TYPES:
            skipped.append(
                SkippedAttachment(
                    name,
                    "unsupported_type",
                    f"unsupported file type ({mime})",
                    size=len(content),
                    mime=mime,
                )
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


@dataclass(frozen=True, slots=True)
class _AttachmentOutcome:
    """The result of ingesting one message's attachments (plus its decision trace)."""

    new: int
    duplicates: int
    skipped: list[SkippedAttachment]
    decisions: list[SelectionDecision]
    produced: list[IngestResult]  # every candidate that ingested (new or duplicate)
    label_usage: LabelUsage | None  # billing for the LLM label pass, if it ran


def _candidate_decision(
    candidate: IngestCandidate, verdict: str, reason: str | None = None, stage: str = "classify"
) -> SelectionDecision:
    """The decision for a candidate that reached ingest."""
    return SelectionDecision(
        kind="attachment",
        filename=candidate.filename,
        mime=candidate.mime,
        size=len(candidate.content),
        stage=stage,
        verdict=verdict,
        reason=reason,
    )


def _body_snippet(message: MailMessage, max_chars: int) -> str:
    """A cleaned body excerpt for LLM label context (prose only, capped)."""
    raw = message.text or ""
    if not raw.strip() and (message.html or "").strip():
        raw = html_to_markdown(message.html) or ""
    return _body_substance(raw)[:max_chars]


def _label_request(
    message: MailMessage, candidates: list[IngestCandidate], settings: Settings
) -> LabelRequest:
    """Build the LLM label request from the surviving attachment candidates."""
    items = [
        LabelItem(index=index, filename=c.filename, mime=c.mime, size=len(c.content))
        for index, c in enumerate(candidates)
    ]
    return LabelRequest(
        subject=message.subject,
        sender=message.from_,
        body_snippet=_body_snippet(message, settings.email_label_body_snippet_chars),
        items=items,
    )


def _error_skip(candidate: IngestCandidate, exc: Exception) -> SkippedAttachment:
    return SkippedAttachment(
        candidate.filename, "error", str(exc), size=len(candidate.content), mime=candidate.mime
    )


def _ingest_attachments(
    message: MailMessage,
    ingest: IngestCallable,
    max_bytes: int,
    settings: Settings,
    label: LabelCallable | None = None,
) -> _AttachmentOutcome:
    """Ingest every supported attachment of one message (classify → label → ingest).

    Pass 1 classifies attachments without side effects. When ``label`` is
    supplied, the surviving candidates are then labelled ``keep``/
    ``probably_noise`` by one LLM call; a ``probably_noise`` verdict flags the
    document (``extra["email_selection"]``) but is **still ingested** — the label
    never drops. Pass 2 ingests the survivors, stamping each with the email's
    dropped siblings (so validation can surface them). Returns an
    :class:`_AttachmentOutcome` with counts, skips, the per-attachment decision
    trace, the ingest results, and the label billing. A per-attachment failure —
    a content rejection (``IngestError``) or any other exception — is recorded as
    a skip and never aborts the message.
    """
    candidates, skipped = _classify_attachments(message, max_bytes, settings)
    # Only the classify-pass drops can be stamped onto survivors at creation
    # (ingest-pass errors below are not yet known and would race the async
    # processing job); that is exactly the common "unsupported sibling" case.
    dropped_siblings = _dropped_siblings_payload(skipped)
    decisions: list[SelectionDecision] = [_skip_decision(item) for item in skipped]

    verdicts: dict[int, tuple[str, str | None]] = {}
    label_usage: LabelUsage | None = None
    if label is not None and candidates:
        outcome = label(_label_request(message, candidates, settings))
        verdicts = outcome.verdicts
        label_usage = outcome.usage
        if outcome.skip_reason:
            logger.info(
                "email-label: pass skipped (%s) for %r", outcome.skip_reason, message.subject
            )

    produced: list[IngestResult] = []
    new = duplicates = 0
    for index, candidate in enumerate(candidates):
        verdict = verdicts.get(index)
        flagged = verdict is not None and verdict[0] == "probably_noise"
        extra: dict[str, object] = {}
        if dropped_siblings:
            extra["email_siblings_dropped"] = dropped_siblings
        if flagged:
            # Ingest-and-flag: the LLM only annotates, never drops (a false
            # positive costs a review click, not a lost document).
            extra["email_selection"] = {
                "verdict": "probably_noise",
                "reason": verdict[1],
                "source": "llm_label",
            }
        stamped = replace(candidate, extra_document=extra) if extra else candidate
        try:
            result = ingest(stamped)
        except IngestError as exc:
            logger.warning("email: attachment %r rejected (%s)", candidate.filename, exc)
            skipped.append(_error_skip(candidate, exc))
            decisions.append(_candidate_decision(candidate, "dropped", "error"))
            continue
        except Exception as exc:  # one bad attachment must not abort its siblings
            logger.exception("email: attachment %r failed to ingest; skipped", candidate.filename)
            skipped.append(_error_skip(candidate, exc))
            decisions.append(_candidate_decision(candidate, "dropped", "error"))
            continue
        produced.append(result)
        if result.duplicate:
            duplicates += 1
            decisions.append(_candidate_decision(candidate, "duplicate"))
        elif flagged:
            new += 1
            decisions.append(
                _candidate_decision(candidate, "flagged_ambiguous", verdict[1], stage="llm_label")
            )
        else:
            new += 1
            decisions.append(_candidate_decision(candidate, "ingested"))
    return _AttachmentOutcome(new, duplicates, skipped, decisions, produced, label_usage)


def _body_filename(subject: str | None, extension: str) -> str:
    """A safe synthetic filename for a body document.

    The suffix (``.md``/``.txt``) is load-bearing: ``detect_mime`` reads it to
    classify the UTF-8 body (see ``library.ingest``). Path separators and
    control characters are collapsed so the name is safe to store.
    """
    stem = re.sub(r"[/\\\r\n\t]+", "-", (subject or "").strip())[:200].rstrip(". ")
    return f"{stem or 'email'}.{extension}"


#: A body must reach one of these to be worth filing as a document — filters
#: contentless cover notes ("FYI see attached") without dropping a real
#: body-as-invoice. Either bound satisfies (short-but-dense or long-but-sparse).
_BODY_MIN_WORDS = 40
_BODY_MIN_CHARS = 240

#: "On <date>, <someone> wrote:" — the reply-quote header most clients emit.
_QUOTE_HEADER_RE = re.compile(r"^\s*On\b.+\bwrote:\s*$", re.IGNORECASE)
#: A forwarded-message / original-message banner that precedes quoted context.
_FORWARD_BANNER_RE = re.compile(
    r"^\s*-+\s*(forwarded message|original message)\s*-+\s*$", re.IGNORECASE
)
#: "Sent from my iPhone" / Dutch "Verzonden vanaf" mobile-client footers.
_MOBILE_FOOTER_RE = re.compile(r"^\s*(sent|verzonden)\s+(from|via|vanaf)\b", re.IGNORECASE)


def _body_substance(text: str) -> str:
    """Return the body's genuine prose: quoted replies, signatures, footers removed.

    Best-effort and line-based: it cuts everything from the first quoted-reply
    boundary (an ``On … wrote:`` header, a forwarded/original-message banner, or a
    ``> ``-quoted line) or a signature delimiter (``--``) downward, and drops
    mobile-client footer lines. The result is what the substance threshold is
    measured against and what is actually ingested, so a filed body isn't padded
    with a reply chain it merely quoted.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped == "--"  # signature delimiter (RFC 3676 "-- ", clients vary)
            or _QUOTE_HEADER_RE.match(line)
            or _FORWARD_BANNER_RE.match(line)
            or stripped.startswith(">")
        ):
            break
        if _MOBILE_FOOTER_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _body_candidate(message: MailMessage, max_bytes: int) -> tuple[IngestCandidate | None, str]:
    """Build a candidate from the email body, gated on substance.

    HTML (converted to Markdown, ``text/markdown``) is preferred over plain text
    (``text/plain``). The body is stripped of quoted replies / signatures /
    mobile footers (:func:`_body_substance`) and must clear the substance
    threshold — this filters contentless cover notes ("FYI see attached") while
    still filing a genuine body-as-invoice. Returns ``(candidate, "")`` on
    success, or ``(None, reason)`` where ``reason`` is ``blank`` / ``oversize`` /
    ``below_substance:<n>w`` for the decision trace. Called only when no
    attachment produced a document.
    """
    if (message.html or "").strip():
        markdown = html_to_markdown(message.html)
        if not markdown:
            return None, "blank"
        raw_body, mime, extension = markdown, "text/markdown", "md"
    elif (message.text or "").strip():
        raw_body, mime, extension = message.text, "text/plain", "txt"
    else:
        return None, "blank"
    body = _body_substance(raw_body)
    words = len(body.split())
    if words < _BODY_MIN_WORDS and len(body) < _BODY_MIN_CHARS:
        logger.info(
            "email: body of message %r below substance threshold (%dw/%dc); not ingested",
            message.subject,
            words,
            len(body),
        )
        return None, f"below_substance:{words}w"
    content = body.encode("utf-8")
    if len(content) > max_bytes:
        logger.warning(
            "email: body of message %r is %d bytes (limit %d); skipped",
            message.subject,
            len(content),
            max_bytes,
        )
        return None, "oversize"
    candidate = IngestCandidate(
        content=content,
        filename=_body_filename(message.subject, extension),
        mime=mime,
        event_detail=_event_detail(message),
    )
    return candidate, ""


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


def _log_selection_trace(message: MailMessage, decisions: list[SelectionDecision]) -> None:
    """Emit the always-on, single-line, greppable decision trace for one email.

    This is the primary debug/triage surface: it records what happened to every
    item — ``[body, att1 … attN]`` — at which stage and why, even when the email
    produced no document (the persisted ``email_selection`` event cannot cover
    that case, as it has no document to hang on). The stable ``email-selection``
    prefix lets Loki/``grep`` pull one email's full trace. See the runbook in
    docs/ for the format and the action-per-reason table.
    """
    counts = Counter(decision.verdict for decision in decisions)
    logger.info(
        "email-selection msg=%r from=%r items=%d ingested=%d duplicate=%d "
        "dropped=%d filtered=%d flagged=%d :: %s",
        message.subject,
        message.from_,
        len(decisions),
        counts.get("ingested", 0),
        counts.get("duplicate", 0),
        counts.get("dropped", 0),
        counts.get("filtered", 0),
        counts.get("flagged_ambiguous", 0),
        " | ".join(decision.render() for decision in decisions),
    )


def _selection_event_detail(
    message: MailMessage, decisions: list[SelectionDecision]
) -> dict[str, object]:
    """JSON detail for the persisted ``email_selection`` event: provenance + trace."""
    detail = _event_detail(message)
    detail["items"] = [decision.as_detail() for decision in decisions]
    return detail


def _body_decision(
    body: IngestCandidate, verdict: str, reason: str | None = None
) -> SelectionDecision:
    """The body-stage decision for a body candidate that reached ingest."""
    return SelectionDecision(
        kind="body",
        filename=body.filename,
        mime=body.mime,
        size=len(body.content),
        stage="body_substance",
        verdict=verdict,
        reason=reason,
    )


def _body_skip_decision(reason: str) -> SelectionDecision:
    """The body-stage decision when the body is not ingested at all."""
    return SelectionDecision(
        kind="body",
        filename=None,
        mime=None,
        size=None,
        stage="body_substance",
        verdict="filtered",
        reason=reason,
    )


def poll_mailbox(
    settings: Settings,
    ingest: IngestCallable,
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
    notify: DropNotifier | None = None,
    persist_trace: TracePersister | None = None,
    label: LabelCallable | None = None,
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

    Every message also emits a one-line decision trace (``_log_selection_trace``)
    recording what happened to each item and why; when ``persist_trace`` is
    supplied, that trace is also stored as an ``email_selection`` event on each
    new document so it shows in the document's history.

    No-op (empty summary) when ``email_host`` is unset. ``mailbox_factory``,
    ``notify``, and ``persist_trace`` exist for wiring/tests; the default
    connects per ``settings``.
    """
    if settings.email_host is None:
        logger.debug("email: LIBRARY_EMAIL_HOST unset; poller disabled")
        return EmailPollSummary()
    factory = mailbox_factory or (lambda: _connect(settings))
    allowed = frozenset(settings.email_allowed_senders)
    seen = processed = skipped = ingested = duplicates = dropped = filtered = 0
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
                outcome = _ingest_attachments(
                    message, ingest, settings.max_upload_bytes, settings, label
                )
                new, dups = outcome.new, outcome.duplicates
                dropped_attachments = outcome.skipped
                filtered += sum(1 for item in dropped_attachments if item.reason in _NOISE_REASONS)
                decisions = outcome.decisions
                produced = list(outcome.produced)
                if new == 0 and dups == 0:
                    body, body_skip_reason = _body_candidate(message, settings.max_upload_bytes)
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
                            decisions.append(_body_decision(body, "dropped", "rejected"))
                        else:
                            produced.append(result)
                            if result.duplicate:
                                dups += 1
                                decisions.append(_body_decision(body, "duplicate"))
                            else:
                                new += 1
                                decisions.append(_body_decision(body, "ingested"))
                    else:
                        decisions.append(_body_skip_decision(body_skip_reason or "no_body"))
                else:
                    decisions.append(_body_skip_decision("not_needed"))
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
                # The always-on decision trace (covers the zero-document case too),
                # then persist it onto each new document — best-effort and keyed on
                # document_id, so an email that produced nothing lives only in the log.
                _log_selection_trace(message, decisions)
                if persist_trace is not None:
                    new_document_ids = [
                        result.document.id for result in produced if not result.duplicate
                    ]
                    if new_document_ids:
                        label_detail = (
                            outcome.label_usage.as_detail() if outcome.label_usage else None
                        )
                        persist_trace(
                            new_document_ids,
                            _selection_event_detail(message, decisions),
                            label_detail,
                        )
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
        attachments_filtered=filtered,
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


async def _persist_selection_trace(
    session_factory: async_sessionmaker[AsyncSession],
    document_ids: list[int],
    detail: dict[str, object],
    label_detail: dict[str, object] | None = None,
) -> None:
    """Append the per-email ``email_selection`` trace (and label budget event).

    Writes ``email_selection`` on every new document; when the LLM label pass
    billed, also writes one ``email_label_completed`` event (carrying its cost)
    on the first document — this is what the label budget gate later sums. Best-
    effort audit data: it never blocks the poll and never raises past this
    boundary. An email that produced no *new* document (nothing filed, or only
    duplicates) has no row to hang either event on — that case is covered by the
    always-on trace log line instead, and its (rare) label spend goes unrecorded
    (a deliberate under-count of at most one cheap call per such email).
    """
    if not document_ids:
        return
    try:
        async with session_factory() as session:
            for document_id in document_ids:
                session.add(
                    IngestionEvent(document_id=document_id, event="email_selection", detail=detail)
                )
            if label_detail is not None:
                session.add(
                    IngestionEvent(
                        document_id=document_ids[0],
                        event="email_label_completed",
                        detail=label_detail,
                    )
                )
            await session.commit()
    except Exception:  # audit trail must never take down a poll
        logger.exception("email: failed to persist selection trace for %s", document_ids)


async def _label_email_on_loop(
    session_factory: async_sessionmaker[AsyncSession],
    client: AsyncAnthropic,
    settings: Settings,
    request: LabelRequest,
) -> LabelOutcome:
    """Run the LLM label pass with a fresh session (for the budget read)."""
    async with session_factory() as session:
        return await label_email_items(
            session,
            client,
            settings,
            subject=request.subject,
            sender=request.sender,
            body_snippet=request.body_snippet,
            items=request.items,
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

    def persist_trace_on_loop(
        document_ids: list[int],
        detail: dict[str, object],
        label_detail: dict[str, object] | None,
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _persist_selection_trace(session_factory, document_ids, detail, label_detail),
            loop,
        )
        future.result()

    # The optional per-email LLM label pass is wired only when enabled AND an API
    # key is set; the Anthropic client's lifetime spans the whole poll.
    async with AsyncExitStack() as stack:
        label: LabelCallable | None = None
        if settings.email_label_enabled and settings.anthropic_api_key is not None:
            client = await stack.enter_async_context(
                AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
            )

            def label_on_loop(request: LabelRequest) -> LabelOutcome:
                future = asyncio.run_coroutine_threadsafe(
                    _label_email_on_loop(session_factory, client, settings, request),
                    loop,
                )
                return future.result()

            label = label_on_loop

        return await asyncio.to_thread(
            poll_mailbox,
            settings,
            ingest_on_loop,
            mailbox_factory=mailbox_factory,
            notify=notify_on_loop,
            persist_trace=persist_trace_on_loop,
            label=label,
        )
