"""general document store

Adds three general-reference kinds (reference, research, note) and a nullable
``documents.topics`` JSONB column holding human-readable topic phrases for
long, multi-topic general material.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-28 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_KINDS: list[dict[str, str]] = [
    {"slug": "reference", "name": "Reference"},
    {"slug": "research", "name": "Research paper"},
    {"slug": "note", "name": "Note"},
]


def upgrade() -> None:
    kinds_table = sa.table(
        "kinds",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
    )
    op.bulk_insert(kinds_table, NEW_KINDS)
    op.add_column(
        "documents",
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "topics")
    op.execute("DELETE FROM kinds WHERE slug IN ('reference', 'research', 'note')")
