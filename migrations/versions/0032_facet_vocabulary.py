"""facet vocabulary

Five tables backing the controlled label vocabulary that replaces free-form
tags (design spec layer A).

Two constraints carry the whole model and are worth naming:

``document_labels`` has a composite primary key ``(document_id, facet_id)``,
so a document holds at most one value per facet. That is what a GROUP BY over
a facet relies on to avoid double-counting a document.

``facet_values`` carries a redundant ``UNIQUE (id, facet_id)`` purely so
``document_labels`` can hold a composite foreign key on
``(facet_value_id, facet_id)``. Without it a label row can claim one facet
while pointing at another facet's value, and every aggregate over that facet is
silently wrong.

``parent_id`` is nullable and unused at ship. It exists so moving a facet to two
levels later is a data change rather than a migration.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "facets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("key", name="facets_key"),
    )
    op.create_table(
        "facet_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("facet_values.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("facet_id", "key", name="facet_values_facet_key"),
        sa.UniqueConstraint("id", "facet_id", name="facet_values_id_facet"),
    )
    op.create_table(
        "facet_value_aliases",
        sa.Column(
            "facet_value_id",
            sa.Integer(),
            sa.ForeignKey("facet_values.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alias", sa.String(255), primary_key=True),
    )
    op.create_table(
        "document_labels",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("facet_value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="document_labels_value_facet",
        ),
    )
    op.create_index("ix_document_labels_value", "document_labels", ["facet_value_id"])
    op.create_table(
        "facet_value_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suggested_label", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "facet_id", "document_id", "suggested_label", name="facet_value_suggestions_unique"
        ),
    )


def downgrade() -> None:
    op.drop_table("facet_value_suggestions")
    op.drop_index("ix_document_labels_value", table_name="document_labels")
    op.drop_table("document_labels")
    op.drop_table("facet_value_aliases")
    op.drop_table("facet_values")
    op.drop_table("facets")
