"""Pydantic request/response schemas for the HTTP API.

See docs/api.md for the full surface description. ``Decimal`` fields
(``amount_total``) serialize to JSON strings to preserve precision.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field, StringConstraints, field_validator

from library.models import DocumentLanguage, DocumentSource, DocumentStatus


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
    document_date: date | None
    language: DocumentLanguage
    status: DocumentStatus
    mime_type: str
    page_count: int | None
    created_at: datetime
    has_searchable_pdf: bool
    has_thumbnail: bool
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
    language: DocumentLanguage | None = None
    amount_total: Decimal | None = None
    currency: CurrencyCode | None = None
    due_date: date | None = None
    expiry_date: date | None = None


class ExtractionQueuedResponse(BaseModel):
    """202 body of POST /api/documents/{id}/extract."""

    queued: bool
    job_id: int = Field(description="The Procrastinate job id (see GET /api/jobs).")


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


class UserPreferences(BaseModel):
    """All resolved per-user display preferences (read model).

    Returned by GET /api/settings and embedded in ``UserOut``. Writes are
    split per concern (dashboard fields vs appearance) so each Settings tab
    saves independently; this model is the union the client reads back.
    """

    dashboard_fields: list[DashboardField]
    background_tone: BackgroundTone
    tile_preview: TilePreview


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
    )


class UserOut(BaseModel):
    """The authenticated user (login response and GET /api/auth/me)."""

    id: int
    username: str
    display_name: str
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
    """One row from the procrastinate_jobs table, as exposed by GET /api/jobs."""

    id: int
    status: str
    task_name: str
    attempts: int
    scheduled_at: datetime | None
    document_id: int | None
