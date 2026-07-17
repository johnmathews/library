"""matters

Adds a first-class "business matter" grouping primitive: a ``matters`` table
(slug/name/hint, soft-archive via ``archived_at``) and a ``document_matters``
many-to-many join, mirroring the ``projects``/``document_projects`` pattern. A
matter is an evergreen subject category (e.g. "car insurance") that a document
may belong to any number of; unlike a project it is not a time-bound endeavor.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-17 15:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matters")),
        sa.UniqueConstraint("slug", name=op.f("uq_matters_slug")),
    )
    op.create_table(
        "document_matters",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("matter_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_matters_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_document_matters_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "matter_id", name=op.f("pk_document_matters")),
    )
    op.create_index(
        op.f("ix_document_matters_matter_id"),
        "document_matters",
        ["matter_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_matters_matter_id"), table_name="document_matters")
    op.drop_table("document_matters")
    op.drop_table("matters")
