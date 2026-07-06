"""deleted_at index

Adds an index on ``documents.deleted_at`` so the Recently-Deleted list
(``deleted_at IS NOT NULL``) and the daily purge scan (``deleted_at < cutoff``)
do not sequentially scan the whole table.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-06 20:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_documents_deleted_at"),
        "documents",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_deleted_at"), table_name="documents")
