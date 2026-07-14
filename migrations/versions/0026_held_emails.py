"""held emails

Adds the ``held_emails`` table backing the email hold-for-review queue. When
the mailbox poller is unsure an email belongs in the library it records the
message here (``verdict`` names the trigger, ``trace`` snapshots the selection
trace) and leaves the original in the IMAP folder instead of auto-filing it.
The owner later ingests or dismisses the row; resolved rows are kept as an
audit trail. A partial unique index permits only one *open* (``held``) row per
``message_id``, making re-polls of an unresolved message idempotent.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "held_emails",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("imap_folder", sa.String(length=255), nullable=False),
        sa.Column("imap_uid", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "held",
                "ingested",
                "dismissed",
                name="held_email_status",
                native_enum=False,
                length=16,
            ),
            server_default="held",
            nullable=False,
        ),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_held_emails_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name=op.f("fk_held_emails_resolved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('held', 'ingested', 'dismissed')",
            name=op.f("ck_held_emails_held_email_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_held_emails")),
    )
    op.create_index(op.f("ix_held_emails_status"), "held_emails", ["status"], unique=False)
    op.create_index(op.f("ix_held_emails_owner_id"), "held_emails", ["owner_id"], unique=False)
    # Poll idempotency: at most one open (held) row per message_id; resolved
    # rows and messages without a Message-ID are exempt.
    op.create_index(
        "ix_held_emails_message_id_held",
        "held_emails",
        ["message_id"],
        unique=True,
        postgresql_where=sa.text("status = 'held' AND message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_held_emails_message_id_held", table_name="held_emails")
    op.drop_index(op.f("ix_held_emails_owner_id"), table_name="held_emails")
    op.drop_index(op.f("ix_held_emails_status"), table_name="held_emails")
    op.drop_table("held_emails")
