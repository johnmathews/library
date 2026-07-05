"""document comments + chunk provenance

Adds ``document_comments`` — user-authored, dated free-text attached to an
existing document (distinct from a ``source='note'`` Document). A comment is
later embedded as an extra chunk so ``/ask`` can find the document through it;
``document_chunks.comment_id`` is the nullable back-reference recording that
provenance (NULL for chunks derived from the document's own text).

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_comments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_comments_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_document_comments_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_comments")),
    )
    op.create_index(
        op.f("ix_document_comments_document_id"),
        "document_comments",
        ["document_id"],
        unique=False,
    )

    op.add_column("document_chunks", sa.Column("comment_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_document_chunks_comment_id_document_comments"),
        "document_chunks",
        "document_comments",
        ["comment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_document_chunks_comment_id"),
        "document_chunks",
        ["comment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_chunks_comment_id"), table_name="document_chunks")
    op.drop_constraint(
        op.f("fk_document_chunks_comment_id_document_comments"),
        "document_chunks",
        type_="foreignkey",
    )
    op.drop_column("document_chunks", "comment_id")

    op.drop_index(op.f("ix_document_comments_document_id"), table_name="document_comments")
    op.drop_table("document_comments")
