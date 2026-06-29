"""seed quote kind

Adds a ``quote`` document kind alongside the originally seeded set (see
``0001_initial_schema.SEED_KINDS``), so quotes/estimates can be classified
distinctly from invoices and excluded from spend totals downstream. The
upgrade is idempotent (skips the insert if a ``quote`` row already exists,
e.g. created via ``POST /api/kinds`` before this migration ran); the
downgrade removes the row only if no document still references it.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-29 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


QUOTE_KIND: dict[str, str] = {"slug": "quote", "name": "Quote"}


def upgrade() -> None:
    kinds = sa.table(
        "kinds",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
    )
    op.execute(
        sa.insert(kinds).from_select(
            ["slug", "name"],
            sa.select(
                sa.literal(QUOTE_KIND["slug"]),
                sa.literal(QUOTE_KIND["name"]),
            ).where(
                ~sa.exists(
                    sa.select(sa.literal(1))
                    .select_from(kinds)
                    .where(kinds.c.slug == QUOTE_KIND["slug"])
                )
            ),
        )
    )


def downgrade() -> None:
    # Only remove the seeded row if nothing references it (the FK is
    # ON DELETE SET NULL, so deleting it would silently null documents).
    op.execute(
        "DELETE FROM kinds WHERE slug = 'quote' "
        "AND NOT EXISTS (SELECT 1 FROM documents d "
        "JOIN kinds k ON d.kind_id = k.id WHERE k.slug = 'quote')"
    )
