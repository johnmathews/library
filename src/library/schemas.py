"""Pydantic request/response schemas for the HTTP API.

See docs/api.md for the full surface description. ``Decimal`` fields
(``amount_total``) serialize to JSON strings to preserve precision.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field, StringConstraints, field_validator

from library.models import DocumentLanguage, DocumentSource, DocumentStatus, ReviewStatus


class DocumentUploadResponse(BaseModel):
    """Body returned by POST /api/documents (201 created, 200 duplicate)."""

    id: int
    sha256: str
    status: DocumentStatus
    duplicate: bool


class KindOut(BaseModel):
    """A document kind, expanded inline."""

    slug: str
    name: str


class SenderOut(BaseModel):
    """A sender, expanded inline."""

    id: int
    name: str


class TagOut(BaseModel):
    """A tag, expanded inline."""

    slug: str
    name: str


class ProjectRef(BaseModel):
    """A project/collection, expanded inline on a document."""

    slug: str
    name: str


class IngestionEventOut(BaseModel):
    """One entry of a document's append-only audit trail."""

    event: str
    detail: dict[str, Any]
    created_at: datetime


class DocumentListItem(BaseModel):
    """One row of GET /api/documents."""

    id: int
    title: str | None
    summary: str | None
    kind: KindOut | None
    sender: SenderOut | None
    tags: list[TagOut] = Field(description="Sorted by slug.")
    topics: list[str] = Field(default_factory=list, description="Extracted free-text topics.")
    projects: list[ProjectRef] = Field(default_factory=list, description="Sorted by slug.")
    document_date: date | None
    language: DocumentLanguage
    status: DocumentStatus
    mime_type: str
    page_count: int | None
    created_at: datetime
    has_searchable_pdf: bool
    has_thumbnail: bool
    review_status: ReviewStatus
    amount_total: Decimal | None = None
    currency: str | None = None
    snippet: str | None = Field(
        default=None,
        description=(
            "Only with ?q=. ts_headline fragments over the OCR text with <b>/</b> "
            "markers. NOT HTML-escaped: render as text, handle <b> deliberately."
        ),
    )
    rank: float | None = Field(
        default=None,
        description="Only with ?q=. greatest(ts_rank dutch, ts_rank english).",
    )


class DocumentListResponse(BaseModel):
    """Paginated body of GET /api/documents."""

    items: list[DocumentListItem]
    total: int = Field(description="Filtered count before limit/offset.")
    limit: int
    offset: int


class DocumentDetail(DocumentListItem):
    """Body of GET/PATCH /api/documents/{id}: list item plus full content."""

    ocr_text: str | None
    ocr_confidence: float | None
    due_date: date | None
    expiry_date: date | None
    source: DocumentSource
    original_filename: str | None
    sha256: str
    extraction: dict[str, Any] | None = Field(
        description="Provenance of the last Claude extraction run, if any."
    )
    user_edited_fields: list[str] = Field(
        description="Fields locked by user edits; re-extraction never overwrites them."
    )
    validation: dict[str, Any] | None = Field(
        default=None, description="Latest validation run: findings + provenance."
    )
    events: list[IngestionEventOut] = Field(description="Audit trail, oldest first.")


CurrencyCode = Annotated[
    str, StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$", to_upper=True)
]


class DocumentUpdate(BaseModel):
    """PATCH /api/documents/{id} body; only fields present in the body change.

    ``null`` clears nullable fields; ``tags`` is a full-replacement slug
    list (``[]`` clears, ``null`` is rejected); ``language`` cannot be null.
    """

    title: str | None = None
    summary: str | None = None
    document_date: date | None = None
    kind_slug: str | None = Field(default=None, description="Must be an existing kind slug.")
    sender: str | None = Field(
        default=None, description="Sender name; upserted case-insensitively."
    )
    tags: list[str] | None = Field(
        default=None, description="Full replacement list of tag slugs; created if unknown."
    )
    projects: list[str] | None = Field(
        default=None,
        description=(
            "Full replacement list of project slugs or names; created if unknown. "
            "`[]` clears membership, `null` is rejected."
        ),
    )
    language: DocumentLanguage | None = None
    amount_total: Decimal | None = None
    currency: CurrencyCode | None = None
    due_date: date | None = None
    expiry_date: date | None = None


class ExtractionQueuedResponse(BaseModel):
    """202 body of POST /api/documents/{id}/extract."""

    queued: bool
    job_id: int = Field(description="The Procrastinate job id (see GET /api/jobs).")


class NoteCreate(BaseModel):
    """Body of POST /api/notes — author a new in-app note."""

    title: str = Field(min_length=1, description="The note title (locked against re-extraction).")
    body_markdown: str = Field(description="The note body, authored as Markdown.")


class NoteUpdate(BaseModel):
    """Body of PATCH /api/notes/{id}; only fields present in the body change."""

    title: str | None = Field(default=None, min_length=1)
    body_markdown: str | None = None


class NoteVersionOut(BaseModel):
    """One snapshot in a note's version history (newest-first in listings)."""

    version_no: int
    title: str | None
    body: str
    created_at: datetime


class LoginRequest(BaseModel):
    """Body of POST /api/auth/login."""

    username: str
    password: str


class DashboardField(StrEnum):
    """A metadata field that can be shown on a dashboard tile."""

    KIND = "kind"
    SENDER = "sender"
    TAGS = "tags"
    DATE = "date"
    LANGUAGE = "language"
    STATUS = "status"
    AMOUNT = "amount"
    FILE_TYPE = "file_type"


DEFAULT_DASHBOARD_FIELDS: Final[list[DashboardField]] = [
    DashboardField.KIND,
    DashboardField.SENDER,
    DashboardField.TAGS,
    DashboardField.DATE,
    DashboardField.LANGUAGE,
    DashboardField.STATUS,
]


class DashboardPreferences(BaseModel):
    """Which metadata fields appear on the dashboard tiles (membership only).

    The list is a set of fields to show, not an ordering: the tile render
    order is fixed in the frontend (DocumentListView), independent of the
    order stored here.
    """

    dashboard_fields: list[DashboardField]

    @field_validator("dashboard_fields", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[DashboardField]:
        """Keep only known field keys, de-duplicated, order preserved.

        Tolerant on purpose: unknown/garbage values are dropped (never a
        422 or 500), so a hand-edited row or a renamed field can't break
        the dashboard.
        """
        if not isinstance(value, list):
            return []
        valid = {field.value for field in DashboardField}
        seen: set[str] = set()
        cleaned: list[DashboardField] = []
        for item in value:
            if isinstance(item, str) and item in valid and item not in seen:
                seen.add(item)
                cleaned.append(DashboardField(item))
        return cleaned


def resolve_dashboard_preferences(
    preferences: dict[str, Any] | None,
) -> DashboardPreferences:
    """Resolve a user's stored ``preferences`` blob to display fields.

    Absent ``dashboard_fields`` key -> the default set. An explicit (even
    empty) list is honoured and cleaned.
    """
    blob = preferences or {}
    if "dashboard_fields" not in blob:
        return DashboardPreferences(dashboard_fields=DEFAULT_DASHBOARD_FIELDS)
    return DashboardPreferences(dashboard_fields=blob["dashboard_fields"])


class BackgroundTone(StrEnum):
    """The page-canvas colour behind the dashboard tiles.

    A named token, not a hex value: the frontend owns the actual colour for
    each tone (assets/main.css), so the palette can be retuned without a
    schema or data migration. Applies to light mode only — dark mode keeps
    its near-black canvas regardless.
    """

    NEUTRAL = "neutral"  # gray-200 — the default; clear separation from white tiles
    LIGHT = "light"  # gray-100 — the original, airier canvas
    SOFT = "soft"  # a gentle step down from light
    SLATE = "slate"  # cool, with a subtle violet undertone
    SAND = "sand"  # warm neutral
    MIST = "mist"  # cool blue-grey


DEFAULT_BACKGROUND_TONE: Final[BackgroundTone] = BackgroundTone.NEUTRAL


def _resolve_background_tone(blob: dict[str, Any]) -> BackgroundTone:
    """Pick the stored tone, falling back to the default for absent/garbage.

    Tolerant like ``DashboardPreferences._clean``: an unknown or hand-edited
    value resolves to the default rather than raising.
    """
    raw = blob.get("background_tone")
    if isinstance(raw, str) and raw in {tone.value for tone in BackgroundTone}:
        return BackgroundTone(raw)
    return DEFAULT_BACKGROUND_TONE


class TilePreview(StrEnum):
    """How a dashboard tile renders the document's first-page thumbnail.

    A4 pages are tall and narrow; the tile box is landscape. ``FULL_WIDTH``
    fills the tile width and crops the lower part of the page (the default);
    ``WHOLE_PAGE`` shows the entire first page letterboxed inside the box.
    The frontend owns the actual CSS object-fit for each value.
    """

    FULL_WIDTH = "full_width"  # fill width, crop bottom — the default
    WHOLE_PAGE = "whole_page"  # show the whole page, letterboxed


DEFAULT_TILE_PREVIEW: Final[TilePreview] = TilePreview.FULL_WIDTH


def _resolve_tile_preview(blob: dict[str, Any]) -> TilePreview:
    """Pick the stored preview mode, falling back for absent/garbage values."""
    raw = blob.get("tile_preview")
    if isinstance(raw, str) and raw in {mode.value for mode in TilePreview}:
        return TilePreview(raw)
    return DEFAULT_TILE_PREVIEW


class AppearancePreferences(BaseModel):
    """Body of PUT /api/settings/appearance — page-canvas tone + tile preview."""

    background_tone: BackgroundTone
    tile_preview: TilePreview = DEFAULT_TILE_PREVIEW

    @field_validator("background_tone", mode="before")
    @classmethod
    def _default_unknown(cls, value: object) -> BackgroundTone:
        """Coerce an unknown/garbage tone to the default (never a 422)."""
        if isinstance(value, str) and value in {tone.value for tone in BackgroundTone}:
            return BackgroundTone(value)
        return DEFAULT_BACKGROUND_TONE

    @field_validator("tile_preview", mode="before")
    @classmethod
    def _default_unknown_tile_preview(cls, value: object) -> TilePreview:
        """Coerce an unknown/garbage preview mode to the default (never a 422)."""
        if isinstance(value, str) and value in {mode.value for mode in TilePreview}:
            return TilePreview(value)
        return DEFAULT_TILE_PREVIEW


class NotificationEvent(StrEnum):
    """A document event a user can choose to be pushed about (via Pushover)."""

    DOCUMENT_SUCCESS = "document_success"  # finished the full pipeline
    PROCESSING_ERROR = "processing_error"  # failed somewhere in the pipeline
    NEEDS_REVIEW = "needs_review"  # processed but low-confidence / flagged
    DUPLICATE = "duplicate"  # ingested content already in the library


#: New users get nothing until they opt in — notifications are off by default.
DEFAULT_NOTIFICATION_EVENTS: Final[list[NotificationEvent]] = []


def _clean_events(value: object) -> list[NotificationEvent]:
    """Keep only known event keys, de-duplicated, order preserved.

    Tolerant like :meth:`DashboardPreferences._clean`: unknown/garbage values
    are dropped rather than raising, so a hand-edited row or a renamed event
    can never 422 a settings save.
    """
    if not isinstance(value, list):
        return []
    valid = {event.value for event in NotificationEvent}
    seen: set[str] = set()
    cleaned: list[NotificationEvent] = []
    for item in value:
        if isinstance(item, str) and item in valid and item not in seen:
            seen.add(item)
            cleaned.append(NotificationEvent(item))
    return cleaned


def _clean_addresses(value: object) -> list[str]:
    """Normalise a list of email addresses: lowercased, stripped, de-duplicated.

    The from-addresses a user forwards mail from (email-in attribution). Tolerant
    like the other preference cleaners — garbage is dropped, never a 422.
    """
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in value:
        if isinstance(item, str):
            address = item.strip().lower()
            if address and address not in seen:
                seen.add(address)
                cleaned.append(address)
    return cleaned


class NotificationSettingsIn(BaseModel):
    """Body of PUT /api/settings/notifications (write model).

    The Pushover credentials are secrets: an absent or empty ``pushover_*``
    value means "leave the stored one unchanged" (so saving only ``events``
    never wipes the token). ``pushover_device`` and ``events`` are echoed back
    in the read model, so they are authoritative from the payload.
    """

    enabled: bool = False
    pushover_app_token: str | None = None
    pushover_user_key: str | None = None
    pushover_device: str | None = None
    events: list[NotificationEvent] = Field(default_factory=list)
    # From-addresses this user forwards mail from (email-in attribution): an
    # email whose sender matches is owned by — and notified to — this user.
    email_forward_addresses: list[str] = Field(default_factory=list)

    @field_validator("events", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[NotificationEvent]:
        return _clean_events(value)

    @field_validator("email_forward_addresses", mode="before")
    @classmethod
    def _clean_addrs(cls, value: object) -> list[str]:
        return _clean_addresses(value)

    @field_validator("pushover_app_token", "pushover_user_key", "pushover_device", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Treat an empty/whitespace string as absent (keep-existing)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class NotificationSettingsOut(BaseModel):
    """Resolved notification settings (read model) — never exposes secrets.

    The raw Pushover token/key are write-only; the client only learns whether
    each is configured (``*_set``), so a leaked ``/auth/me`` or ``/settings``
    response cannot reveal a user's credentials.
    """

    enabled: bool
    pushover_app_token_set: bool
    pushover_user_key_set: bool
    pushover_device: str | None
    events: list[NotificationEvent]
    email_forward_addresses: list[str]


@dataclass(frozen=True, slots=True)
class NotificationCredentials:
    """Internal: the secrets + event set needed to actually send a push.

    Returned by :func:`get_notification_credentials` only when a user is fully
    configured (enabled + both credentials present). Never serialized.
    """

    app_token: str
    user_key: str
    device: str | None
    events: frozenset[NotificationEvent]


def _notifications_blob(preferences: dict[str, Any] | None) -> dict[str, Any]:
    """The ``notifications`` sub-dict of a preferences blob (empty if absent)."""
    blob = (preferences or {}).get("notifications")
    return blob if isinstance(blob, dict) else {}


def resolve_notification_settings(
    preferences: dict[str, Any] | None,
) -> NotificationSettingsOut:
    """Resolve a stored ``preferences`` blob to the secret-safe read model."""
    blob = _notifications_blob(preferences)
    app_token = blob.get("pushover_app_token")
    user_key = blob.get("pushover_user_key")
    device = blob.get("pushover_device")
    return NotificationSettingsOut(
        enabled=bool(blob.get("enabled", False)),
        pushover_app_token_set=bool(app_token),
        pushover_user_key_set=bool(user_key),
        pushover_device=device if isinstance(device, str) and device else None,
        events=_clean_events(blob.get("events", DEFAULT_NOTIFICATION_EVENTS)),
        email_forward_addresses=_clean_addresses(blob.get("email_forward_addresses", [])),
    )


def get_notification_credentials(
    preferences: dict[str, Any] | None,
) -> NotificationCredentials | None:
    """Return send-ready credentials, or ``None`` if the user can't be pushed.

    ``None`` whenever notifications are disabled, either credential is missing,
    or no events are selected — the caller (the dispatcher) then sends nothing.
    """
    blob = _notifications_blob(preferences)
    if not blob.get("enabled"):
        return None
    app_token = blob.get("pushover_app_token")
    user_key = blob.get("pushover_user_key")
    if not (isinstance(app_token, str) and isinstance(user_key, str)):
        return None
    events = frozenset(_clean_events(blob.get("events", [])))
    if not events:
        return None
    device = blob.get("pushover_device")
    return NotificationCredentials(
        app_token=app_token,
        user_key=user_key,
        device=device if isinstance(device, str) and device else None,
        events=events,
    )


class UserPreferences(BaseModel):
    """All resolved per-user display preferences (read model).

    Returned by GET /api/settings and embedded in ``UserOut``. Writes are
    split per concern (dashboard fields vs appearance vs notifications) so each
    Settings tab saves independently; this model is the union the client reads
    back.
    """

    dashboard_fields: list[DashboardField]
    background_tone: BackgroundTone
    tile_preview: TilePreview
    notifications: NotificationSettingsOut


def resolve_preferences(preferences: dict[str, Any] | None) -> UserPreferences:
    """Resolve the stored ``preferences`` blob to the full read model.

    Reuses :func:`resolve_dashboard_preferences` for field cleaning/defaults
    and :func:`_resolve_background_tone` for the canvas tone.
    """
    blob = preferences or {}
    return UserPreferences(
        dashboard_fields=resolve_dashboard_preferences(blob).dashboard_fields,
        background_tone=_resolve_background_tone(blob),
        tile_preview=_resolve_tile_preview(blob),
        notifications=resolve_notification_settings(blob),
    )


class UserOut(BaseModel):
    """The authenticated user (login response and GET /api/auth/me)."""

    id: int
    username: str
    display_name: str
    is_admin: bool
    preferences: UserPreferences


class TokenCreateRequest(BaseModel):
    """Body of POST /api/auth/tokens."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]


class TokenCreatedResponse(BaseModel):
    """201 body of POST /api/auth/tokens — the only time the secret appears."""

    id: int
    name: str
    token: str = Field(description="The bearer secret. Shown once; store it now.")
    created_at: datetime


class TokenInfo(BaseModel):
    """One row of GET /api/auth/tokens; never contains secrets or hashes."""

    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class JobInfo(BaseModel):
    """One row from GET /api/jobs: a Procrastinate job enriched with the
    pipeline state of the document it processes (when it has one).

    The document fields are null for jobs without a ``document_id`` (e.g. the
    periodic email poll) or whose document has since been deleted.
    """

    id: int
    status: str
    task_name: str
    attempts: int
    scheduled_at: datetime | None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    document_id: int | None
    active: bool
    document_title: str | None = None
    document_status: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    tokens: int | None = None


class MarkdownPage(BaseModel):
    """One page of a document's per-page markdown rendering."""

    page_number: int
    markdown: str


class MarkdownResponse(BaseModel):
    """Body of GET /api/documents/{id}/markdown."""

    page_count: int
    pages: list[MarkdownPage]
