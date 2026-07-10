"""page markdown in full-text search

Adds ``documents.pages_markdown`` — the concatenated per-page vision markdown
(the "understood layer") denormalized onto the document — and folds it into the
two STORED generated tsvector columns (``search_vector_nl`` / ``search_vector_en``)
via ``coalesce(pages_markdown, ocr_text, '')``. A generated column can only read
same-row columns, so it cannot reach the child ``document_pages`` table directly;
this mirror makes image-PDF body text (which OCR never captured) findable by plain
FTS, matching the prefer-markdown rule the embed/Ask paths already use.

The new column is backfilled from ``document_pages`` in one aggregation, then each
tsvector column and its GIN index is dropped and recreated so every existing row
recomputes its vector off the backfilled text — the migration is self-backfilling.
The coalesce (rather than concatenating both markdown and ocr_text) avoids
double-indexing born-digital docs and notes, where ``pages_markdown == ocr_text``.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-10 13:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Expression WITHOUT pages_markdown (0024 head, used by downgrade).
_OLD_EXPRESSION: str = (
    "to_tsvector('{config}', coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(ocr_text, '') || ' ' "
    "|| coalesce(topics::text, ''))"
)

# Expression WITH pages_markdown (matches library.models.FTS_EXPRESSION).
_NEW_EXPRESSION: str = (
    "to_tsvector('{config}', coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(pages_markdown, ocr_text, '') || ' ' "
    "|| coalesce(topics::text, ''))"
)

# (column suffix, Postgres text-search config) for each generated vector.
_VECTORS: tuple[tuple[str, str], ...] = (("nl", "dutch"), ("en", "english"))

# Concatenate a document's page markdown in page order, matching the app-side
# helper (library.markdown.apply._document_markdown_text) delimiter exactly.
_BACKFILL_SQL: str = """
    UPDATE documents AS d
    SET pages_markdown = p.md
    FROM (
        SELECT document_id, string_agg(markdown, E'\\n\\n' ORDER BY page_number) AS md
        FROM document_pages
        GROUP BY document_id
    ) AS p
    WHERE p.document_id = d.id
"""


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
    # Add the column and backfill it BEFORE rebuilding the generated vectors, so
    # the recomputed tsvectors pick up existing documents' page markdown.
    op.add_column("documents", sa.Column("pages_markdown", sa.Text(), nullable=True))
    op.execute(_BACKFILL_SQL)
    _rebuild(_NEW_EXPRESSION)


def downgrade() -> None:
    # Restore the pages_markdown-free expression FIRST (so the generated columns
    # no longer depend on the column), then drop the column.
    _rebuild(_OLD_EXPRESSION)
    op.drop_column("documents", "pages_markdown")
