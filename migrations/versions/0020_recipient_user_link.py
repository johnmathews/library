"""recipient → user link

Adds ``recipients.user_id`` — a nullable FK to ``users.id`` so a recipient can
be linked to a user (W13). Creating a user auto-links a recipient named by their
display name, and ingestion resolves a document to that recipient when the
extracted recipient name matches the user's username *or* display name. The FK
is ``ON DELETE SET NULL`` so deleting a user merely unlinks (does not delete)
their recipient, leaving documents addressed to that person intact.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recipients", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_recipients_user_id_users"),
        "recipients",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_recipients_user_id"),
        "recipients",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recipients_user_id"), table_name="recipients")
    op.drop_constraint(op.f("fk_recipients_user_id_users"), "recipients", type_="foreignkey")
    op.drop_column("recipients", "user_id")
