"""email selection traces

Adds the ``email_selection_traces`` table: a durable per-email audit of
skipped items. Whenever a processed email's selection filtered or dropped at
least one item (the quiet noise skips — ``signature_image``, ``tiny_image``,
``decoration_image``, ``non_document_type``, ``llm_noise_corroborated`` —
included, alongside the user-facing ``oversize``/``unsupported_type``/
``error`` drops), the poller stores one row here with the full decision list
(``SelectionDecision.as_detail()`` dicts, the ``email_selection`` event
shape). Previously that trace was persisted only as an ``email_selection``
event on each NEW sibling document, so an email whose items were ALL skipped
left nothing but a container log line — a wrongly-skipped attachment was
undiscoverable without grepping logs. Exposed read-only via
GET /api/settings/email-triage/recent-skips.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_selection_traces",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_selection_traces")),
    )
    # The read path is "the most recent N rows" — index the ordering key.
    op.create_index(
        op.f("ix_email_selection_traces_created_at"),
        "email_selection_traces",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_email_selection_traces_created_at"), table_name="email_selection_traces")
    op.drop_table("email_selection_traces")
