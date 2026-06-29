"""authored series

Adds ``authored_series`` and ``authored_series_members`` — user-curated
(*authored*) recurring series (W14).

Emergent series are detected on the fly as ``(sender_id, kind_id, currency)``
groups with enough amount-bearing documents (see ``library.series``). An
authored series instead lets a user name a series, pick a currency, and add
documents explicitly — producing a chart even without a natural ≥3-document
emergent seed. ``authored_series`` holds the series identity (name, optional
description/currency, optional owner); ``authored_series_members`` is the
explicit membership (one row per document, deduped per series).

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authored_series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_authored_series_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series")),
    )
    op.create_index(
        op.f("ix_authored_series_owner_id"),
        "authored_series",
        ["owner_id"],
        unique=False,
    )
    op.create_table(
        "authored_series_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_members_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_members_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_members")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_members_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_members_authored_series_id"),
        "authored_series_members",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_members_document_id"),
        "authored_series_members",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_authored_series_members_document_id"),
        table_name="authored_series_members",
    )
    op.drop_index(
        op.f("ix_authored_series_members_authored_series_id"),
        table_name="authored_series_members",
    )
    op.drop_table("authored_series_members")
    op.drop_index(op.f("ix_authored_series_owner_id"), table_name="authored_series")
    op.drop_table("authored_series")
