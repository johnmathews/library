"""authored series suggestions

Adds ``authored_series_suggestions`` — proposed (auto-continue) memberships for
an authored series.

When a newly-indexed document mechanically matches an authored series' signature
— the dominant ``(sender_id, kind_id, currency)`` triple of its existing members
(see ``library.series.derive_signature``) — it is recorded here as a ``pending``
suggestion rather than silently added as a member (PROPOSE-FOR-REVIEW). The owner
then accepts (promoting it to an ``authored_series_members`` row) or dismisses it
(leaving a ``dismissed`` tombstone so it is never re-suggested for the series).
The ``signature_*`` columns snapshot the matched signature; the LLM
``reason``/token columns are reserved for the odd-one-out rationale flow. One row
per ``(series, document)``.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authored_series_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "dismissed",
                name="suggestion_state",
                native_enum=False,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("signature_sender_id", sa.Integer(), nullable=True),
        sa.Column("signature_kind_id", sa.Integer(), nullable=True),
        sa.Column("signature_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
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
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_suggestions_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_suggestions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'dismissed')",
            name=op.f("ck_authored_series_suggestions_suggestion_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_suggestions")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_suggestions_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_suggestions_authored_series_id"),
        "authored_series_suggestions",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_suggestions_document_id"),
        "authored_series_suggestions",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_authored_series_suggestions_document_id"),
        table_name="authored_series_suggestions",
    )
    op.drop_index(
        op.f("ix_authored_series_suggestions_authored_series_id"),
        table_name="authored_series_suggestions",
    )
    op.drop_table("authored_series_suggestions")
