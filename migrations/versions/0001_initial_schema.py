"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-10 18:41:36.264477

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_KINDS: list[dict[str, str]] = [
    {"slug": "invoice", "name": "Invoice"},
    {"slug": "receipt", "name": "Receipt"},
    {"slug": "certificate", "name": "Certificate"},
    {"slug": "utility-bill", "name": "Utility bill"},
    {"slug": "parking-ticket", "name": "Parking ticket"},
    {"slug": "warranty", "name": "Warranty"},
    {"slug": "manual", "name": "Manual"},
    {"slug": "letter", "name": "Letter"},
    {"slug": "contract", "name": "Contract"},
    {"slug": "ticket", "name": "Ticket"},
    {"slug": "other", "name": "Other"},
]


def upgrade() -> None:
    op.create_table(
        "kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kinds")),
        sa.UniqueConstraint("slug", name=op.f("uq_kinds_slug")),
    )
    op.create_table(
        "senders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_senders")),
        sa.UniqueConstraint("name", name=op.f("uq_senders_name")),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("slug", name=op.f("uq_tags_slug")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_api_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "ocr",
                "extract",
                "indexed",
                "failed",
                name="document_status",
                native_enum=False,
                length=16,
            ),
            server_default="received",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "upload",
                "consume",
                "email",
                "api",
                "mcp",
                "import",
                name="document_source",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column(
            "language",
            sa.Enum(
                "nld",
                "eng",
                "mixed",
                "unknown",
                name="document_language",
                native_enum=False,
                length=8,
            ),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("amount_total", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("searchable_pdf", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("original_filename", sa.String(length=1024), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("paperless_id", sa.Integer(), nullable=True),
        sa.Column("uploader_id", sa.Integer(), nullable=True),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("kind_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector_nl",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('dutch', coalesce(title, '') || ' ' "
                "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "search_vector_en",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' "
                "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"], ["kinds.id"], name=op.f("fk_documents_kind_id_kinds"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_documents_sender_id_senders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"],
            ["users.id"],
            name=op.f("fk_documents_uploader_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("paperless_id", name=op.f("uq_documents_paperless_id")),
        sa.UniqueConstraint("sha256", name=op.f("uq_documents_sha256")),
    )
    op.create_index(op.f("ix_documents_created_at"), "documents", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_documents_document_date"), "documents", ["document_date"], unique=False
    )
    op.create_index(op.f("ix_documents_kind_id"), "documents", ["kind_id"], unique=False)
    op.create_index(
        "ix_documents_search_vector_en",
        "documents",
        ["search_vector_en"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_documents_search_vector_nl",
        "documents",
        ["search_vector_nl"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(op.f("ix_documents_sender_id"), "documents", ["sender_id"], unique=False)
    op.create_index(op.f("ix_documents_status"), "documents", ["status"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_tags_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_document_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("document_id", "tag_id", name=op.f("pk_document_tags")),
    )
    op.create_table(
        "ingestion_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_ingestion_events_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_events")),
    )
    op.create_index(
        op.f("ix_ingestion_events_document_id"), "ingestion_events", ["document_id"], unique=False
    )

    kinds_table = sa.table(
        "kinds",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
    )
    op.bulk_insert(kinds_table, SEED_KINDS)


def downgrade() -> None:
    op.drop_index(op.f("ix_ingestion_events_document_id"), table_name="ingestion_events")
    op.drop_table("ingestion_events")
    op.drop_table("document_tags")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_documents_status"), table_name="documents")
    op.drop_index(op.f("ix_documents_sender_id"), table_name="documents")
    op.drop_index("ix_documents_search_vector_nl", table_name="documents", postgresql_using="gin")
    op.drop_index("ix_documents_search_vector_en", table_name="documents", postgresql_using="gin")
    op.drop_index(op.f("ix_documents_kind_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_document_date"), table_name="documents")
    op.drop_index(op.f("ix_documents_created_at"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_table("users")
    op.drop_table("tags")
    op.drop_table("senders")
    op.drop_table("kinds")
