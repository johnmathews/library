"""recipient field

Adds a ``recipients`` lookup table (mirroring ``senders``) and a nullable
``documents.recipient_id`` FK, so each document can record who it was addressed
to. Existing documents are backfilled to a seeded "John" recipient.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-29 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    recipients = op.create_table(
        "recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipients")),
        sa.UniqueConstraint("name", name=op.f("uq_recipients_name")),
    )

    op.add_column("documents", sa.Column("recipient_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_recipient_id_recipients"),
        "documents",
        "recipients",
        ["recipient_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_documents_recipient_id"),
        "documents",
        ["recipient_id"],
        unique=False,
    )

    op.bulk_insert(recipients, [{"name": "John"}])
    op.execute(
        "UPDATE documents SET recipient_id = "
        "(SELECT id FROM recipients WHERE name = 'John') "
        "WHERE recipient_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_recipient_id"), table_name="documents")
    op.drop_constraint(
        op.f("fk_documents_recipient_id_recipients"),
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "recipient_id")
    op.drop_table("recipients")
