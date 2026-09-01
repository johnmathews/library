"""chunk context header

Adds ``document_chunks.context_header``: the ``sender · date · kind · title``
line prepended to a chunk's text before it is embedded, so a chunk retrieves on
its document's identity as well as its own words (Plan B, finding #6).

Stored in its own column rather than baked into ``text`` because ``text`` is
also what Ask reads back as an excerpt. With ``retrieve_chunks_per_doc = 3`` and
``retrieve_top_k = 10``, a baked-in header would repeat the same metadata up to
thirty times in a single tool result, duplicating fields the result rows already
carry as structured values.

Nullable, with no backfill: existing chunks keep the vectors they were embedded
with, which do NOT include a header. Re-embedding is an operator action
(``library backfill-embeddings --include-existing``), deliberately not a
migration — it calls a network sidecar once per document and would make this
migration unbounded in time and able to fail for reasons unrelated to schema.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("context_header", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "context_header")
