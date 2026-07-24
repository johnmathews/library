"""smart groups: semantic authored series

Adds semantic-mode membership learning to authored series:
- authored_series.mode (manual | semantic)
- authored_series_members.origin (manual | accepted_suggestion | auto)
- authored_series_suggestions.score (float, similarity of a backfill match)
- authored_series_exclusions (negative examples written on prune/dismiss)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "authored_series",
        sa.Column(
            "mode",
            sa.Enum("manual", "semantic", name="series_mode", native_enum=False, length=16),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "authored_series_members",
        sa.Column(
            "origin",
            sa.Enum(
                "manual",
                "accepted_suggestion",
                "auto",
                name="member_origin",
                native_enum=False,
                length=24,
            ),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "authored_series_suggestions",
        sa.Column("score", sa.Float(), nullable=True),
    )
    op.create_table(
        "authored_series_exclusions",
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
            name=op.f("fk_authored_series_exclusions_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_exclusions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_exclusions")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_exclusions_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_authored_series_id"),
        "authored_series_exclusions",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_document_id"),
        "authored_series_exclusions",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_authored_series_exclusions_document_id"),
        table_name="authored_series_exclusions",
    )
    op.drop_index(
        op.f("ix_authored_series_exclusions_authored_series_id"),
        table_name="authored_series_exclusions",
    )
    op.drop_table("authored_series_exclusions")
    op.drop_column("authored_series_suggestions", "score")
    op.drop_column("authored_series_members", "origin")
    op.drop_column("authored_series", "mode")
