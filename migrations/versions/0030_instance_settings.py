"""instance_settings

Adds a small key/value store for instance-wide operational settings that an
admin can change at runtime, without a redeploy or a container restart.

Everything configurable in library until now was either a per-user *display*
preference (the ``users.preferences`` blob) or an environment variable read once
at startup into ``Settings``. The LLM backend switch needs a third thing: it is
instance-wide, not per-user, and it has to be changeable while the app runs.

Deliberately generic (``key``/``value`` with a JSON value) rather than a typed
column per setting: each new operational toggle would otherwise cost a
migration, and the set of these is expected to grow slowly and unpredictably.
Reads fall back to the ``Settings`` value when a key is absent, so this table is
an override layer — an empty table means "behave exactly as the environment
says", which is also what every existing deployment gets on upgrade.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-20 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Who last changed it, for the audit trail an operational toggle needs.
        # SET NULL rather than CASCADE: deleting a user must not silently revert
        # an instance-wide setting to its environment default.
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_instance_settings_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_instance_settings")),
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
