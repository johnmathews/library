"""SQLAlchemy 2.0 declarative models for the Library backend.

Design notes
------------
- Lifecycle/source/language fields use ``sa.Enum(..., native_enum=False)``:
  plain ``VARCHAR`` columns with a CHECK constraint instead of Postgres enum
  types. Adding a value is then an ordinary migration (drop/recreate the
  check) rather than ``ALTER TYPE``, and values stay readable in psql.
- Full-text search uses two STORED generated tsvector columns (Dutch and
  English configs) over title + summary + ocr_text, each with a GIN index.
  Stemming differs per language, so one column cannot serve both.
- ``documents.deleted_at`` implements soft delete; ``ingestion_events`` is an
  append-only audit trail.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
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
    "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, '') || ' ' "
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


class OverrideAction(enum.StrEnum):
    """Direction of a manual series-membership override (see ``SeriesMembershipOverride``)."""

    PIN = "pin"
    EXCLUDE = "exclude"


class SuggestionState(enum.StrEnum):
    """Lifecycle of a proposed authored-series membership (see ``AuthoredSeriesSuggestion``).

    A signature-matching document is recorded as a ``pending`` suggestion for the
    owner to review; ``dismiss``-ing it writes a ``dismissed`` tombstone so the
    same document is never re-suggested for that series.
    """

    PENDING = "pending"
    DISMISSED = "dismissed"


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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


class SeriesInsight(Base):
    """Cached, LLM-generated natural-language description of a recurring series.

    A *series* is one ``(sender_id, kind_id, currency)`` — e.g. EUR utility
    bills from one provider (see ``library.series``). The series statistics are
    computed on the fly, but the prose summary ("bills have crept up ~12% over
    the last year, with a seasonal winter peak") costs an LLM call, so it is
    precomputed by a background job whenever a new document joins the series and
    cached here. ``member_count`` records how many documents the description was
    generated over, so a description left behind by series growth can be spotted
    and regenerated. One row per series: the unique key treats a NULL currency
    as a single bucket (``NULLS NOT DISTINCT``) rather than allowing duplicates.
    """

    __tablename__ = "series_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id", ondelete="CASCADE"), index=True)
    kind_id: Mapped[int] = mapped_column(ForeignKey("kinds.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    description: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    member_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_insights_sender_kind_currency",
            postgresql_nulls_not_distinct=True,
        ),
    )


class SeriesMembershipOverride(Base):
    """A durable manual edit to a recurring series' computed membership.

    Series are computed on the fly as ``(sender_id, kind_id, currency)`` groups
    (see ``library.series``). A user can nudge that computation: ``pin`` a
    document the grouping missed, or ``exclude`` one it wrongly included. The
    override is keyed by series identity + ``document_id`` and applied on every
    future ``summarize_series`` call, mirroring the ``document.extra["corrections"]``
    precedent for extraction edits — but as a first-class table so all overrides
    for a series can be queried (the LLM matcher in W9 reads them as hints).

    A pinned document whose own currency differs from the series is
    FX-converted (see ``library.fx``) into the series currency. One override per
    ``(series, document)``: the unique key treats NULL currency as one bucket.
    """

    __tablename__ = "series_membership_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id", ondelete="CASCADE"), index=True)
    kind_id: Mapped[int] = mapped_column(ForeignKey("kinds.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[OverrideAction] = mapped_column(
        Enum(
            OverrideAction,
            name="series_override_action",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            "document_id",
            name="series_membership_overrides_series_document",
            postgresql_nulls_not_distinct=True,
        ),
    )


class SeriesMetaOverride(Base):
    """A user override for a recurring series' *title* and/or *description*.

    A series is one ``(sender_id, kind_id, currency)`` group (see
    ``library.series``). Its title is normally derived (``sender · cadence
    series``) and its description is the read-only, auto-refreshed cached LLM
    prose (``SeriesInsight``). This table lets a user pin their own title and/or
    description for a series, applied on every ``summarize_series`` call. It is
    kept deliberately separate from ``SeriesInsight`` so user edits are never
    clobbered by the background insight refresh (W12). One row per series
    identity: the unique key treats a NULL currency as a single bucket.
    """

    __tablename__ = "series_meta_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id", ondelete="CASCADE"), index=True)
    kind_id: Mapped[int] = mapped_column(ForeignKey("kinds.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_meta_overrides_series",
            postgresql_nulls_not_distinct=True,
        ),
    )


class AuthoredSeries(Base):
    """A user-curated (*authored*) recurring series (W14).

    Emergent series are detected on the fly as ``(sender_id, kind_id, currency)``
    groups (see ``library.series``). An authored series instead lets a user name
    a series, pick a currency, and add documents explicitly — producing a chart
    even without a natural ≥3-document emergent seed. The membership is the
    explicit set of ``AuthoredSeriesMember`` rows; the statistics are computed on
    the fly by ``summarize_authored_series`` using the authored ``name`` as the
    series title and ``description`` as its prose.
    """

    __tablename__ = "authored_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["AuthoredSeriesMember"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", lazy="selectin"
    )
    suggestions: Mapped[list["AuthoredSeriesSuggestion"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", lazy="selectin"
    )


class AuthoredSeriesMember(Base):
    """One document's membership in an :class:`AuthoredSeries` (deduped per series)."""

    __tablename__ = "authored_series_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    authored_series_id: Mapped[int] = mapped_column(
        ForeignKey("authored_series.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    series: Mapped[AuthoredSeries] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_members_series_document",
        ),
    )


class AuthoredSeriesSuggestion(Base):
    """A proposed (auto-continue) membership for an :class:`AuthoredSeries`.

    When a newly-indexed document mechanically matches an authored series'
    *signature* — the dominant ``(sender_id, kind_id, currency)`` triple of its
    existing members (see ``library.series.derive_signature``) — it is recorded
    here as a ``pending`` suggestion rather than silently added as a member
    (PROPOSE-FOR-REVIEW). The owner then accepts (promoting it to an
    ``AuthoredSeriesMember``) or dismisses it (leaving a ``dismissed`` tombstone
    so it is never re-suggested for this series). The ``signature_*`` columns
    snapshot the matched signature at proposal time; the LLM ``reason``/token
    columns are reserved for the odd-one-out rationale flow and are normally
    NULL for a plain match. One row per ``(series, document)``.
    """

    __tablename__ = "authored_series_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    authored_series_id: Mapped[int] = mapped_column(
        ForeignKey("authored_series.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[SuggestionState] = mapped_column(
        Enum(
            SuggestionState,
            name="suggestion_state",
            native_enum=False,
            length=16,
            values_callable=lambda obj: [member.value for member in obj],
        ),
        default=SuggestionState.PENDING,
        server_default=SuggestionState.PENDING.value,
    )
    reason: Mapped[str | None] = mapped_column(Text)
    signature_sender_id: Mapped[int | None] = mapped_column(Integer)
    signature_kind_id: Mapped[int | None] = mapped_column(Integer)
    signature_currency: Mapped[str | None] = mapped_column(CHAR(3))
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    series: Mapped[AuthoredSeries] = relationship(back_populates="suggestions")

    __table_args__ = (
        UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_suggestions_series_document",
        ),
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
