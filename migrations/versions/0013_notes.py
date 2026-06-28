"""notes: in-app authoring with version history

Adds the ``note`` value to the ``documents.source`` enum and creates the
append-only ``note_versions`` table that snapshots a note's previous
(title, body) on every edit/restore.

The source enum is ``native_enum=False`` (a VARCHAR guarded by a CHECK rather
than a Postgres enum type), so a new value means recreating the CHECK. The
constraint is dropped/created with explicit DDL (``IF EXISTS`` on the drop) so
the migration is idempotent and independent of Alembic's naming-convention
rendering — and it leaves the column actively enforcing the enum going forward.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The CHECK constraint name follows the model naming convention
# (ck_%(table_name)s_%(constraint_name)s with enum name "document_source").
_SOURCE_CHECK = "ck_documents_document_source"
_SOURCES_WITH_NOTE = ("upload", "consume", "email", "api", "mcp", "import", "note")
_SOURCES_WITHOUT_NOTE = ("upload", "consume", "email", "api", "mcp", "import")


def _set_source_check(values: tuple[str, ...]) -> None:
    joined = ", ".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TABLE documents DROP CONSTRAINT IF EXISTS {_SOURCE_CHECK}")
    op.execute(f"ALTER TABLE documents ADD CONSTRAINT {_SOURCE_CHECK} CHECK (source IN ({joined}))")


def upgrade() -> None:
    _set_source_check(_SOURCES_WITH_NOTE)

    op.create_table(
        "note_versions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_note_versions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_versions")),
    )
    op.create_index(
        op.f("ix_note_versions_document_id"), "note_versions", ["document_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_note_versions_document_id"), table_name="note_versions")
    op.drop_table("note_versions")

    _set_source_check(_SOURCES_WITHOUT_NOTE)
