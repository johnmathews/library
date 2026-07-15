"""Email-in ingestion: poll an IMAP mailbox and ingest its attachments.

A periodic Procrastinate task (``library.jobs.poll_email_inbox``) polls
``LIBRARY_EMAIL_FOLDER`` every ``LIBRARY_EMAIL_POLL_MINUTES`` minutes and
feeds each *useful* attachment through the same ``ingest_file`` service as an
upload (``source=email``; the uploader is resolved from the sender via
``resolve_sender_owner``), attaching the sender, subject, and Message-ID to the
recorded ingestion event. Which items are worth filing is decided by the
selection gates — a deterministic noise gate (``_noise_reason``: inline
signature images, tiny pixels/icons, decoration images such as logos and
banners, calendar/vCard/PKCS7/TNEF parts), an
optional per-email LLM label pass (``library.email_label``, off by default;
its ``probably_noise`` verdict normally ingests-and-flags, but becomes a quiet
``llm_noise_corroborated`` skip when at least one deterministic decoration
signal agrees — see ``_ingest_attachments``), and
a body-substance gate (``_body_substance``) — all recorded in a per-email
decision trace (``_log_selection_trace`` + an ``email_selection`` event on each
new document, plus one durable ``email_selection_traces`` row whenever anything
was skipped, however quietly — ``_skip_trace_record`` — so a wrongly-skipped
attachment is discoverable without grepping logs, even when the email produced
zero documents). When no
attachment produces a document, the email body itself is ingested *if it clears
the substance gate* — HTML converted to Markdown (``text/markdown``), else plain
text (``text/plain``) — so "the email is the invoice" works too. See
docs/ingestion.md, "Email item selection". The overriding invariant: **never lose
a real document** — nothing is deleted; an item is ingested, ingested-and-flagged
(``needs_review``), or recorded as a recoverable/quiet drop.

An email the pipeline judges *not library-worthy* is **held for review** rather
than silently processed (``email_hold_enabled``, on by default): a durable
``held_emails`` row is written first, then the message is moved to
``LIBRARY_EMAIL_HELD_FOLDER`` — row-before-move, so a hold can never lose its
pointer (a failed row leaves the mail in place for the next poll; a failed move
retries idempotently against the existing row). A held email is resolved by a
human: "ingest anyway" (``ingest_held_email_async``, run as the
``library.jobs.ingest_held_email`` task) re-fetches the message from the Held
folder by Message-ID and ingests it with override semantics — the hard
deterministic gates (signature/tiny/non-document/oversize/unsupported) still
apply, but the decoration heuristics (``decoration_image`` and the
LLM-corroborated skip) are bypassed and no label call is made (the human
already overruled them), and the body-substance gate is bypassed when
the attachments produce nothing — while "dismiss" (``dismiss_held_email``) is a
pure DB status flip that leaves the bytes in the Held folder forever. Holding
also fires a best-effort ``email_held`` push to the resolved owner (opt-in, see
``library.notifications``). Four triggers hold: an
allowlist-rejected sender (``sender_unknown``), a body-only email below the
substance gate (``below_substance``), an email whose user-facing drops left
nothing ingested (``nothing_ingested``), and the label pass's whole-email
verdict (``llm_hold``). The label pass (when enabled) now runs once per
*message* — the surviving attachments plus the body as a judged item, so
body-only emails are judged too — and returns ``email_verdict``:
``file`` proceeds with normal ingestion, ``hold`` routes the whole email to
review before anything is ingested. Fail-open is absolute: any label skip,
error, budget stop, or untrusted response degrades to ``file`` — exactly
today's ingest behavior — never a hold, never a loss. With
``email_hold_enabled=false`` every trigger reverts to the pre-hold behavior.

Idempotency is folder-based: every fully processed message is moved to
``LIBRARY_EMAIL_PROCESSED_FOLDER`` (created on first use), so a message
is never scanned twice; content dedup in ``ingest_file`` (sha256) backs
that up if the same attachment arrives in a different mail. Per-message
errors are isolated — a broken mail is logged and left in place for the
next poll, and never aborts the run. A message whose Processed-move fails
is retried whole on the next poll: the retry re-ingests as all-duplicates
and may re-run the label pass — tolerated, because that spend is recorded
(anchored on the duplicate document) and bounded by the daily label budget.
The dropped-attachments push fires only *after* a successful move, so it is
at-most-once per message, never repeated across retries.

IMAP I/O is synchronous (imap-tools); the periodic task runs
``poll_mailbox`` in a worker thread via ``poll_mailbox_async`` while the
ingest calls themselves are marshalled back onto the event loop.
"""

import asyncio
import imaplib
import io
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, AsyncExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from anthropic import AsyncAnthropic
from imap_tools import AND, H, MailBox, MailMessage
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
from library.models import (
    DocumentSource,
    EmailSelectionTrace,
    HeldEmail,
    HeldEmailStatus,
    IngestionEvent,
    User,
)
from library.notifications import (
    dispatch_attachments_dropped_notification,
    dispatch_email_held_notification,
)

logger = logging.getLogger(__name__)

#: Upper bound (seconds) on each ``future.result()`` marshalled from the poll's
#: worker thread onto the event loop (ingest / notify / trace / label). A wedged
#: loop-side coroutine must never hang the poll thread forever. Deliberately a
#: constant, not a setting: it is a last-resort safety net, generous enough
#: (5 min) that a healthy call never trips it.
_LOOP_BRIDGE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class EmailPollSummary:
    """Outcome of one mailbox poll."""

    messages_seen: int = 0
    messages_processed: int = 0  # moved to the processed folder
    messages_skipped: int = 0  # errored (or allowlist-rejected, holds off); left in place
    messages_held: int = 0  # held for review (durable row + moved to the held folder)
    attachments_ingested: int = 0  # new documents created
    attachments_duplicate: int = 0  # content already in the library
    attachments_dropped: int = 0  # rejected (oversize/unsupported/error); surfaced
    attachments_filtered: int = 0  # deterministic noise (signature/tiny/decoration/non-document)


@dataclass(frozen=True, slots=True)
class SkippedAttachment:
    """One attachment that could not be turned into a document.

    ``reason`` is a stable code: ``empty`` (no payload — usually inline cruft,
    not surfaced to the user), ``oversize``, ``unsupported_type``, ``error``
    (an unexpected failure during ingest), one of the deterministic noise
    filters (``signature_image``, ``tiny_image``, ``decoration_image``,
    ``non_document_type`` — see ``_noise_reason``), or the LLM-corroborated
    skip (``llm_noise_corroborated`` — a ``probably_noise`` label verdict on an
    image that at least one decoration signal agrees with). ``detail`` is a human
    sentence. ``size``/``mime`` are the attachment's byte length and detected
    type, carried for the decision trace.
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

#: Quiet noise skip reasons: the deterministic gate's (``_noise_reason``) plus
#: the LLM-corroborated skip (``llm_noise_corroborated``, ``_ingest_attachments``).
#: Recorded in the decision trace and counted (``attachments_filtered``) but
#: never surfaced.
_NOISE_REASONS: frozenset[str] = frozenset(
    {
        "signature_image",
        "tiny_image",
        "decoration_image",
        "non_document_type",
        "llm_noise_corroborated",
    }
)

#: Every reason that means "this item was skipped, however quietly" — the
#: union of the quiet noise skips and the user-facing drops. An email whose
#: decision trace contains at least one of these gets a durable
#: ``email_selection_traces`` row (``_skip_trace_record``), and the settings
#: API's recent-skips view filters a stored trace down to these decisions.
#: Public: imported by ``library.api.settings``.
SKIP_TRACE_REASONS: frozenset[str] = _NOISE_REASONS | _USER_FACING_DROP_REASONS

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
    #: The human sentence behind ``reason`` (e.g. which decoration signals
    #: fired) — persisted in the ``email_selection`` event so the audit trail is
    #: self-explanatory, but kept out of the compact ``render()`` log token.
    detail: str | None = None

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
            "detail": self.detail,
        }

    def render(self) -> str:
        """Compact single-token rendering for the trace log line."""
        token = f"{self.filename or '<' + self.kind + '>'}:{self.stage}:{self.verdict}"
        return f"{token}({self.reason})" if self.reason else token


def _skip_verdict(reason: str) -> str:
    """A skipped attachment is a user-facing ``dropped`` or a quiet ``filtered``."""
    return "dropped" if reason in _USER_FACING_DROP_REASONS else "filtered"


def _skip_decision(item: SkippedAttachment, stage: str = "classify") -> SelectionDecision:
    """The decision for one skipped attachment (classify-stage by default;
    ``llm_label`` for the corroborated-noise skip)."""
    return SelectionDecision(
        kind="attachment",
        filename=item.filename,
        mime=item.mime,
        size=item.size,
        stage=stage,
        verdict=_skip_verdict(item.reason),
        reason=item.reason,
        detail=item.detail,
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
#: ``email_selection`` event on each new document, plus (when present) one
#: ``email_label_completed`` budget event on the anchor document — the first
#: document the email produced, new **or duplicate**, so an all-duplicate
#: re-send still records its spend. The async wrapper marshals it onto the
#: loop. Given the new document ids, the trace detail, the label detail, and
#: the anchor document id.
TracePersister = Callable[
    [list[int], dict[str, object], dict[str, object] | None, int | None], None
]


@dataclass(frozen=True, slots=True)
class SkipTraceRecord:
    """One durable ``email_selection_traces`` row: the per-email skip audit.

    Built (``_skip_trace_record``) only when the email's decision trace
    contains at least one skipped item (reason in :data:`SKIP_TRACE_REASONS`)
    — an email with no skips writes no row, keeping the table signal-only.
    ``decisions`` is the FULL ``SelectionDecision.as_detail()`` list (ingested
    siblings included), the same shape as the ``email_selection`` event's
    ``items``, so the row reads as a whole email.
    """

    message_id: str | None
    subject: str | None
    from_address: str | None
    decisions: list[dict[str, object]]


#: Synchronous bridge to persist one :class:`SkipTraceRecord` (best-effort,
#: like the ``email_selection`` trace: a failure logs and never fails the
#: poll). Unlike :data:`TracePersister` it is keyed on the email, not on
#: document ids, so it covers the zero-document case — an email whose items
#: were ALL skipped still leaves a durable, queryable audit row.
SkipTracePersister = Callable[[SkipTraceRecord], None]


@dataclass(frozen=True, slots=True)
class HoldRecord:
    """Everything ``_persist_held_email`` needs to write one ``held_emails`` row.

    ``trace`` is the ``_selection_event_detail`` shape (plus ``label_usage``
    when the LLM pass billed) so the review UI can show what the pipeline saw.
    ``imap_uid`` is a hint only — UIDs are folder-scoped and change on move;
    the Message-ID is the authoritative pointer for a later re-fetch.
    """

    message_id: str | None
    sender: str | None
    subject: str | None
    received_at: datetime | None
    verdict: str
    reason: str | None
    trace: dict[str, object]
    imap_uid: str | None


#: Synchronous bridge to persist the durable ``held_emails`` row for one held
#: message (poll_mailbox runs off-loop; the async wrapper marshals it onto the
#: loop). Unlike ``TracePersister`` this is NOT best-effort: it MUST raise on
#: failure — the row precedes the move, and a hold without its row would be
#: invisible to review — so the per-message handler leaves the mail in place
#: for the next poll to retry.
HoldPersister = Callable[[HoldRecord], None]


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

    def set(self, folder: str) -> Any: ...


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
    return MailBox(
        settings.email_host, settings.email_port, timeout=settings.email_imap_timeout_seconds
    ).login(
        settings.email_username.get_secret_value(),
        settings.email_password.get_secret_value(),
        initial_folder=settings.email_folder,
    )


def _message_id(message: MailMessage) -> str | None:
    """The raw Message-ID header, if present."""
    raw = message.obj["Message-ID"]
    return raw.strip() if raw else None


#: imap-tools' sentinel for an unparseable ``Date:`` header.
_UNPARSED_DATE = datetime(1900, 1, 1)


def _received_at(message: MailMessage) -> datetime | None:
    """The message's ``Date:`` header as a datetime, when present and parseable.

    imap-tools returns ``datetime(1900, 1, 1)`` for an unparseable header; both
    that sentinel and a missing header map to ``None``. A naive result is pinned
    to UTC so the timezone-aware ``held_emails.received_at`` column stores a
    deterministic instant. ``getattr`` guards a message stub (best-effort
    provenance must never itself raise).
    """
    if not (getattr(message, "date_str", "") or "").strip():
        return None
    value = getattr(message, "date", None)
    if not isinstance(value, datetime) or value == _UNPARSED_DATE:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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


def _image_dimensions(content: bytes) -> tuple[int, int] | None:
    """``(width, height)`` in px (header-only decode); ``None`` if undecodable.

    ``Image.open`` reads only the header, so this stays cheap. A decode failure
    returns ``None`` and the caller keeps the attachment (bias to ingest).
    """
    try:
        with Image.open(io.BytesIO(content)) as image:
            return image.size
    except Exception:
        return None


#: Filename stems that scream "decoration, not a document" — matched as
#: case-insensitive substrings of the stem (``company-logo`` counts).
_DECORATION_FILENAME_WORDS: tuple[str, ...] = ("logo", "signature", "banner", "footer", "icon")

#: Outlook's auto-generated names for embedded images: ``image`` + 2-3 digits
#: (``image001``). Full-stem match only — ``image1`` and ``image1234`` don't.
_DECORATION_OUTLOOK_STEM_RE = re.compile(r"image\d{2,3}")

#: Banner shape: at least this wide-to-tall, with a short edge at/under this —
#: the header/footer strips mail clients paste above signatures.
_BANNER_MIN_ASPECT_RATIO = 4.0
_BANNER_MAX_SHORT_EDGE_PX = 128


def _decoration_signals(
    filename: str | None,
    content: bytes,
    dimensions: tuple[int, int] | None,
    settings: Settings,
) -> dict[str, bool]:
    """Three independent "this is decoration, not a document" signals.

    Pure and standalone (no I/O, no logging; later work units reuse it): the
    caller supplies the already-decoded ``dimensions``. Each signal is weak on
    its own — a scan can be named ``logo.png``, a receipt photo can compress
    small — so the caller must require **at least two** before skipping.

    - ``filename``: the stem contains a decoration word (logo, signature,
      banner, footer, icon) or is an Outlook auto-embed name (``image001``).
    - ``size``: the payload is at/under ``email_filter_decoration_max_bytes``.
    - ``shape``: longest edge at/under ``email_filter_decoration_max_edge_px``,
      or banner-shaped (aspect >= 4:1 with a short edge <= 128px). ``None``
      dimensions never claim a shape.
    """
    stem = (filename or "").rsplit(".", 1)[0].strip().lower()
    filename_signal = bool(stem) and (
        any(word in stem for word in _DECORATION_FILENAME_WORDS)
        or _DECORATION_OUTLOOK_STEM_RE.fullmatch(stem) is not None
    )
    size_signal = len(content) <= settings.email_filter_decoration_max_bytes
    shape_signal = False
    if dimensions is not None:
        long_edge, short_edge = max(dimensions), min(dimensions)
        shape_signal = long_edge <= settings.email_filter_decoration_max_edge_px or (
            short_edge > 0
            and long_edge / short_edge >= _BANNER_MIN_ASPECT_RATIO
            and short_edge <= _BANNER_MAX_SHORT_EDGE_PX
        )
    return {"filename": filename_signal, "size": size_signal, "shape": shape_signal}


def _noise_reason(
    attachment: Any,
    content: bytes,
    mime: str | None,
    html: str,
    settings: Settings,
    *,
    override: bool = False,
) -> tuple[str, str] | None:
    """A deterministic ``(reason, detail)`` when an attachment is unambiguous noise.

    Conservative by design (bias to ingest): only a non-document protocol part,
    an inline/CID signature image, a sub-threshold tiny image, or a decodable
    image with **at least two** independent decoration signals
    (``_decoration_signals``: filename, size, shape) trips a rule — byte size
    alone never skips, and neither does a decoration-style filename alone. A
    normal-sized image is kept; an image we cannot decode is kept unless it is
    also below the byte-size floor (the tiny-image fallback). ``override`` is
    the ingest-anyway path (``ingest_held_email_async``): a human clicked
    "ingest anyway", so the *heuristic* ``decoration_image`` rule yields to that
    intent while the hard gates (non-document part, signature image, tiny
    image) still apply. Returns ``None`` to keep.
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
        dimensions = _image_dimensions(content)
        if dimensions is not None:
            edge = max(dimensions)
            if edge <= settings.email_filter_tiny_image_max_edge_px:
                return "tiny_image", f"longest edge {edge}px is at/under the tiny-image threshold"
            # Decoration gate (doc-159): a non-inline logo/banner slips past the
            # tiny-image rule, but never past two agreeing decoration signals.
            # Heuristic, so the ingest-anyway override bypasses it.
            if not override:
                signals = _decoration_signals(
                    getattr(attachment, "filename", None) or None, content, dimensions, settings
                )
                fired = [name for name, hit in signals.items() if hit]
                if len(fired) >= 2:
                    return (
                        "decoration_image",
                        f"decoration image ({len(fired)}/3 signals fired: {', '.join(fired)})",
                    )
        elif len(content) < settings.email_filter_tiny_image_max_bytes:
            return "tiny_image", f"undecodable image, {len(content)} bytes below the threshold"
    return None


def _classify_attachments(
    message: MailMessage, max_bytes: int, settings: Settings, *, override: bool = False
) -> tuple[list[IngestCandidate], list[SkippedAttachment]]:
    """Split a message's attachments into ingestable candidates and skips.

    Side-effect free (it only sniffs bytes) so it can run *before* any
    ingestion — the poller needs the full skip list up front to stamp the
    survivors with their dropped siblings. Every rejected attachment becomes a
    :class:`SkippedAttachment` instead of vanishing with a bare ``continue``.
    ``override`` (the ingest-anyway path) is threaded into ``_noise_reason`` so
    the heuristic ``decoration_image`` rule yields to the human's intent; the
    hard gates (empty/oversize/noise/unsupported) still apply.
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
        noise = _noise_reason(attachment, content, mime, html, settings, override=override)
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


def _label_items(
    candidates: list[IngestCandidate], body: IngestCandidate | None, body_skip_reason: str
) -> list[LabelItem]:
    """The item manifest for one message-level label call.

    Attachments come first (indices ``0..n-1``, matching their candidate order,
    so the caller can map verdicts back). The message body is a judged item of
    its own — index ``len(candidates)``, ``kind="body"`` — when it is a viable
    candidate OR when it failed only the substance gate (a thin cover note
    still informs the whole-email verdict; body-only emails must be judged). A
    blank or oversize body is context only, never an item.
    """
    items = [
        LabelItem(index=index, filename=c.filename, mime=c.mime, size=len(c.content))
        for index, c in enumerate(candidates)
    ]
    if body is not None:
        items.append(
            LabelItem(
                index=len(candidates),
                filename=body.filename,
                mime=body.mime,
                size=len(body.content),
                kind="body",
            )
        )
    elif body_skip_reason.startswith("below_substance"):
        items.append(
            LabelItem(index=len(candidates), filename=None, mime=None, size=None, kind="body")
        )
    return items


def _label_request(
    message: MailMessage, items: list[LabelItem], settings: Settings
) -> LabelRequest:
    """Build the LLM label request for one message's judged items."""
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


def _corroborating_signals(candidate: IngestCandidate, settings: Settings) -> list[str]:
    """Decoration-signal names corroborating an LLM ``probably_noise`` verdict.

    The gate for the ``llm_noise_corroborated`` skip: only an image can be
    decoration, and the check rides the same feature flag as the deterministic
    noise gate. Returns the fired ``_decoration_signals`` names — empty for a
    non-image, a disabled gate, or a signal-free image, in which case the
    caller keeps today's ingest-and-flag disposition.
    """
    if not settings.email_filter_noise_enabled:
        return []
    declared = (candidate.mime or "").split(";", 1)[0].strip().lower()
    if not _is_image(declared, detect_mime(candidate.content, candidate.mime)):
        return []
    signals = _decoration_signals(
        candidate.filename, candidate.content, _image_dimensions(candidate.content), settings
    )
    return [name for name, hit in signals.items() if hit]


def _ingest_attachments(
    message: MailMessage,
    ingest: IngestCallable,
    candidates: list[IngestCandidate],
    classify_skipped: list[SkippedAttachment],
    verdicts: dict[int, tuple[str, str | None]],
    settings: Settings,
) -> _AttachmentOutcome:
    """Ingest one message's surviving attachment candidates.

    ``candidates``/``classify_skipped`` come from ``_classify_attachments`` and
    ``verdicts`` from the (message-level) label pass, keyed by candidate index —
    the label call itself is hoisted to ``poll_mailbox`` so the whole email is
    judged once, body included. A ``probably_noise`` verdict normally flags the
    document (``extra["email_selection"]``) but **still ingests** — except when
    the item is an image and at least one deterministic decoration signal
    (``_corroborating_signals``) agrees: two independent judges calling it
    decoration make it a quiet ``llm_noise_corroborated`` skip instead (the
    doc-159 fix; noise-gate flag off, a non-image, or zero signals all keep
    today's ingest-and-flag). Each survivor is stamped with the email's dropped
    siblings (so validation can surface them). Returns an
    :class:`_AttachmentOutcome` with counts, skips, the per-attachment decision
    trace, and the ingest results. A per-attachment failure — a content
    rejection (``IngestError``) or any other exception — is recorded as a skip
    and never aborts the message. The ingest-anyway override never reaches the
    corroborated skip: it passes empty ``verdicts`` (no label call is made for
    an override).
    """
    skipped = list(classify_skipped)
    # Only the classify-pass drops can be stamped onto survivors at creation
    # (ingest-pass errors below are not yet known and would race the async
    # processing job); that is exactly the common "unsupported sibling" case.
    dropped_siblings = _dropped_siblings_payload(skipped)
    decisions: list[SelectionDecision] = [_skip_decision(item) for item in skipped]

    produced: list[IngestResult] = []
    new = duplicates = 0
    for index, candidate in enumerate(candidates):
        verdict = verdicts.get(index)
        flagged = verdict is not None and verdict[0] == "probably_noise"
        if flagged:
            corroborating = _corroborating_signals(candidate, settings)
            if corroborating:
                # The LLM and at least one deterministic decoration signal
                # agree: skip quietly (like the other noise reasons) instead
                # of ingest-and-flag. The detail keeps both judges' reasoning.
                skip = SkippedAttachment(
                    candidate.filename,
                    "llm_noise_corroborated",
                    f"LLM judged probably-noise ({verdict[1] or 'no reason given'}); "
                    f"corroborated by decoration signal(s): {', '.join(corroborating)}",
                    size=len(candidate.content),
                    mime=candidate.mime,
                )
                logger.info("email: attachment %r skipped (%s)", candidate.filename, skip.detail)
                skipped.append(skip)
                decisions.append(_skip_decision(skip, stage="llm_label"))
                continue
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
    return _AttachmentOutcome(new, duplicates, skipped, decisions, produced)


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
#: Public (no underscore): surfaced read-only by GET /api/settings/email-triage.
#: Module constants, not Settings fields — changing them is a code change.
BODY_MIN_WORDS = 40
BODY_MIN_CHARS = 240

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


def _body_candidate(
    message: MailMessage, max_bytes: int, *, ignore_substance: bool = False
) -> tuple[IngestCandidate | None, str]:
    """Build a candidate from the email body, gated on substance.

    HTML (converted to Markdown, ``text/markdown``) is preferred over plain text
    (``text/plain``). The body is stripped of quoted replies / signatures /
    mobile footers (:func:`_body_substance`) and must clear the substance
    threshold — this filters contentless cover notes ("FYI see attached") while
    still filing a genuine body-as-invoice. Returns ``(candidate, "")`` on
    success, or ``(None, reason)`` where ``reason`` is ``blank`` / ``oversize`` /
    ``below_substance:<n>w`` for the decision trace. Called only when no
    attachment produced a document. ``ignore_substance`` is the ingest-anyway
    override (``ingest_held_email_async``): a *non-empty* body is filed even
    below the threshold — a ``below_substance`` hold must ingest its body when a
    human says so — while blank/oversize still apply (there is nothing to file,
    or it cannot be stored).
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
    if words < BODY_MIN_WORDS and len(body) < BODY_MIN_CHARS and not (ignore_substance and body):
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
    """Emit the single-line, greppable decision trace for one email.

    This is the primary debug/triage surface: it records what happened to every
    item — ``[body, att1 … attN]`` — at which stage and why, even when the email
    produced no document (the persisted ``email_selection`` event cannot cover
    that case, as it has no document to hang on). Emitted once per processed
    message and once per held message (``_hold_message`` traces too, so even an
    allowlist hold leaves a line); the only untraced mail is an allowlist reject
    while holds are off (left in place) and a message whose processing raised.
    The stable ``email-selection`` prefix lets Loki/``grep`` pull one email's
    full trace. See docs/runbooks/email-triage.md for the format and the
    action-per-reason table.
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


def _skip_trace_record(
    message: MailMessage, decisions: list[SelectionDecision]
) -> SkipTraceRecord | None:
    """The durable skip-audit row for this email, or ``None`` when nothing was
    skipped (the body's bookkeeping skips — ``not_needed``/``no_body``/
    ``below_substance`` — deliberately don't count: only reasons in
    :data:`SKIP_TRACE_REASONS` make an email worth a row)."""
    if not any(decision.reason in SKIP_TRACE_REASONS for decision in decisions):
        return None
    return SkipTraceRecord(
        message_id=_message_id(message),
        subject=message.subject,
        from_address=message.from_,
        decisions=[decision.as_detail() for decision in decisions],
    )


def _body_decision(
    body: IngestCandidate, verdict: str, reason: str | None = None, stage: str = "body_substance"
) -> SelectionDecision:
    """The body-stage decision for a body candidate that reached ingest."""
    return SelectionDecision(
        kind="body",
        filename=body.filename,
        mime=body.mime,
        size=len(body.content),
        stage=stage,
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


def _hold_message(
    mailbox: MailboxProtocol,
    message: MailMessage,
    settings: Settings,
    persist_hold: HoldPersister | None,
    decisions: list[SelectionDecision],
    *,
    verdict: str,
    reason: str | None,
    label_usage: LabelUsage | None = None,
) -> None:
    """Hold one message for review: durable row first, then move to the Held folder.

    The ordering is load-bearing (row-before-move): ``persist_hold`` failures
    PROPAGATE to the per-message handler, which leaves the mail in place for the
    next poll — a hold must never move a message without its row. A failed
    *move* leaves the row behind instead; the retry's insert is skip-if-exists
    (backed by the partial unique index on ``message_id WHERE status='held'``)
    and completes the move. The whole-email decision is appended to the trace so
    both the log line and the persisted row show it. ``label_usage`` (when the
    LLM pass billed for this email) rides in ``trace["label_usage"]`` — the held
    email produced no document, so there is no budget-event anchor. When
    ``persist_hold`` is ``None`` (direct ``poll_mailbox`` wiring in tests) the
    row is skipped and only the move happens.
    """
    decisions.append(
        SelectionDecision(
            kind="email",
            filename=None,
            mime=None,
            size=None,
            stage="email_verdict",
            verdict="held",
            reason=reason or verdict,
        )
    )
    _log_selection_trace(message, decisions)
    if not mailbox.folder.exists(settings.email_held_folder):
        mailbox.folder.create(settings.email_held_folder)
    trace = _selection_event_detail(message, decisions)
    if label_usage is not None:
        trace["label_usage"] = label_usage.as_detail()
    if persist_hold is not None:
        persist_hold(
            HoldRecord(
                message_id=_message_id(message),
                sender=message.from_,
                subject=message.subject,
                received_at=_received_at(message),
                verdict=verdict,
                reason=reason,
                trace=trace,
                imap_uid=message.uid,
            )
        )
    mailbox.move(message.uid, settings.email_held_folder)
    logger.info(
        "email: message %r held for review (%s: %s); moved to %r",
        message.subject,
        verdict,
        reason,
        settings.email_held_folder,
    )


def poll_mailbox(
    settings: Settings,
    ingest: IngestCallable,
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
    notify: DropNotifier | None = None,
    persist_trace: TracePersister | None = None,
    label: LabelCallable | None = None,
    persist_hold: HoldPersister | None = None,
    persist_skip_trace: SkipTracePersister | None = None,
) -> EmailPollSummary:
    """Poll the configured mailbox once and ingest its documents.

    Fetches every message in ``email_folder`` (``ALL``, not unseen-only —
    the seen flag is fragile when a human also reads the mailbox) and, per
    message: checks the sender against ``email_allowed_senders`` (when
    non-empty), classifies the attachments, runs the optional message-level
    LLM label pass (``label``) over the survivors plus the body, ingests
    each supported attachment via ``ingest`` and — when no attachment
    produced a document — the email body itself (HTML preferred, else plain
    text), then moves the message to ``email_processed_folder`` — the move is
    what makes polling idempotent. Errors are isolated per message: the mail
    is left in place for the next poll and the run continues.

    A message the pipeline judges not library-worthy is **held** instead of
    processed (``email_hold_enabled``): an allowlist-rejected sender, a
    body-only email below the substance gate, an email whose user-facing drops
    left nothing ingested (all-duplicate emails still file to Processed), or an
    LLM ``email_verdict="hold"`` each write a durable row via ``persist_hold``
    (raising on failure — see :class:`HoldPersister`) and move the message to
    ``email_held_folder`` instead. Held messages skip the Processed move, the
    drop notification, and the ``processed`` count. With holds disabled the
    pre-hold behavior applies: rejected senders stay in place, everything else
    files to Processed.

    Attachments that cannot become documents are no longer dropped silently:
    each is recorded, the survivors of the same email are stamped with them (so
    validation can flag the document), and ``notify`` — when supplied — is
    called once per message with drops so the owner gets a push. The push fires
    only after the message has been safely moved, so it is at-most-once per
    message. Nothing is lost quietly.

    Every message also emits a one-line decision trace (``_log_selection_trace``)
    recording what happened to each item and why; when ``persist_trace`` is
    supplied, that trace is also stored as an ``email_selection`` event on each
    new document so it shows in the document's history. When
    ``persist_skip_trace`` is supplied, a processed email whose trace contains
    at least one skipped item (reason in :data:`SKIP_TRACE_REASONS`, however
    quiet) additionally writes one durable ``email_selection_traces`` row —
    covering the zero-document email the per-document event cannot (held
    emails don't write one: their trace already lives on the ``held_emails``
    row). Both persisters are best-effort and never fail the poll.

    No-op (empty summary) when ``email_host`` is unset. ``mailbox_factory``,
    ``notify``, ``persist_trace``, and ``persist_hold`` exist for wiring/tests;
    the default connects per ``settings``.
    """
    if settings.email_host is None:
        logger.debug("email: LIBRARY_EMAIL_HOST unset; poller disabled")
        return EmailPollSummary()
    factory = mailbox_factory or (lambda: _connect(settings))
    allowed = frozenset(settings.email_allowed_senders)
    hold_enabled = settings.email_hold_enabled
    seen = processed = skipped = held = ingested = duplicates = dropped = filtered = 0
    try:
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
                        if hold_enabled and settings.email_hold_unknown_senders:
                            logger.warning(
                                "email: message %r from %r not in allowlist; holding for review",
                                message.subject,
                                sender,
                            )
                            _hold_message(
                                mailbox,
                                message,
                                settings,
                                persist_hold,
                                [],
                                verdict="sender_unknown",
                                reason=f"sender {sender or '(unknown)'} not in allowlist",
                            )
                            held += 1
                        else:
                            logger.warning(
                                "email: message %r from %r not in allowlist; left in place",
                                message.subject,
                                sender,
                            )
                            skipped += 1
                        continue
                    candidates, classify_skipped = _classify_attachments(
                        message, settings.max_upload_bytes, settings
                    )
                    filtered += sum(1 for item in classify_skipped if item.reason in _NOISE_REASONS)
                    # Precompute the body candidate so the label pass judges the
                    # body too (body-only emails included); whether the body is
                    # INGESTED is still decided below, only after the attachments
                    # produced nothing — exactly as before.
                    body, body_skip_reason = _body_candidate(message, settings.max_upload_bytes)
                    label_outcome = LabelOutcome({}, None, None)
                    if label is not None:
                        label_items = _label_items(candidates, body, body_skip_reason)
                        if label_items:
                            label_outcome = label(_label_request(message, label_items, settings))
                            if label_outcome.skip_reason:
                                logger.info(
                                    "email-label: pass skipped (%s) for %r",
                                    label_outcome.skip_reason,
                                    message.subject,
                                )
                    if hold_enabled and label_outcome.email_verdict == "hold":
                        # The LLM judged the WHOLE email as not library material:
                        # nothing is ingested; the message waits in review. The
                        # label pass is fail-open, so this branch is unreachable
                        # on any label failure (email_verdict stays "file").
                        _hold_message(
                            mailbox,
                            message,
                            settings,
                            persist_hold,
                            [_skip_decision(item) for item in classify_skipped],
                            verdict="llm_hold",
                            reason=label_outcome.email_reason,
                            label_usage=label_outcome.usage,
                        )
                        held += 1
                        continue
                    outcome = _ingest_attachments(
                        message,
                        ingest,
                        candidates,
                        classify_skipped,
                        {
                            index: verdict
                            for index, verdict in label_outcome.verdicts.items()
                            if index < len(candidates)
                        },
                        settings,
                    )
                    # Corroborated LLM-noise skips arise inside the ingest pass
                    # (they need the label verdicts), so they are counted here —
                    # the classify-pass noise was already counted above.
                    filtered += sum(
                        1 for item in outcome.skipped if item.reason == "llm_noise_corroborated"
                    )
                    new, dups = outcome.new, outcome.duplicates
                    dropped_attachments = outcome.skipped
                    decisions = outcome.decisions
                    produced = list(outcome.produced)
                    # Surface every user-facing drop: log a summary and count it.
                    # Done after ingest so ingest-pass errors count too (held
                    # messages count their drops as well). The push itself waits
                    # until after the Processed move (below), so a failed move
                    # can never notify the owner twice across retries.
                    user_facing = [
                        item
                        for item in dropped_attachments
                        if item.reason in _USER_FACING_DROP_REASONS
                    ]
                    if user_facing:
                        _log_dropped(message.subject, dropped_attachments)
                        dropped += len(user_facing)
                    if new == 0 and dups == 0:
                        if body is not None:
                            # The body is now this email's only document, so it must
                            # carry the dropped siblings too — otherwise the
                            # "attachments couldn't be added" review reason would be
                            # absent exactly when every attachment was dropped.
                            dropped_siblings = _dropped_siblings_payload(dropped_attachments)
                            body_verdict = label_outcome.verdicts.get(len(candidates))
                            body_flagged = (
                                body_verdict is not None and body_verdict[0] == "probably_noise"
                            )
                            extra: dict[str, object] = {}
                            if dropped_siblings:
                                extra["email_siblings_dropped"] = dropped_siblings
                            if body_flagged:
                                # Ingest-and-flag, identical to a flagged attachment:
                                # the LLM only annotates, never drops.
                                extra["email_selection"] = {
                                    "verdict": "probably_noise",
                                    "reason": body_verdict[1],
                                    "source": "llm_label",
                                }
                            if extra:
                                body = replace(body, extra_document=extra)
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
                                elif body_flagged:
                                    new += 1
                                    decisions.append(
                                        _body_decision(
                                            body,
                                            "flagged_ambiguous",
                                            body_verdict[1],
                                            stage="llm_label",
                                        )
                                    )
                                else:
                                    new += 1
                                    decisions.append(_body_decision(body, "ingested"))
                        elif (
                            hold_enabled
                            and settings.email_hold_below_substance
                            and body_skip_reason.startswith("below_substance")
                        ):
                            # Nothing ingested and the body is a thin cover note:
                            # hold instead of quietly filing the email away.
                            decisions.append(_body_skip_decision(body_skip_reason))
                            _hold_message(
                                mailbox,
                                message,
                                settings,
                                persist_hold,
                                decisions,
                                verdict="below_substance",
                                reason=body_skip_reason,
                                label_usage=label_outcome.usage,
                            )
                            held += 1
                            continue
                        else:
                            decisions.append(_body_skip_decision(body_skip_reason or "no_body"))
                    else:
                        decisions.append(_body_skip_decision("not_needed"))
                    if hold_enabled and new == 0 and dups == 0 and user_facing:
                        # User-facing drops and NOTHING ingested (new or duplicate):
                        # moving to Processed would file the drops out of sight, so
                        # hold instead. All-duplicate emails still file to Processed
                        # (the content is already in the library).
                        _hold_message(
                            mailbox,
                            message,
                            settings,
                            persist_hold,
                            decisions,
                            verdict="nothing_ingested",
                            reason="no attachment or body produced a document",
                            label_usage=label_outcome.usage,
                        )
                        held += 1
                        continue
                    ingested += new
                    duplicates += dups
                    # The always-on decision trace (covers the zero-document case too),
                    # then persist it onto each new document — best-effort and keyed on
                    # document_id. The zero-document gap is closed by the per-email
                    # skip-trace row below: an email with at least one skipped item
                    # leaves a durable row even when it produced nothing.
                    _log_selection_trace(message, decisions)
                    if persist_skip_trace is not None:
                        skip_record = _skip_trace_record(message, decisions)
                        if skip_record is not None:
                            persist_skip_trace(skip_record)
                    if persist_trace is not None:
                        new_document_ids = [
                            result.document.id for result in produced if not result.duplicate
                        ]
                        # The label budget event anchors on the FIRST produced
                        # document — new or duplicate — so an all-duplicate
                        # re-send still records its spend for the budget gate.
                        anchor_id = produced[0].document.id if produced else None
                        label_detail = (
                            label_outcome.usage.as_detail() if label_outcome.usage else None
                        )
                        if new_document_ids or (label_detail is not None and anchor_id is not None):
                            persist_trace(
                                new_document_ids,
                                _selection_event_detail(message, decisions),
                                label_detail,
                                anchor_id,
                            )
                    # The move is what makes polling idempotent, so it precedes the
                    # (at-most-once) drop notification: on a move failure the
                    # message stays put and is re-run whole next poll — the retry
                    # ingests as all-duplicates and only then notifies.
                    try:
                        mailbox.move(message.uid, settings.email_processed_folder)
                    except Exception:
                        logger.error(
                            "email: failed to move message %r (uid %s) to %r; "
                            "message will be reprocessed next poll",
                            message.subject,
                            message.uid,
                            settings.email_processed_folder,
                            exc_info=True,
                        )
                        raise
                    processed += 1
                    if user_facing and notify is not None:
                        notify(message.from_, message.subject, dropped_attachments)
                except Exception:
                    logger.exception(
                        "email: failed to process message %r; left in place for the next poll",
                        message.subject,
                    )
                    skipped += 1
    except (OSError, TimeoutError, imaplib.IMAP4.error):
        # A dead/wedged server (socket timeout, dropped connection, protocol
        # error) aborts this poll, not the worker: whatever was already
        # processed is counted below, and everything else stays in the inbox
        # untouched for the next idempotent poll.
        logger.warning("email: poll aborted (imap timeout/connection error)", exc_info=True)
    summary = EmailPollSummary(
        messages_seen=seen,
        messages_processed=processed,
        messages_skipped=skipped,
        messages_held=held,
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
    anchor_id: int | None = None,
) -> None:
    """Append the per-email ``email_selection`` trace (and label budget event).

    Writes ``email_selection`` on every new document; when the LLM label pass
    billed, also writes one ``email_label_completed`` event (carrying its cost)
    on the **anchor** document — the first document the email produced, new or
    duplicate — this is what the label budget gate later sums. Anchoring on any
    produced document (not just new ones) keeps the budget honest for the
    all-duplicate re-send: the call still cost money and must count against the
    daily cap. Best-effort audit data: it never blocks the poll and never raises
    past this boundary. An email that produced no document at all has no anchor
    — that case is covered by the always-on trace log line, and its label spend
    (only possible when every judged item errored out of ingest) stays visible
    in the log alone.
    """
    if not document_ids and (label_detail is None or anchor_id is None):
        return
    try:
        async with session_factory() as session:
            for document_id in document_ids:
                session.add(
                    IngestionEvent(document_id=document_id, event="email_selection", detail=detail)
                )
            if label_detail is not None and anchor_id is not None:
                session.add(
                    IngestionEvent(
                        document_id=anchor_id,
                        event="email_label_completed",
                        detail=label_detail,
                    )
                )
            await session.commit()
    except Exception:  # audit trail must never take down a poll
        logger.exception("email: failed to persist selection trace for %s", document_ids)


async def _persist_skip_trace(
    session_factory: async_sessionmaker[AsyncSession], record: SkipTraceRecord
) -> None:
    """Insert the durable per-email skip-audit row (``email_selection_traces``).

    Best-effort, exactly like ``_persist_selection_trace``: a failure logs a
    warning and never fails the poll (or the ingest-anyway override) — the
    always-on trace log line still covers the email.
    """
    try:
        async with session_factory() as session:
            session.add(
                EmailSelectionTrace(
                    message_id=record.message_id,
                    subject=record.subject,
                    from_address=record.from_address,
                    decisions=list(record.decisions),
                )
            )
            await session.commit()
    except Exception:  # audit trail must never take down a poll
        logger.warning(
            "email: failed to persist skip trace for %r; continuing",
            record.subject,
            exc_info=True,
        )


async def _persist_held_email(
    session_factory: async_sessionmaker[AsyncSession],
    record: HoldRecord,
    *,
    imap_folder: str,
    default_owner_username: str | None = None,
    held_url_base: str | None = None,
) -> None:
    """Insert the durable ``held_emails`` row for one held message (idempotent).

    Skip-if-exists on ``(message_id, status='held')``: a retry after a failed
    Held-folder move finds the open row from the first attempt and returns
    without inserting (the partial unique index backstops a race). The owner is
    resolved from the sender exactly like a document's would be
    (``resolve_sender_owner``), so the review queue can scope to its user.
    ``imap_folder`` is the *held* folder — where the message lives once the
    move (or its retry) lands.

    Unlike the trace persister this RAISES on failure: the row precedes the
    move, and a hold without its row would be invisible to review — the
    per-message handler then leaves the mail in place for the next poll.

    After a *fresh* insert commits, a best-effort ``email_held`` push goes to
    the resolved owner (opt-in; deep-link built from ``held_url_base``). The
    skip-if-exists retry path never re-notifies, so the push is at-most-once
    per hold — and a notification failure is NOT a hold failure: it is caught
    and logged here, never raised into the row-before-move contract above.
    """
    async with session_factory() as session:
        if record.message_id is not None:
            existing = (
                await session.execute(
                    select(HeldEmail.id).where(
                        HeldEmail.message_id == record.message_id,
                        HeldEmail.status == HeldEmailStatus.HELD,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                logger.info(
                    "email: held row %s already open for message %r; not duplicating",
                    existing,
                    record.message_id,
                )
                return
        owner_id = await resolve_sender_owner(
            session, record.sender, default_owner_username=default_owner_username
        )
        session.add(
            HeldEmail(
                message_id=record.message_id,
                sender=record.sender,
                subject=record.subject,
                received_at=record.received_at,
                verdict=record.verdict,
                reason=record.reason,
                trace=dict(record.trace),
                imap_folder=imap_folder,
                imap_uid=record.imap_uid,
                owner_id=owner_id,
            )
        )
        await session.commit()
    try:
        await dispatch_email_held_notification(
            session_factory,
            owner_id,
            subject=record.subject,
            reason=record.reason or record.verdict,
            held_url_base=held_url_base,
        )
    except Exception:  # the push must never fail the hold (the row is committed)
        logger.warning(
            "email: email-held notification failed for %r; continuing",
            record.subject,
            exc_info=True,
        )


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
        try:
            return future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except TimeoutError:
            # Propagate: the caller records this candidate as an `error` skip
            # and its siblings continue. Cancel is best-effort — the loop-side
            # coroutine may already be past its await points.
            future.cancel()
            raise

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
        try:
            future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except Exception:  # best-effort by contract: a push must never wedge a poll
            future.cancel()
            logger.warning(
                "email: dropped-attachments notification failed for %r; continuing",
                subject,
                exc_info=True,
            )

    def persist_trace_on_loop(
        document_ids: list[int],
        detail: dict[str, object],
        label_detail: dict[str, object] | None,
        anchor_id: int | None,
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _persist_selection_trace(
                session_factory, document_ids, detail, label_detail, anchor_id
            ),
            loop,
        )
        try:
            future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except Exception:  # best-effort audit data: never blocks the poll
            future.cancel()
            logger.warning(
                "email: selection-trace persistence failed for %s; continuing",
                document_ids,
                exc_info=True,
            )

    def persist_skip_trace_on_loop(record: SkipTraceRecord) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _persist_skip_trace(session_factory, record),
            loop,
        )
        try:
            future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except Exception:  # best-effort audit data: never blocks the poll
            future.cancel()
            logger.warning(
                "email: skip-trace persistence failed for %r; continuing",
                record.subject,
                exc_info=True,
            )

    def persist_hold_on_loop(record: HoldRecord) -> None:
        # NOT best-effort (unlike notify/trace): a failure here must propagate
        # so the per-message handler leaves the mail in place — a hold must
        # never move a message without its durable row.
        future = asyncio.run_coroutine_threadsafe(
            _persist_held_email(
                session_factory,
                record,
                imap_folder=settings.email_held_folder,
                default_owner_username=default_owner,
                held_url_base=url_base,
            ),
            loop,
        )
        try:
            future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except TimeoutError:
            # Cancel is best-effort — the loop-side coroutine may already be
            # past its await points — and the timeout still propagates.
            future.cancel()
            raise

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
                try:
                    return future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
                except Exception:  # fail-open: the label pass may only ever add flags
                    future.cancel()
                    logger.warning(
                        "email-label: loop bridge failed or timed out; keeping all items",
                        exc_info=True,
                    )
                    return LabelOutcome({}, None, "error")

            label = label_on_loop

        return await asyncio.to_thread(
            poll_mailbox,
            settings,
            ingest_on_loop,
            mailbox_factory=mailbox_factory,
            notify=notify_on_loop,
            persist_trace=persist_trace_on_loop,
            label=label,
            persist_hold=persist_hold_on_loop,
            persist_skip_trace=persist_skip_trace_on_loop,
        )


# --- Held-email resolution: ingest anyway (override) and dismiss ---------------


@dataclass(frozen=True, slots=True)
class _OverrideOutcome:
    """What the ingest-anyway IMAP worker brought back to the event loop.

    ``error`` is recorded on the row's ``last_error`` (message not found, or a
    post-ingest move failure). ``trace_detail``/``new_document_ids`` feed the
    best-effort ``email_selection`` persistence, exactly like the poll path;
    ``skip_trace`` (set when a hard gate still filtered something during the
    override) feeds the best-effort per-email skip-audit row the same way.
    """

    error: str | None = None
    new_document_ids: list[int] = field(default_factory=list)
    trace_detail: dict[str, object] | None = None
    skip_trace: SkipTraceRecord | None = None


async def _mark_held_email_ingested(
    session_factory: async_sessionmaker[AsyncSession],
    held_email_id: int,
    resolved_by_id: int | None,
    document_ids: list[int],
) -> None:
    """Resolve the held row as ingested (status/resolver/timestamp/documents).

    NOT best-effort: this runs *before* the Processed-folder move (row before
    move, mirroring the hold path) and raising here leaves the row held and the
    message in the Held folder — the override can simply be retried.
    """
    async with session_factory() as session:
        row = await session.get(HeldEmail, held_email_id)
        if row is None:  # deleted mid-flight; nothing to resolve
            raise ValueError(f"held email {held_email_id} vanished during ingest-anyway")
        row.status = HeldEmailStatus.INGESTED
        row.resolved_by_id = resolved_by_id
        row.resolved_at = datetime.now(UTC)
        row.document_ids = list(document_ids)
        row.last_error = None
        await session.commit()


async def _record_held_email_error(
    session_factory: async_sessionmaker[AsyncSession], held_email_id: int, error: str
) -> None:
    """Stamp ``last_error`` on the held row (status untouched)."""
    async with session_factory() as session:
        row = await session.get(HeldEmail, held_email_id)
        if row is None:
            return
        row.last_error = error
        await session.commit()


async def ingest_held_email_async(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    held_email_id: int,
    resolved_by_id: int | None,
    *,
    mailbox_factory: Callable[[], AbstractContextManager[MailboxProtocol]] | None = None,
) -> None:
    """Ingest a held email anyway: the human override for a hold (W12).

    Re-fetches the message from the row's ``imap_folder`` by **Message-ID
    header search** — never by ``imap_uid``, which is folder-scoped and changed
    on the Held move — and runs the per-message processing with override
    semantics: the hard deterministic classify gates still apply (signature/CID
    images, tiny images, non-document parts, oversize, unsupported types), but
    the decoration heuristics — the ``decoration_image`` rule and the
    ``llm_noise_corroborated`` skip — are bypassed, the LLM label pass is NOT
    consulted (the human already overruled it), and the body-substance gate is
    bypassed when the attachments produce nothing, so overriding a
    ``below_substance`` hold files its body. Ingestion goes through the same
    ``_ingest_candidate`` path as the poll (source ``email``, owner resolved
    from the sender), and the decision trace is persisted as ``email_selection``
    events on the new documents.

    Ordering mirrors the hold's row-before-move: the row is resolved
    (``ingested`` + resolver + ``document_ids``) *before* the message moves
    Held → Processed; a move failure only stamps ``last_error`` (the documents
    are the truth — sha256 dedup makes any re-ingest a duplicate). A row that
    is not currently ``held`` makes the whole call a logged no-op, so a
    double-fired job cannot ingest twice. A message that cannot be found in the
    Held folder stamps ``last_error`` and leaves the row held.

    Runs the synchronous IMAP work in a worker thread (like
    ``poll_mailbox_async``), marshalling the ingest and row writes back onto
    this event loop.
    """
    async with session_factory() as session:
        row = await session.get(HeldEmail, held_email_id)
    if row is None:
        logger.warning("email: held email %s not found; nothing to ingest", held_email_id)
        return
    if row.status is not HeldEmailStatus.HELD:
        logger.info(
            "email: held email %s is already %s; ingest-anyway is a no-op",
            held_email_id,
            row.status.value,
        )
        return
    message_id = row.message_id
    held_folder = row.imap_folder
    if message_id is None:
        await _record_held_email_error(
            session_factory,
            held_email_id,
            "held email has no Message-ID; cannot re-fetch it from IMAP",
        )
        return

    loop = asyncio.get_running_loop()
    default_owner = settings.email_default_owner
    factory = mailbox_factory or (lambda: _connect(settings))
    criteria = str(AND(header=H("Message-ID", message_id)))

    def ingest_on_loop(candidate: IngestCandidate) -> IngestResult:
        future = asyncio.run_coroutine_threadsafe(
            _ingest_candidate(session_factory, candidate, default_owner_username=default_owner),
            loop,
        )
        try:
            return future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except TimeoutError:
            future.cancel()
            raise

    def resolve_row_on_loop(document_ids: list[int]) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _mark_held_email_ingested(session_factory, held_email_id, resolved_by_id, document_ids),
            loop,
        )
        try:
            future.result(timeout=_LOOP_BRIDGE_TIMEOUT_SECONDS)
        except TimeoutError:
            future.cancel()
            raise

    def run() -> _OverrideOutcome:
        with factory() as mailbox:
            mailbox.folder.set(held_folder)
            # The IMAP HEADER search is substring-based; pin the exact id.
            matches = [
                message
                for message in mailbox.fetch(criteria, mark_seen=False)
                if _message_id(message) == message_id
            ]
            if not matches:
                return _OverrideOutcome(
                    error=f"message {message_id} not found in folder {held_folder!r}"
                )
            message = matches[0]
            # Override semantics: the hard deterministic gates only — the
            # decoration heuristic yields (override=True) and there is no
            # label call (empty verdicts), so neither decoration_image nor
            # llm_noise_corroborated can veto the human's "ingest anyway".
            candidates, classify_skipped = _classify_attachments(
                message, settings.max_upload_bytes, settings, override=True
            )
            outcome = _ingest_attachments(
                message, ingest_on_loop, candidates, classify_skipped, {}, settings
            )
            decisions = outcome.decisions
            produced = list(outcome.produced)
            if outcome.new == 0 and outcome.duplicates == 0:
                body, body_skip_reason = _body_candidate(
                    message, settings.max_upload_bytes, ignore_substance=True
                )
                if body is not None:
                    dropped_siblings = _dropped_siblings_payload(outcome.skipped)
                    if dropped_siblings:
                        body = replace(
                            body, extra_document={"email_siblings_dropped": dropped_siblings}
                        )
                    try:
                        result = ingest_on_loop(body)
                    except IngestError as exc:
                        logger.warning(
                            "email: body of held message %r rejected (%s)", message.subject, exc
                        )
                        decisions.append(_body_decision(body, "dropped", "rejected"))
                    else:
                        produced.append(result)
                        decisions.append(
                            _body_decision(body, "duplicate" if result.duplicate else "ingested")
                        )
                else:
                    decisions.append(_body_skip_decision(body_skip_reason or "no_body"))
            else:
                decisions.append(_body_skip_decision("not_needed"))
            decisions.append(
                SelectionDecision(
                    kind="email",
                    filename=None,
                    mime=None,
                    size=None,
                    stage="email_verdict",
                    verdict="ingested",
                    reason="manual_override",
                )
            )
            _log_selection_trace(message, decisions)
            document_ids = [result.document.id for result in produced]
            new_document_ids = [result.document.id for result in produced if not result.duplicate]
            trace_detail = _selection_event_detail(message, decisions)
            # Row before move: resolve first (raising on failure so the retry
            # sees a still-held row), then file the message away.
            resolve_row_on_loop(document_ids)
            error: str | None = None
            try:
                if not mailbox.folder.exists(settings.email_processed_folder):
                    mailbox.folder.create(settings.email_processed_folder)
                mailbox.move(message.uid, settings.email_processed_folder)
            except Exception as exc:
                logger.error(
                    "email: held email %s ingested but the move of message %r to %r failed; "
                    "status stays ingested (document_ids are the truth)",
                    held_email_id,
                    message.subject,
                    settings.email_processed_folder,
                    exc_info=True,
                )
                error = (
                    f"ingested (documents {document_ids}) but the move to "
                    f"{settings.email_processed_folder!r} failed: {exc}"
                )
            return _OverrideOutcome(
                error=error,
                new_document_ids=new_document_ids,
                trace_detail=trace_detail,
                skip_trace=_skip_trace_record(message, decisions),
            )

    outcome = await asyncio.to_thread(run)
    if outcome.trace_detail is not None and outcome.new_document_ids:
        await _persist_selection_trace(
            session_factory, outcome.new_document_ids, outcome.trace_detail
        )
    if outcome.skip_trace is not None:
        # The override bypasses the decoration heuristics but the hard gates
        # still filter (tiny/signature/non-document/oversize/unsupported), so
        # its skips get the same durable audit row as a poll's.
        await _persist_skip_trace(session_factory, outcome.skip_trace)
    if outcome.error is not None:
        logger.warning("email: held email %s: %s", held_email_id, outcome.error)
        await _record_held_email_error(session_factory, held_email_id, outcome.error)


async def dismiss_held_email(
    session: AsyncSession, held_email_id: int, resolved_by_id: int | None
) -> HeldEmail:
    """Dismiss a held email: a pure DB status flip, no IMAP round-trip (D5).

    The message's bytes stay in the Held folder forever (the row keeps the
    pointer), so "never lose a real document" survives a wrong dismiss — a
    human can still recover the mail by hand. Raises ``ValueError`` when the
    row does not exist or is not currently ``held`` (the API maps it to 409).
    Commits and returns the updated row.
    """
    row = await session.get(HeldEmail, held_email_id)
    if row is None:
        raise ValueError(f"held email {held_email_id} does not exist")
    if row.status is not HeldEmailStatus.HELD:
        raise ValueError(f"held email {held_email_id} is already {row.status.value}")
    row.status = HeldEmailStatus.DISMISSED
    row.resolved_by_id = resolved_by_id
    row.resolved_at = datetime.now(UTC)
    await session.commit()
    return row
