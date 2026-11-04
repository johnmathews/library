"""OAuth authorization-server tables for MCP connectors

Revision ID: 0040
Revises: 0039

Library becomes its own OAuth 2.1 authorization server so claude.ai, ChatGPT
and Claude Code can connect to ``/mcp`` as a specific Library user
(``library.auth.oauth_provider``). Five tables:

- ``oauth_clients`` — dynamically registered clients (RFC 7591). The client
  secret is stored in plaintext because the MCP SDK compares it directly; it
  identifies a client application, never a user.
- ``oauth_grants`` — one row per user-and-client consent; revoking it kills every
  code and token under it.
- ``oauth_authorization_requests`` — a pending ``/authorize`` waiting on the
  consent page (10 min).
- ``oauth_authorization_codes`` — single-use codes (5 min).
- ``oauth_tokens`` — access and refresh tokens; ``family_id`` groups a refresh
  rotation chain so reuse of a rotated token revokes the whole family.

Request ids, codes and tokens are stored as SHA-256 hex digests only, like
``api_tokens.token_hash``. Nothing existing changes; ``downgrade`` drops the
five tables.

The CHECK's ``name="kind"`` is convention-relative (see 0036): the live
constraint is ``ck_oauth_tokens_kind``.

Create Date: 2026-11-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(255), primary_key=True),
        sa.Column("client_secret", sa.String(255), nullable=True),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("client_info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
    )

    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.String(255),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "client_id", name="uq_oauth_grants_user_client"),
    )
    op.create_index("ix_oauth_grants_client_id", "oauth_grants", ["client_id"])

    op.create_table(
        "oauth_authorization_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_hash", sa.String(64), nullable=False),
        sa.Column(
            "client_id",
            sa.String(255),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id_hash", name="uq_oauth_authorization_requests_id_hash"),
    )
    op.create_index(
        "ix_oauth_authorization_requests_client_id",
        "oauth_authorization_requests",
        ["client_id"],
    )
    op.create_index(
        "ix_oauth_authorization_requests_expires_at",
        "oauth_authorization_requests",
        ["expires_at"],
    )

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column(
            "grant_id",
            sa.Integer(),
            sa.ForeignKey("oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code_hash", name="uq_oauth_authorization_codes_code_hash"),
    )
    op.create_index(
        "ix_oauth_authorization_codes_grant_id", "oauth_authorization_codes", ["grant_id"]
    )
    op.create_index(
        "ix_oauth_authorization_codes_expires_at", "oauth_authorization_codes", ["expires_at"]
    )

    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "grant_id",
            sa.Integer(),
            sa.ForeignKey("oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_oauth_tokens_token_hash"),
        sa.CheckConstraint("kind IN ('access','refresh')", name="kind"),
    )
    op.create_index("ix_oauth_tokens_grant_id", "oauth_tokens", ["grant_id"])
    op.create_index("ix_oauth_tokens_family_id", "oauth_tokens", ["family_id"])
    op.create_index("ix_oauth_tokens_expires_at", "oauth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_authorization_requests")
    op.drop_table("oauth_grants")
    op.drop_table("oauth_clients")
