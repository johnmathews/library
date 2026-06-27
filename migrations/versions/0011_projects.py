"""projects

Adds a first-class project/collection grouping primitive: a ``projects`` table
(slug/name/description, soft-archive via ``archived_at``) and a
``document_projects`` many-to-many join, mirroring the ``tags``/``document_tags``
pattern. A document may belong to several projects.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("slug", name=op.f("uq_projects_slug")),
    )
    op.create_table(
        "document_projects",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_projects_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_document_projects_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "project_id", name=op.f("pk_document_projects")),
    )
    op.create_index(
        op.f("ix_document_projects_project_id"),
        "document_projects",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_projects_project_id"), table_name="document_projects")
    op.drop_table("document_projects")
    op.drop_table("projects")
