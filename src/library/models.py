"""SQLAlchemy 2.0 declarative models for the Library backend.

Design notes
------------
- Lifecycle/source/language fields use ``sa.Enum(..., native_enum=False)``:
  plain ``VARCHAR`` columns with a CHECK constraint instead of Postgres enum
  types. Adding a value is then an ordinary migration (drop/recreate the
  check) rather than ``ALTER TYPE``, and values stay readable in psql.
- Full-text search uses two STORED generated tsvector columns (Dutch and
  English configs) over title + summary + coalesce(pages_markdown, ocr_text)
  + topics, each with a GIN index. Stemming differs per language, so one
  column cannot serve both. The body term prefers the vision "understood
  layer" (``pages_markdown``, the concatenated per-page markdown) and falls
  back to raw ``ocr_text`` — mirroring the embed/Ask retrieval paths — so
  image PDFs are findable by body text OCR never captured.
- ``documents.deleted_at`` implements soft delete; ``ingestion_events`` is an
  append-only audit trail.
"""

import enum
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Dimensionality of the bge-m3 embeddings stored in ``document_chunks``.
EMBEDDING_DIM: int = 1024

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Expression shared by both generated FTS columns, parameterised on the
# Postgres text-search config ('dutch' / 'english').
FTS_EXPRESSION: str = (
    "to_tsvector('{config}', coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(pages_markdown, ocr_text, '') || ' ' "
    "|| coalesce(topics::text, ''))"
)


class DocumentStatus(enum.StrEnum):
    """Processing lifecycle of a document."""

    RECEIVED = "received"
    OCR = "ocr"
    EXTRACT = "extract"
    MARKDOWN = "markdown"
    EMBED = "embed"
    INDEXED = "indexed"
    FAILED = "failed"


class DocumentSource(enum.StrEnum):
    """Channel through which a document entered the system."""

    UPLOAD = "upload"
    CONSUME = "consume"
    EMAIL = "email"
    API = "api"
    MCP = "mcp"
    IMPORT = "import"
    NOTE = "note"


class DocumentLanguage(enum.StrEnum):
    """Detected language of a document's text."""

    NLD = "nld"
    ENG = "eng"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ReviewStatus(enum.StrEnum):
    """Trust state of a document's extracted metadata."""

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    UNREVIEWED = "unreviewed"


class AmountKind(enum.StrEnum):
    """What a document's ``amount_total`` actually is.

    ``amount_total`` is always a magnitude. The sign of a document's
    contribution to a spending total is a property of what the number
    *means*, so it is carried here and nowhere else — see ``AMOUNT_SIGN``.
    The non-contributing values exist so that a coverage ceiling, an opening
    balance, a quote or a nil-return confirmation can be recorded faithfully
    without contaminating a total.
    """

    PAYMENT_DUE = "payment_due"
    PAYMENT_MADE = "payment_made"
    ASSESSMENT = "assessment"
    REFUND = "refund"
    COVERAGE_LIMIT = "coverage_limit"
    BALANCE = "balance"
    ESTIMATE = "estimate"
    NONE = "none"


#: How each contributing kind enters a spending total. A kind absent from this
#: map never enters one, so "summable" and "signed" are the same predicate and
#: cannot drift apart. A refund is the only negative: money returned, or an
#: amount owed cancelled.
AMOUNT_SIGN: Mapping[AmountKind, int] = MappingProxyType(
    {
        AmountKind.PAYMENT_DUE: 1,
        AmountKind.PAYMENT_MADE: 1,
        AmountKind.ASSESSMENT: 1,
        AmountKind.REFUND: -1,
    }
)

SUMMABLE_AMOUNT_KINDS: frozenset[AmountKind] = frozenset(AMOUNT_SIGN)


class SpendLineOrigin(enum.StrEnum):
    """Where a line came from. Only ``MANUAL`` is produced today; extraction
    proposing lines is deferred (spec §14, open question 3)."""

    EXTRACTED = "extracted"
    MANUAL = "manual"


class HeldEmailStatus(enum.StrEnum):
    """Lifecycle of a held email (see ``HeldEmail``).

    An email the poller declined to auto-file lands as ``held``; the owner
    either ingests it (``ingested``) or dismisses it (``dismissed``). Resolved
    rows are kept as an audit trail rather than deleted.
    """

    HELD = "held"
    INGESTED = "ingested"
    DISMISSED = "dismissed"


class Grain(enum.StrEnum):
    """The time bucket a chart's x-axis uses (spec §9.2)."""

    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names for Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    api_tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Session(Base):
    """A browser session; the client holds the raw token, we store its hash."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="api_tokens")


class SavedView(Base):
    """A named, per-user snapshot of the document-list filter/search state.

    ``filter_state`` stores the frontend's canonical URL query (the
    ``buildDocumentQuery`` output) verbatim, so applying a view is just pushing
    that query at the homepage. ``pinned`` surfaces the view as a custom
    dashboard in the sidebar; ``sort_order`` orders a user's views (and their
    pinned subset). Scoped to one user — cascade-deleted with the account.
    """

    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    filter_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Composite index serves both the FK lookup (user_id prefix) and the
    # per-user ordered listing.
    __table_args__ = (Index("ix_saved_views_user_id_sort_order", "user_id", "sort_order"),)


class Kind(Base):
    """Document kind (invoice, receipt, ...); rows are seeded by migration."""

    __tablename__ = "kinds"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))


class Sender(Base):
    __tablename__ = "senders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: A stored colour for this sender as a chart split value (spec §10.3).
    #: NULL means "derive a palette slot from the id" — the normal state.
    colour: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="colour_hex"),)


class Recipient(Base):
    """Document recipient (who a document was addressed to); lookup table mirroring ``Sender``.

    Optionally linked to a :class:`User` via ``user_id`` (nullable FK, migration
    0020): creating a user auto-links a recipient named by their display name,
    and ingestion resolves a document to that recipient when the extracted name
    matches the user's username *or* display name. ``ON DELETE SET NULL`` keeps
    the recipient (and the documents addressed to it) when its user is deleted.
    """

    __tablename__ = "recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User | None"] = relationship(lazy="selectin")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


document_tags: Table = Table(
    "document_tags",
    Base.metadata,
    Column(
        "document_id",
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Facet(Base):
    """A named label dimension. A document carries at most one value per facet."""

    __tablename__ = "facets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(255))
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class FacetValue(Base):
    """One allowed value of a facet.

    ``parent_id`` is unused at ship; it exists so a flat facet can gain a second
    level as a data change rather than a migration. The redundant
    ``UNIQUE (id, facet_id)`` is what lets label tables hold a composite foreign
    key and so cannot point at another facet's value.
    """

    __tablename__ = "facet_values"
    __table_args__ = (
        UniqueConstraint("facet_id", "key", name="facet_values_facet_key"),
        UniqueConstraint("id", "facet_id", name="facet_values_id_facet"),
        CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="colour_hex"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facet_id: Mapped[int] = mapped_column(ForeignKey("facets.id", ondelete="RESTRICT"))
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("facet_values.id", ondelete="RESTRICT"), nullable=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: A stored colour for this value as a chart split value (spec §10.3).
    #: NULL means "derive a palette slot from the key" — the normal state.
    colour: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FacetValueAlias(Base):
    """A surface form that resolves to a value: a plate, a marque, a misspelling."""

    __tablename__ = "facet_value_aliases"

    facet_value_id: Mapped[int] = mapped_column(
        ForeignKey("facet_values.id", ondelete="CASCADE"), primary_key=True
    )
    alias: Mapped[str] = mapped_column(String(255), primary_key=True)


class DocumentLabel(Base):
    """One document's value for one facet. The PK enforces at-most-one."""

    __tablename__ = "document_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="document_labels_value_facet",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    facet_id: Mapped[int] = mapped_column(
        ForeignKey("facets.id", ondelete="RESTRICT"), primary_key=True
    )
    facet_value_id: Mapped[int] = mapped_column(Integer)


class SpendLine(Base):
    """One part of a document's amount, when the money divides.

    A document has either no lines at all — the common case, and the one
    ``spend_facts`` synthesises a row for — or a complete set summing to
    ``amount_total``. There is no partial state; the sum is enforced by a pair
    of deferred constraint triggers (migration 0035) rather than by application
    code, and from both sides: editing ``documents.amount_total`` out from under
    an allocation is refused just as an unbalanced line set is.
    """

    __tablename__ = "spend_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[SpendLineOrigin] = mapped_column(
        Enum(
            SpendLineOrigin,
            name="spend_line_origin",
            native_enum=False,
            length=16,
            # Without this SQLAlchemy persists the member *name* ("MANUAL"),
            # which the migration's `origin IN ('extracted','manual')` CHECK
            # rejects. Same treatment as every other enum column here.
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=SpendLineOrigin.MANUAL,
        server_default=SpendLineOrigin.MANUAL.value,
    )


class LineLabel(Base):
    """One line's value for one facet. Overrides the document's, if any."""

    __tablename__ = "line_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="line_labels_value_facet",
        ),
    )

    line_id: Mapped[int] = mapped_column(
        ForeignKey("spend_lines.id", ondelete="CASCADE"), primary_key=True
    )
    facet_id: Mapped[int] = mapped_column(
        ForeignKey("facets.id", ondelete="RESTRICT"), primary_key=True
    )
    facet_value_id: Mapped[int] = mapped_column(Integer)


class FacetValueSuggestion(Base):
    """A value the labeller wanted but the closed vocabulary does not contain.

    Queued for approval rather than created. This is the mechanism that keeps
    the vocabulary closed while still letting it grow deliberately.
    """

    __tablename__ = "facet_value_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "facet_id", "document_id", "suggested_label", name="facet_value_suggestions_unique"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facet_id: Mapped[int] = mapped_column(ForeignKey("facets.id", ondelete="CASCADE"))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    suggested_label: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    """A first-class project/collection grouping documents (M2M, soft-archive)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


document_projects: Table = Table(
    "document_projects",
    Base.metadata,
    Column(
        "document_id",
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "project_id",
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

Index("ix_document_projects_project_id", document_projects.c.project_id)


class Matter(Base):
    """A first-class "business matter" grouping documents by subject (M2M).

    Unlike :class:`Project` (a time-bound endeavor that is archived when it
    concludes), a matter is an evergreen life/business category — "car
    insurance", "health insurance", "subscriptions" — that a document can
    belong to any number of. Matters are user-curated (name + a short ``hint``
    that guides the LLM classifier) and auto-assigned at ingest, but stay
    hand-correctable. ``archived_at`` soft-disables a matter without deleting
    its history.
    """

    __tablename__ = "matters"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # Short human-written description of what belongs in this matter; fed to the
    # LLM classifier as the disambiguation hint. NULL = no hint provided.
    hint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


document_matters: Table = Table(
    "document_matters",
    Base.metadata,
    Column(
        "document_id",
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "matter_id",
        ForeignKey("matters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

Index("ix_document_matters_matter_id", document_matters.c.matter_id)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str] = mapped_column(String(255))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name="document_status",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=DocumentStatus.RECEIVED,
        server_default=DocumentStatus.RECEIVED.value,
        index=True,
    )
    source: Mapped[DocumentSource] = mapped_column(
        Enum(
            DocumentSource,
            name="document_source",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
    )

    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, index=True)
    language: Mapped[DocumentLanguage] = mapped_column(
        Enum(
            DocumentLanguage,
            name="document_language",
            native_enum=False,
            length=8,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=DocumentLanguage.UNKNOWN,
        server_default=DocumentLanguage.UNKNOWN.value,
    )
    amount_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # What amount_total means. NULL = not yet decided; consumers treat NULL as
    # not summable, so an un-backfilled archive under-reports rather than over-.
    amount_kind: Mapped[AmountKind | None] = mapped_column(
        Enum(
            AmountKind,
            name="amount_kind",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        nullable=True,
    )
    # The document's own invoice / order / booking number. The only evidence
    # that pairs an invoice with its receipt across an arbitrary date gap.
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    due_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            name="review_status",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=ReviewStatus.UNREVIEWED,
        server_default=ReviewStatus.UNREVIEWED.value,
        index=True,
    )

    ocr_text: Mapped[str | None] = mapped_column(Text)
    # Concatenated ``DocumentPage.markdown`` (the vision "understood layer"),
    # denormalized onto the document so the same-row generated FTS columns can
    # index it — a generated column cannot read the child ``document_pages``
    # table. Kept in sync by the app wherever page rows are (re)written
    # (markdown/apply.py, api/notes.py); NULL when the document has no pages, in
    # which case FTS falls back to ``ocr_text``. Not the source of truth for
    # page content — ``document_pages`` is; this is a search-only mirror.
    pages_markdown: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    searchable_pdf: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    original_filename: Mapped[str | None] = mapped_column(String(1024))
    page_count: Mapped[int | None] = mapped_column(Integer)
    paperless_id: Mapped[int | None] = mapped_column(Integer, unique=True)

    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("senders.id", ondelete="SET NULL"), index=True
    )
    recipient_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipients.id", ondelete="SET NULL"), index=True
    )
    kind_id: Mapped[int | None] = mapped_column(
        ForeignKey("kinds.id", ondelete="SET NULL"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    search_vector_nl: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(FTS_EXPRESSION.format(config="dutch"), persisted=True),
    )
    search_vector_en: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(FTS_EXPRESSION.format(config="english"), persisted=True),
    )

    uploader: Mapped[User | None] = relationship(lazy="selectin")
    sender: Mapped[Sender | None] = relationship(lazy="selectin")
    recipient: Mapped[Recipient | None] = relationship(lazy="selectin")
    kind: Mapped[Kind | None] = relationship(lazy="selectin")
    tags: Mapped[list[Tag]] = relationship(secondary=document_tags, lazy="selectin")
    projects: Mapped[list[Project]] = relationship(secondary=document_projects, lazy="selectin")
    matters: Mapped[list[Matter]] = relationship(secondary=document_matters, lazy="selectin")
    events: Mapped[list["IngestionEvent"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )
    # Chunks carry large embedding vectors and are never wanted on a normal
    # document load: rely on the DB-level ON DELETE CASCADE (passive_deletes)
    # and query them explicitly. ``lazy="raise"`` turns any accidental implicit
    # load into a loud error rather than a silent N+1 over embeddings.
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    comments: Mapped[list["DocumentComment"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentComment.created_at",
        lazy="raise",
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="DocumentPage.page_number",
    )

    __table_args__ = (
        Index("ix_documents_search_vector_nl", "search_vector_nl", postgresql_using="gin"),
        Index("ix_documents_search_vector_en", "search_vector_en", postgresql_using="gin"),
    )


class DocumentChunk(Base):
    """A page-sized slice of a document's text plus its embedding vector.

    One row per chunk (see ``embedding.chunker``); ``chunk_index`` is the
    1-based ordinal of the chunk within the document. ``page_number`` (when
    non-NULL) carries the true page provenance from the markdown layer; NULL
    when the chunk came from the ocr_text fallback. The embedding is a bge-m3
    1024-dim vector used for semantic retrieval; an HNSW index over
    ``embedding`` (cosine ops) backs nearest-neighbour search.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    #: The document-identity line prepended to ``text`` before embedding, so a
    #: chunk retrieves on its sender/date/kind/title as well as its content.
    #: Stored separately rather than baked into ``text`` because ``text`` is
    #: also what Ask reads back as an excerpt: with three passages per document
    #: and ten documents per search, a baked-in header would repeat the same
    #: metadata up to thirty times per tool result, duplicating fields the
    #: result rows already carry. NULL for chunks written before this column
    #: existed, and for documents with no metadata at all.
    context_header: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("document_comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 200},
        ),
    )


class DocumentPage(Base):
    """Per-page markdown rendering of a document — the canonical "understood" layer.

    Generated by Claude vision grounded on the OCR text. One row per page;
    the full-document markdown is these rows ordered by ``page_number``. This
    is the source for page-aware chunking (``DocumentChunk.page_number``) and
    the detail-view markdown tab. Like ``chunks``, never wanted on a normal
    document load (``lazy="raise"`` on the relationship).
    """

    __tablename__ = "document_pages"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    markdown: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="pages")


class IngestionEvent(Base):
    """Append-only audit trail of pipeline events for a document."""

    __tablename__ = "ingestion_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="events")


class NoteVersion(Base):
    """Append-only version history for in-app notes (source ``note``).

    Each edit (or restore) of a note snapshots the note's *previous* title and
    markdown body here before overwriting them, so the full edit history can be
    listed and any prior version restored. ``version_no`` is monotonic per
    document starting at 1; the table mirrors ``IngestionEvent`` in being
    append-only (rows are never updated or deleted except via the document's
    ON DELETE CASCADE).
    """

    __tablename__ = "note_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentComment(Base):
    """User-authored, dated free-text attached to an existing document.

    Distinct from a note (a source='note' Document): a comment annotates
    another document and is embedded as an extra chunk so /ask can find the
    document through it. `created_at` is the recorded date shown in the UI.
    """

    __tablename__ = "document_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="comments")


class AskThread(Base):
    """One Ask conversation: an ordered series of question/answer turns."""

    __tablename__ = "ask_threads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    turns: Mapped[list["AskTurn"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AskTurn.created_at",
    )


class AskTurn(Base):
    """One question/answer turn within a thread (cost + provenance + replay).

    Subsumes the former ``ask_logs`` audit row. ``messages`` holds the
    serialized Anthropic message blocks this turn produced (the user question
    plus assistant ``tool_use`` / ``tool_result`` / final-answer blocks) so a
    follow-up can replay prior tool results without re-querying.
    """

    __tablename__ = "ask_turns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("ask_threads.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    used_tools: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    citations: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    messages: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    thread: Mapped[AskThread] = relationship(back_populates="turns")


class EvalRun(Base):
    """One extraction-quality evaluation run, comparable across versions.

    ``prompt_version``/``model`` hold the modal (most common) pair across the
    evaluated documents for easy filtering; ``version_mix`` records the full
    distribution so a sample spanning versions is never silently misattributed.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    prompt_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    version_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    sample_size: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    per_field: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    overall: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class HeldEmail(Base):
    """An email the mailbox poller held for human review instead of auto-filing.

    Rather than silently filing (or dropping) a message the pipeline is unsure
    about, ``poll_mailbox`` records it here and leaves the original in the IMAP
    folder. ``verdict`` names the hold trigger (``llm_hold`` /
    ``below_substance`` / ``nothing_ingested`` / ``sender_unknown``) and
    ``trace`` snapshots the full selection trace (the
    ``_selection_event_detail`` shape) so the review UI can show what the
    pipeline saw. ``imap_folder``/``imap_uid`` locate the original message for
    a later ingest (the UID is a hint only — UIDs are not stable across
    mailbox UIDVALIDITY changes). Resolution stamps ``status``,
    ``resolved_by_id``/``resolved_at`` and, on ingest, the created
    ``document_ids``; ``last_error`` keeps the most recent failed-resolution
    error. The partial unique index permits only one *open* (``held``) row per
    ``message_id``, so re-polling an unresolved message is idempotent while
    resolved rows keep their history.
    """

    __tablename__ = "held_emails"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verdict: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    trace: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    imap_folder: Mapped[str] = mapped_column(String(255))
    imap_uid: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[HeldEmailStatus] = mapped_column(
        Enum(
            HeldEmailStatus,
            name="held_email_status",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=HeldEmailStatus.HELD,
        server_default=HeldEmailStatus.HELD.value,
        index=True,
    )
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_ids: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Poll idempotency: at most one open (held) row per message_id; resolved
        # rows and messages without a Message-ID are exempt.
        Index(
            "ix_held_emails_message_id_held",
            "message_id",
            unique=True,
            postgresql_where=text("status = 'held' AND message_id IS NOT NULL"),
        ),
    )


class EmailSelectionTrace(Base):
    """A durable per-email audit of skipped items from the mailbox poller.

    Written by ``poll_mailbox`` (and the held-email ingest-anyway override)
    whenever an email's selection filtered or dropped at least one item —
    quiet noise skips such as ``decoration_image`` and
    ``llm_noise_corroborated`` included. ``decisions`` snapshots the FULL
    decision list (``SelectionDecision.as_detail()`` dicts, the same shape as
    the ``email_selection`` event's ``items``), so the row reads as a whole
    email even though only the skips triggered it. This complements the
    per-document ``email_selection`` event: an email whose items were ALL
    skipped produces no document to hang that event on, and this row is then
    the only durable audit of the skip. Best-effort append-only data — a
    failed write never fails a poll.
    """

    __tablename__ = "email_selection_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # Indexed: the read path is "the most recent N rows".
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class FxRate(Base):
    """A reference foreign-exchange rate, base = USD.

    ``rate_to_base`` is the value of one unit of ``currency`` in USD on ``as_of``
    (so USD itself is 1.0 by definition and is handled in code, not stored).
    Conversion picks the row with the greatest ``as_of`` on-or-before the
    document's date (falling back to the earliest), giving date-aware historical
    conversion (see ``library.fx``). Seeded with a researched yearly snapshot by
    migration 0015; rows can be added later to refine accuracy.
    """

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(CHAR(3), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    rate_to_base: Mapped[Decimal] = mapped_column(Numeric(18, 8))

    __table_args__ = (UniqueConstraint("currency", "as_of", name="fx_rates_currency_as_of"),)


class InstanceSetting(Base):
    """One instance-wide operational setting, changeable at runtime.

    The third kind of configuration in library, alongside per-user display
    preferences (``User.preferences``) and environment variables read once at
    startup into ``Settings``. This one is instance-wide *and* mutable without a
    restart — which is what an operational toggle like the LLM backend needs.

    An **override layer**, not a replacement: a missing row means "use the
    ``Settings`` value", so an empty table behaves exactly as the environment
    says. That is also what every existing deployment gets on upgrade, and what
    makes reverting a setting equivalent to deleting its row.

    Deliberately key/value with a JSON ``value`` rather than a typed column per
    setting: the alternative costs a migration for every new toggle.
    """

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # SET NULL on user delete, not CASCADE: removing a user must never silently
    # revert an instance-wide setting to its environment default.
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PaymentOverride(Base):
    """A human correction to the derived payment identity.

    ``MERGE`` joins two documents the rules kept apart; ``SPLIT`` separates two
    the rules joined. ``doc_a < doc_b`` is enforced by a check constraint so a
    pair has one canonical representation.
    """

    __tablename__ = "payment_overrides"
    __table_args__ = (
        CheckConstraint("kind IN ('MERGE','SPLIT')", name="payment_overrides_kind"),
        CheckConstraint("doc_a < doc_b", name="payment_overrides_ordered"),
        UniqueConstraint("kind", "doc_a", "doc_b", name="payment_overrides_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    doc_a: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    doc_b: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chart(Base):
    """A saved question over ``spend_facts``.

    ``rule`` is a serialised :class:`library.charts.rule.Rule`. The two axes
    are independent by design: ``default_grain`` and ``default_split`` are
    only starting positions, and changing either at request time never alters
    the total (spec §9.2).
    """

    __tablename__ = "charts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    question_text: Mapped[str] = mapped_column(Text)
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    default_grain: Mapped[Grain] = mapped_column(
        Enum(
            Grain,
            name="chart_grain",
            native_enum=False,
            length=16,
            # Without this SQLAlchemy persists the member *name* ("MONTH"),
            # which the migration's `default_grain IN ('week',...)` CHECK
            # rejects. Same treatment as every other enum column here.
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=Grain.MONTH,
        server_default=Grain.MONTH.value,
    )
    default_split: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_currency: Mapped[str] = mapped_column(String(3))
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
