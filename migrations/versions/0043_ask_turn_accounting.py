"""ask_turns: record the backend, the input-token split and the model calls

Revision ID: 0043
Revises: 0042

A turn's ``cost_usd`` could not be explained after the fact: nothing said which
backend answered it, how its ``input_tokens`` total divided into fresh input,
cache writes and cache reads (which bill at very different rates), or how many
model requests the tool loop made. Reconstructing one conversation needed the
Prometheus token counter. These five nullable columns hold that per turn.

The token split and ``model_calls`` were never recorded for existing turns, so
they stay null. ``backend`` is filled only where the history proves it:

* ``api`` before 2026-08-20 14:13 UTC, when the release that added the
  subscription backend merged. Until then the Messages API was the only one.
* the stored ``llm_backend.ask`` value for turns from that row's
  ``updated_at`` onwards. Every save of the setting moves ``updated_at``
  (``backends.set_backend``), so the stored value has held since then. A value
  ``resolve_backend`` does not recognise falls back to an environment default
  the database cannot see, so it proves nothing.
* null otherwise: between the release and the last save, or with no stored row,
  the environment default decided, and it is not recorded anywhere.

``downgrade`` drops the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBSCRIPTION_RELEASE = "2026-08-20 14:13:00+00"
_INTEGER_COLUMNS = ("fresh_input_tokens", "cache_read_tokens", "cache_write_tokens", "model_calls")


def upgrade() -> None:
    op.add_column("ask_turns", sa.Column("backend", sa.String(length=16), nullable=True))
    for name in _INTEGER_COLUMNS:
        op.add_column("ask_turns", sa.Column(name, sa.Integer(), nullable=True))
    # ONE statement per op.execute: Alembic runs over asyncpg, which prepares
    # each statement and refuses several in one string.
    op.execute(f"UPDATE ask_turns SET backend = 'api' WHERE created_at < '{_SUBSCRIPTION_RELEASE}'")
    op.execute(
        "UPDATE ask_turns SET backend = s.value #>> '{}' "
        "FROM instance_settings s "
        "WHERE s.key = 'llm_backend.ask' "
        "AND s.value #>> '{}' IN ('api', 'subscription') "
        "AND ask_turns.created_at >= s.updated_at"
    )


def downgrade() -> None:
    for name in reversed(_INTEGER_COLUMNS):
        op.drop_column("ask_turns", name)
    op.drop_column("ask_turns", "backend")
