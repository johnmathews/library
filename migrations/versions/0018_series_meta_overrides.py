"""series meta overrides

Adds ``series_meta_overrides`` — user-authored overrides for a recurring
``(sender_id, kind_id, currency)`` series' *title* and *description* (W12).

A series title is normally derived (``sender · cadence series``) and its
description is the read-only, auto-refreshed cached LLM prose
(``series_insights``). This table is kept deliberately separate from
``series_insights`` so a user's edits are never clobbered by the background
insight refresh. One row per series identity; the unique key treats a NULL
currency as a single bucket (``NULLS NOT DISTINCT``), mirroring
``series_membership_overrides`` (migration 0015).

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-29 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series_meta_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
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
            name=op.f("fk_series_meta_overrides_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_meta_overrides_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_meta_overrides")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_meta_overrides_series",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_meta_overrides_sender_id"),
        "series_meta_overrides",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_meta_overrides_kind_id"),
        "series_meta_overrides",
        ["kind_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_series_meta_overrides_kind_id"), table_name="series_meta_overrides")
    op.drop_index(op.f("ix_series_meta_overrides_sender_id"), table_name="series_meta_overrides")
    op.drop_table("series_meta_overrides")
