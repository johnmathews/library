"""extraction quality

Add documents.review_status and the eval_runs table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVIEW_STATUS = sa.Enum(
    "verified",
    "needs_review",
    "unreviewed",
    name="review_status",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "review_status",
            _REVIEW_STATUS,
            server_default="unreviewed",
            nullable=False,
        ),
    )
    op.create_index("ix_documents_review_status", "documents", ["review_status"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "version_mix",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "per_field",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "overall",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_index("ix_documents_review_status", table_name="documents")
    op.drop_column("documents", "review_status")
