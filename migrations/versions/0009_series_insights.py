"""series insights

Cached LLM-generated descriptions of recurring (sender, kind, currency) series.
Precomputed by a background job when a new document joins a series so the chart
tile and the /charts aggregate can show prose without a per-request LLM call.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series_insights",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
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
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_series_insights_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_insights_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_insights")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_insights_sender_kind_currency",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_insights_sender_id"), "series_insights", ["sender_id"], unique=False
    )
    op.create_index(
        op.f("ix_series_insights_kind_id"), "series_insights", ["kind_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_series_insights_kind_id"), table_name="series_insights")
    op.drop_index(op.f("ix_series_insights_sender_id"), table_name="series_insights")
    op.drop_table("series_insights")
