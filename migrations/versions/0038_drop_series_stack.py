"""drop the legacy series stack's seven tables

Plan 5 of the charts redesign. Every module, route, job and ORM class that read
these tables was deleted in the previous pull request and deployed; this is the
irreversible half, split out so that between the two deploys a ``git revert``
plus a redeploy of the previous image still had its rows to come back to. That
gap is now closed.

``downgrade`` restores **schema, not rows**. It recreates the seven tables
empty — mirroring the ``create_table`` calls of 0009, 0015, 0018, 0019, 0021 and
0029, including the columns 0029 later added to three of them — so an older
image can boot against the schema. The data is gone; only a backup brings it
back.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Children before parents. ``authored_series_members``, ``_suggestions`` and
#: ``_exclusions`` each carry a ``CASCADE`` foreign key to ``authored_series``,
#: so ``authored_series`` cannot go first without a ``CASCADE`` on the DROP —
#: and an explicit order is the version of that which cannot take a table
#: nobody listed. The other three stand alone (their foreign keys point *out*,
#: at ``senders``/``kinds``/``documents``, which all survive).
_DROP_ORDER: tuple[str, ...] = (
    "authored_series_members",
    "authored_series_suggestions",
    "authored_series_exclusions",
    "authored_series",
    "series_membership_overrides",
    "series_meta_overrides",
    "series_insights",
)


def upgrade() -> None:
    # Postgres drops a table's indexes and constraints with the table, so the
    # per-index `drop_index` calls the source migrations' downgrades make are
    # not needed here.
    for table in _DROP_ORDER:
        op.drop_table(table)


def downgrade() -> None:
    # Parents before children, the mirror of `_DROP_ORDER`.

    # ---- series_insights (0009) -------------------------------------------
    op.create_table(
        "series_insights",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
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
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_series_insights_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_insights_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_insights")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_insights_sender_kind_currency",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_insights_sender_id"), "series_insights", ["sender_id"], unique=False
    )
    op.create_index(
        op.f("ix_series_insights_kind_id"), "series_insights", ["kind_id"], unique=False
    )

    # ---- series_membership_overrides (0015) -------------------------------
    op.create_table(
        "series_membership_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "pin",
                "exclude",
                name="series_override_action",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_series_membership_overrides_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_membership_overrides_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_series_membership_overrides_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_membership_overrides")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            "document_id",
            name="series_membership_overrides_series_document",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_membership_overrides_sender_id"),
        "series_membership_overrides",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_membership_overrides_kind_id"),
        "series_membership_overrides",
        ["kind_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_membership_overrides_document_id"),
        "series_membership_overrides",
        ["document_id"],
        unique=False,
    )

    # ---- series_meta_overrides (0018) -------------------------------------
    op.create_table(
        "series_meta_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_series_meta_overrides_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_meta_overrides_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_meta_overrides")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            name="series_meta_overrides_series",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_meta_overrides_sender_id"),
        "series_meta_overrides",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_meta_overrides_kind_id"),
        "series_meta_overrides",
        ["kind_id"],
        unique=False,
    )

    # ---- authored_series (0019) + `mode` (0029) ---------------------------
    op.create_table(
        "authored_series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
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
        sa.Column(
            "mode",
            sa.Enum("manual", "semantic", name="series_mode", native_enum=False, length=16),
            server_default="manual",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_authored_series_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series")),
    )
    op.create_index(
        op.f("ix_authored_series_owner_id"),
        "authored_series",
        ["owner_id"],
        unique=False,
    )

    # ---- authored_series_members (0019) + `origin` (0029) -----------------
    op.create_table(
        "authored_series_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum(
                "manual",
                "accepted_suggestion",
                "auto",
                name="member_origin",
                native_enum=False,
                length=24,
            ),
            server_default="manual",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_members_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_members_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_members")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_members_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_members_authored_series_id"),
        "authored_series_members",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_members_document_id"),
        "authored_series_members",
        ["document_id"],
        unique=False,
    )

    # ---- authored_series_suggestions (0021) + `score` (0029) --------------
    op.create_table(
        "authored_series_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "dismissed",
                name="suggestion_state",
                native_enum=False,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("signature_sender_id", sa.Integer(), nullable=True),
        sa.Column("signature_kind_id", sa.Integer(), nullable=True),
        sa.Column("signature_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
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
        sa.Column("score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_suggestions_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_suggestions_document_id_documents"),
            ondelete="CASCADE",
        ),
        # `sa.Enum(native_enum=False)` writes a VARCHAR and no CHECK, so 0021's
        # state vocabulary is enforced by this constraint alone. `op.f` keeps
        # the name verbatim rather than substituting it into the "ck" naming
        # convention a second time.
        sa.CheckConstraint(
            "state IN ('pending', 'dismissed')",
            name=op.f("ck_authored_series_suggestions_suggestion_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_suggestions")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_suggestions_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_suggestions_authored_series_id"),
        "authored_series_suggestions",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_suggestions_document_id"),
        "authored_series_suggestions",
        ["document_id"],
        unique=False,
    )

    # ---- authored_series_exclusions (0029) --------------------------------
    op.create_table(
        "authored_series_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authored_series_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["authored_series_id"],
            ["authored_series.id"],
            name=op.f("fk_authored_series_exclusions_authored_series_id_authored_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_authored_series_exclusions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authored_series_exclusions")),
        sa.UniqueConstraint(
            "authored_series_id",
            "document_id",
            name="authored_series_exclusions_series_document",
        ),
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_authored_series_id"),
        "authored_series_exclusions",
        ["authored_series_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authored_series_exclusions_document_id"),
        "authored_series_exclusions",
        ["document_id"],
        unique=False,
    )
