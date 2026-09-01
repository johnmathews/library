"""the charts table

Revision ID: 0036
Revises: 0035

A saved question, its rule, and the axes it defaults to (spec §9.1, §6). The
time axis (``default_grain``) and the split axis (``default_split``) are
independent starting positions, not a query cache: changing either at request
time never alters the total (spec §9.2).

``default_grain`` is a ``native_enum=False`` column, so the CHECK constraint
below is what actually restricts it to the four grains — SQLAlchemy 2.0
defaults ``Enum.create_constraint`` to False and does not add one on its own
(the same lesson 0034 already paid for on ``amount_kind``).

The CHECK's ``name="default_grain"`` looks unprefixed on purpose: Alembic's
``"ck"`` naming-convention template is ``"ck_%(table_name)s_%(constraint_name)s"``,
and an explicit name is substituted *into* the ``%(constraint_name)s`` token —
so a name already carrying the ``ck_charts_`` prefix would be prefixed twice
(``ck_charts_ck_charts_default_grain``) in the live database. Passing the
convention-relative suffix here is what makes the actual constraint name
``ck_charts_default_grain``. ``UniqueConstraint``/foreign-key/primary-key
templates carry no such token, so ``uq_charts_name`` above is used verbatim.

``default_split`` is nullable with no default: a chart with no split is one
series, the shape of the seeded "All spending" card before the owner picks an
axis (spec §10.1).

Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column(
            "rule",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "default_grain",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'month'"),
        ),
        sa.Column("default_split", sa.String(64), nullable=True),
        sa.Column("display_currency", sa.String(3), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_charts_name"),
        sa.CheckConstraint(
            "default_grain IN ('week','quarter','month','year')",
            name="default_grain",
        ),
    )


def downgrade() -> None:
    op.drop_table("charts")
