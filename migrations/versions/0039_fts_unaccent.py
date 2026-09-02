"""fold accents in full-text search

A document whose text carries a diacritic could only be found by typing the
diacritic: searching the plain-ASCII spelling a person would naturally type
returned "No documents match your search", indistinguishable from the document
not existing. Both generated tsvector columns were built with a plain config and
no accent folding, so ``Škoda`` and ``Skoda`` were simply different lexemes.

The fix folds accents on the INDEX side here, and ``library.search`` folds the
same way on the QUERY side. Both are required — folding one alone just moves the
mismatch instead of removing it.

``unaccent()`` is STABLE, not IMMUTABLE (the one-argument form depends on a
runtime dictionary lookup), so it cannot appear in a generated column or an
index. The two-argument form IS immutable once the dictionary is named
explicitly, which is what ``immutable_unaccent`` below pins down — fully
schema-qualified, so the expression cannot change meaning with ``search_path``.

The extension is created here rather than by hand on the host: ``unaccent`` is a
TRUSTED extension from PG13 on, and the application role owns the database, so
this needs no superuser. Verified against the live database (PG 17.11, owner
``library``) before writing this.

Each generated column and its GIN index is dropped and recreated, which forces
every existing row to recompute its vector through the new expression — the
migration is self-backfilling, exactly as 0025 was. On the live archive that is
263 rows / 616 kB of heap, so the table rewrite is negligible.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-02 06:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The text every vector is built from. Held once so the two expressions below
# can differ ONLY in whether the accent fold wraps it.
_SOURCE: str = (
    "coalesce(title, '') || ' ' "
    "|| coalesce(summary, '') || ' ' || coalesce(pages_markdown, ocr_text, '') || ' ' "
    "|| coalesce(topics::text, '')"
)

# 0038 head — no accent folding (used by downgrade).
_OLD_EXPRESSION: str = f"to_tsvector('{{config}}', {_SOURCE})"

# Accent-folded (matches library.models.FTS_EXPRESSION).
_NEW_EXPRESSION: str = f"to_tsvector('{{config}}', public.immutable_unaccent({_SOURCE}))"

# (column suffix, Postgres text-search config) for each generated vector. Both
# configs need the change: folding one silently half-works, because the query
# ORs the two vectors together and the unfolded leg contributes nothing.
_VECTORS: tuple[tuple[str, str], ...] = (("nl", "dutch"), ("en", "english"))

_CREATE_FUNCTION: str = """
CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
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
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(_CREATE_FUNCTION)
    _rebuild(_NEW_EXPRESSION)


def downgrade() -> None:
    # Drop the dependency (the generated columns) BEFORE the function they call,
    # or the DROP FUNCTION fails.
    _rebuild(_OLD_EXPRESSION)
    op.execute("DROP FUNCTION IF EXISTS public.immutable_unaccent(text)")
    # The extension is deliberately left installed. `CREATE EXTENSION IF NOT
    # EXISTS` on the way up cannot tell "we installed it" from "it was already
    # there", so dropping it on the way down risks removing something this
    # migration did not create. An unused extension is harmless; a missing one
    # another object depends on is not.
