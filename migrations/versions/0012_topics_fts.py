"""topics in full-text search

Extends the two STORED generated tsvector columns (``search_vector_nl`` /
``search_vector_en``) to also index ``documents.topics``. The JSONB list is
folded into the document text via the IMMUTABLE ``topics::text`` cast (a
set-returning ``unnest`` is not allowed in a generated column). Each column and
its GIN index is dropped and recreated so every existing row recomputes its
vector — the migration is self-backfilling.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-28 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Expression WITHOUT topics (current head, used by downgrade).
_OLD_EXPRESSION: str = (
    "to_tsvector('{config}', coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, ''))"
)

# Expression WITH topics (matches library.models.FTS_EXPRESSION).
_NEW_EXPRESSION: str = (
    "to_tsvector('{config}', coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, '') || ' ' "
    "|| coalesce(topics::text, ''))"
)

# (column suffix, Postgres text-search config) for each generated vector.
_VECTORS: tuple[tuple[str, str], ...] = (("nl", "dutch"), ("en", "english"))


def _rebuild(expression: str) -> None:
    """Drop and recreate each generated tsvector column + GIN index.

    Recreating the column forces every row to recompute the STORED vector with
    ``expression`` (config substituted), so the change backfills itself.
    """
    for suffix, config in _VECTORS:
        index = f"ix_documents_search_vector_{suffix}"
        column = f"search_vector_{suffix}"
        op.drop_index(index, table_name="documents", postgresql_using="gin")
        op.drop_column("documents", column)
        op.add_column(
            "documents",
            sa.Column(
                column,
                postgresql.TSVECTOR(),
                sa.Computed(expression.format(config=config), persisted=True),
                nullable=True,
            ),
        )
        op.create_index(
            index,
            "documents",
            [column],
            unique=False,
            postgresql_using="gin",
        )


def upgrade() -> None:
    _rebuild(_NEW_EXPRESSION)


def downgrade() -> None:
    _rebuild(_OLD_EXPRESSION)
