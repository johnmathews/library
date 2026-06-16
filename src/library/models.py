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
    "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, ''))"
)


class DocumentStatus(enum.StrEnum):
    """Processing lifecycle of a document."""

    RECEIVED = "received"
    OCR = "ocr"
    EXTRACT = "extract"
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


class DocumentLanguage(enum.StrEnum):
    """Detected language of a document's text."""

    NLD = "nld"
    ENG = "eng"
    MIXED = "mixed"
    UNKNOWN = "unknown"


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
    kind: Mapped[Kind | None] = relationship(lazy="selectin")
    tags: Mapped[list[Tag]] = relationship(secondary=document_tags, lazy="selectin")
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

    __table_args__ = (
        Index("ix_documents_search_vector_nl", "search_vector_nl", postgresql_using="gin"),
        Index("ix_documents_search_vector_en", "search_vector_en", postgresql_using="gin"),
    )


class DocumentChunk(Base):
    """A page-sized slice of a document's text plus its embedding vector.

    One row per chunk (see ``embedding.chunker``); ``chunk_index`` is the
    1-based ordinal of the chunk within the document (OCR text carries no
    reliable page boundaries, so this is a position, not a PDF page number).
    The embedding is a bge-m3 1024-dim vector used for semantic retrieval; an
    HNSW index over ``embedding`` (cosine ops) backs nearest-neighbour search.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
