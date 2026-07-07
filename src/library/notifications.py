"""Pushover push notifications (per-user, opt-in).

Each user supplies their *own* Pushover application token and user key in
their notification settings (see ``library.schemas`` notification settings and
``docs/jobs-and-notifications.md``); we POST form-encoded to the Pushover
messages API. There is no official Pushover Python SDK, so this uses the
project's existing ``httpx`` dependency.

The module has two layers:

* Low-level API calls — :func:`send_pushover` and :func:`validate_pushover` —
  which never raise on an *API-level* failure (a bad token, a quota hit). They
  return a result object so callers decide how to react. Transport errors are
  likewise folded into the result rather than raised.
* The higher-level :func:`dispatch_document_notification`, which loads a
  document's owner, consults their settings, and sends at most one push. It is
  fully best-effort: any failure is logged and swallowed so it can never fail
  the pipeline job or an ingest request (mirroring
  ``library.jobs.notify_document_event``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library.models import Document, User
from library.schemas import NotificationEvent, get_notification_credentials

logger = logging.getLogger(__name__)

#: Base for the Pushover REST API. A module attribute so tests can point the
#: client at an ``httpx.MockTransport`` URL without monkeypatching internals.
PUSHOVER_API_BASE = "https://api.pushover.net/1"
_TIMEOUT_S = 10.0

# Pushover priorities (docs: https://pushover.net/api#priority). We use only
# normal and high — never emergency (which mandates retry/expire).
_PRIORITY_NORMAL = 0
_PRIORITY_HIGH = 1


@dataclass(frozen=True, slots=True)
class PushoverResult:
    """Outcome of a single Pushover messages call."""

    ok: bool
    request_id: str | None = None
    errors: tuple[str, ...] = ()
    invalid_token: bool = False
    invalid_user: bool = False
    app_remaining: int | None = None


@dataclass(frozen=True, slots=True)
class PushoverValidation:
    """Outcome of a Pushover ``users/validate`` call."""

    valid: bool
    errors: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()


def _app_remaining(headers: httpx.Headers) -> int | None:
    """Parse the ``X-Limit-App-Remaining`` quota header, if present."""
    raw = headers.get("X-Limit-App-Remaining")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_message_response(response: httpx.Response) -> PushoverResult:
    """Map a Pushover messages response to a :class:`PushoverResult`.

    Pushover returns HTTP 200 with ``{"status": 1, ...}`` on success and a 4xx
    with ``{"status": 0, "errors": [...]}`` on bad input, echoing the offending
    field (``"token": "invalid"`` / ``"user": "invalid"``) so we can tell which
    credential is wrong.
    """
    remaining = _app_remaining(response.headers)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if body.get("status") == 1:
        return PushoverResult(ok=True, request_id=body.get("request"), app_remaining=remaining)
    errors = tuple(str(item) for item in body.get("errors", [])) or (
        f"HTTP {response.status_code}",
    )
    return PushoverResult(
        ok=False,
        request_id=body.get("request"),
        errors=errors,
        invalid_token=body.get("token") == "invalid",
        invalid_user=body.get("user") == "invalid",
        app_remaining=remaining,
    )


async def send_pushover(
    *,
    app_token: str,
    user_key: str,
    message: str,
    title: str | None = None,
    url: str | None = None,
    url_title: str | None = None,
    device: str | None = None,
    priority: int = _PRIORITY_NORMAL,
    client: httpx.AsyncClient | None = None,
) -> PushoverResult:
    """Send one Pushover message; never raises on an API or transport failure.

    Posts ``application/x-www-form-urlencoded`` (``data=``, not ``json=``) to
    ``messages.json``. Returns ``ok=False`` with ``errors`` populated on a bad
    token/user, a quota hit, or a transport error.
    """
    data: dict[str, str] = {
        "token": app_token,
        "user": user_key,
        "message": message,
        "priority": str(priority),
    }
    for key, value in (
        ("title", title),
        ("url", url),
        ("url_title", url_title),
        ("device", device),
    ):
        if value:
            data[key] = value

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        try:
            response = await client.post(f"{PUSHOVER_API_BASE}/messages.json", data=data)
        except httpx.HTTPError as error:
            return PushoverResult(ok=False, errors=(f"request failed: {error}",))
        return _parse_message_response(response)
    finally:
        if owns_client:
            await client.aclose()


async def validate_pushover(
    *,
    app_token: str,
    user_key: str,
    device: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PushoverValidation:
    """Validate a Pushover token/user(/device) without sending a message.

    Used at save time so a typo surfaces immediately. ``status == 1`` means the
    pair is usable and the account has at least one active device.
    """
    data: dict[str, str] = {"token": app_token, "user": user_key}
    if device:
        data["device"] = device

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        try:
            response = await client.post(f"{PUSHOVER_API_BASE}/users/validate.json", data=data)
        except httpx.HTTPError as error:
            return PushoverValidation(valid=False, errors=(f"request failed: {error}",))
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if body.get("status") == 1:
            devices = tuple(str(item) for item in body.get("devices", []))
            return PushoverValidation(valid=True, devices=devices)
        errors = tuple(str(item) for item in body.get("errors", [])) or (
            f"HTTP {response.status_code}",
        )
        return PushoverValidation(valid=False, errors=errors)
    finally:
        if owns_client:
            await client.aclose()


# --- Document-event dispatch (wired into the pipeline in jobs.py / ingest.py) ---

# Per-event presentation: (push title, Pushover priority). Errors go out at
# high priority so they bypass the recipient's quiet hours; everything else
# at normal priority.
_EVENT_TITLES: dict[NotificationEvent, tuple[str, int]] = {
    NotificationEvent.DOCUMENT_SUCCESS: ("Document processed", _PRIORITY_NORMAL),
    NotificationEvent.PROCESSING_ERROR: ("Processing failed", _PRIORITY_HIGH),
    NotificationEvent.NEEDS_REVIEW: ("Document needs review", _PRIORITY_NORMAL),
    NotificationEvent.DUPLICATE: ("Duplicate document", _PRIORITY_NORMAL),
}

_EVENT_BODIES: dict[NotificationEvent, str] = {
    NotificationEvent.DOCUMENT_SUCCESS: "finished processing.",
    NotificationEvent.PROCESSING_ERROR: "failed during processing.",
    NotificationEvent.NEEDS_REVIEW: "was processed but may need a review.",
    NotificationEvent.DUPLICATE: "was already in your library (duplicate).",
}


@dataclass(frozen=True, slots=True)
class _OwnerTarget:
    """A resolved notification target: how to reach the document's owner.

    Carries the owner's full opted-in ``events`` set so a caller can pick which
    of several candidate kinds to send (e.g. needs-review vs success) without a
    second database round-trip.
    """

    app_token: str
    user_key: str
    device: str | None
    events: frozenset[NotificationEvent]
    label: str
    document_id: int


def _document_label(document: Document) -> str:
    """A short human label for the document in a push body."""
    title = (document.title or "").strip() if document.title is not None else ""
    if title:
        return f"“{title}”"
    if document.original_filename:
        return document.original_filename
    return f"Document {document.id}"


def _target_from_document(document: Document) -> _OwnerTarget | None:
    """Build a target from an already-loaded document (``uploader`` populated).

    ``None`` when there's nobody to notify: no owner, or the owner has no
    send-ready notification settings.
    """
    if document.uploader is None:
        return None
    creds = get_notification_credentials(document.uploader.preferences)
    if creds is None:
        return None
    return _OwnerTarget(
        app_token=creds.app_token,
        user_key=creds.user_key,
        device=creds.device,
        events=creds.events,
        label=_document_label(document),
        document_id=document.id,
    )


async def _resolve_target(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: int,
) -> _OwnerTarget | None:
    """Load the document + owner on a short-lived session; ``None`` if nobody."""
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return None
        return _target_from_document(document)


async def _send_for_target(
    target: _OwnerTarget,
    kind: NotificationEvent,
    document_id: int,
    *,
    document_url_base: str | None,
    client: httpx.AsyncClient | None,
) -> bool:
    """Send one push for ``kind`` to a resolved target; never raises."""
    push_title, priority = _EVENT_TITLES[kind]
    url = (
        f"{document_url_base.rstrip('/')}/documents/{target.document_id}"
        if document_url_base
        else None
    )
    result = await send_pushover(
        app_token=target.app_token,
        user_key=target.user_key,
        message=f"{target.label} {_EVENT_BODIES[kind]}",
        title=push_title,
        url=url,
        url_title="Open in Library" if url else None,
        device=target.device,
        priority=priority,
        client=client,
    )
    if not result.ok:
        logger.warning(
            "pushover %s for document %s failed: %s",
            kind.value,
            document_id,
            ", ".join(result.errors),
        )
        return False
    if result.app_remaining is not None and result.app_remaining < 100:
        logger.warning("pushover app quota low: %s messages remaining", result.app_remaining)
    return True


async def dispatch_document_notification(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: int,
    kind: NotificationEvent,
    *,
    document_url_base: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Best-effort: push ``kind`` to the document owner if they opted in.

    Loads the document and its owner on a short-lived session (decoupled from
    the caller's unit of work), checks the owner's notification settings, and
    sends at most one Pushover message. Returns ``True`` if a message was sent.
    Never raises — every failure is logged and swallowed.
    """
    try:
        target = await _resolve_target(session_factory, document_id)
        if target is None or kind not in target.events:
            return False
        return await _send_for_target(
            target, kind, document_id, document_url_base=document_url_base, client=client
        )
    except Exception:  # pragma: no cover - defensive: dispatch is best-effort
        logger.warning(
            "could not dispatch %s notification for document %s; continuing",
            kind.value,
            document_id,
            exc_info=True,
        )
        return False


async def dispatch_loaded_document_notification(
    document: Document,
    kind: NotificationEvent,
    *,
    document_url_base: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Best-effort push for an already-loaded document (``uploader`` populated).

    For callers that already hold the document and don't run inside the worker
    pipeline — notably the ingest-time ``duplicate`` event, which never enters
    the pipeline and so has no worker job to notify from. Never raises.
    """
    try:
        target = _target_from_document(document)
        if target is None or kind not in target.events:
            return False
        return await _send_for_target(
            target, kind, document.id, document_url_base=document_url_base, client=client
        )
    except Exception:  # pragma: no cover - defensive: dispatch is best-effort
        logger.warning(
            "could not dispatch %s notification for document %s; continuing",
            kind.value,
            document.id,
            exc_info=True,
        )
        return False


def _format_dropped_message(subject: str | None, filenames: list[str | None]) -> str:
    """Body for the 'attachments couldn't be added' push."""
    named = [name for name in filenames if name] or ["an attachment"]
    shown = ", ".join(named[:5])
    if len(named) > 5:
        shown += f", +{len(named) - 5} more"
    where = f" from “{subject}”" if subject else ""
    count = len(filenames)
    noun = "attachment" if count == 1 else "attachments"
    return f"{count} {noun}{where} couldn't be added to your library: {shown}."


async def dispatch_attachments_dropped_notification(
    session_factory: async_sessionmaker[AsyncSession],
    owner_id: int | None,
    *,
    subject: str | None,
    filenames: list[str | None],
    document_url_base: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Best-effort: tell an owner that some email attachments could not be added.

    Unlike the document-centric dispatchers this has *no* document (the content
    never became a row), so it resolves the owner's credentials directly and
    reuses the ``processing_error`` opt-in (a dropped attachment is a processing
    problem) at high priority. Returns ``True`` if a message was sent; never
    raises — mirrors the other dispatchers.
    """
    if owner_id is None or not filenames:
        return False
    try:
        async with session_factory() as session:
            owner = await session.get(User, owner_id)
            if owner is None:
                return False
            creds = get_notification_credentials(owner.preferences)
        if creds is None or NotificationEvent.PROCESSING_ERROR not in creds.events:
            return False
        _, priority = _EVENT_TITLES[NotificationEvent.PROCESSING_ERROR]
        url = f"{document_url_base.rstrip('/')}/documents" if document_url_base else None
        result = await send_pushover(
            app_token=creds.app_token,
            user_key=creds.user_key,
            message=_format_dropped_message(subject, filenames),
            title="Attachments not added",
            url=url,
            url_title="Open Library" if url else None,
            device=creds.device,
            priority=priority,
            client=client,
        )
        if not result.ok:
            logger.warning(
                "pushover attachments-dropped for owner %s failed: %s",
                owner_id,
                ", ".join(result.errors),
            )
            return False
        return True
    except Exception:  # pragma: no cover - defensive: dispatch is best-effort
        logger.warning(
            "could not dispatch attachments-dropped notification for owner %s; continuing",
            owner_id,
            exc_info=True,
        )
        return False


async def dispatch_document_completion(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: int,
    *,
    needs_review: bool,
    document_url_base: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Best-effort: send the single completion push for a finished document.

    Decision (one push, never two): if the document needs review *and* the
    owner opted into ``needs_review``, send that; otherwise fall back to the
    ``document_success`` push if they opted into it. Returns ``True`` if a
    message was sent. Never raises.
    """
    try:
        target = await _resolve_target(session_factory, document_id)
        if target is None:
            return False
        if needs_review and NotificationEvent.NEEDS_REVIEW in target.events:
            kind = NotificationEvent.NEEDS_REVIEW
        elif NotificationEvent.DOCUMENT_SUCCESS in target.events:
            kind = NotificationEvent.DOCUMENT_SUCCESS
        else:
            return False
        return await _send_for_target(
            target, kind, document_id, document_url_base=document_url_base, client=client
        )
    except Exception:  # pragma: no cover - defensive: dispatch is best-effort
        logger.warning(
            "could not dispatch completion notification for document %s; continuing",
            document_id,
            exc_info=True,
        )
        return False
