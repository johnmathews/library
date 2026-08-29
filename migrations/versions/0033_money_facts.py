"""money facts: amount semantics, reference numbers, payment overrides

``amount_total`` alone says nothing about what a number MEANS. The live archive
carries insurance coverage ceilings, nil-return confirmations and quotes in the
same column as real payments, and summing them together is how a coverage
ceiling was once charted as spending.

``amount_kind`` declares the meaning; only the payment kinds are ever summed.
It is NULLABLE on purpose: NULL is "not yet decided", which is not the same as
"carries no money", and only the former belongs in a backfill queue. Consumers
treat NULL as not summable, so an un-backfilled archive under-reports rather
than over-reports.

``reference`` is the document's own invoice/order/booking number. It is the
strongest evidence that two documents describe one payment, and the only such
evidence that works across an arbitrary gap between an invoice's date and its
receipt's.

Revision ID: 0033
Revises: 0032

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_KINDS = (
    "payment_due",
    "payment_made",
    "assessment",
    "coverage_limit",
    "balance",
    "estimate",
    "none",
)


def upgrade() -> None:
    amount_kind = sa.Enum(*_AMOUNT_KINDS, name="amount_kind", native_enum=False, length=16)
    op.add_column("documents", sa.Column("amount_kind", amount_kind, nullable=True))
    op.add_column("documents", sa.Column("reference", sa.String(128), nullable=True))
    op.create_index("ix_documents_reference", "documents", ["sender_id", "reference"])
    op.create_table(
        "payment_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "doc_a",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_b",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('MERGE','SPLIT')", name="payment_overrides_kind"),
        sa.CheckConstraint("doc_a < doc_b", name="payment_overrides_ordered"),
        sa.UniqueConstraint("kind", "doc_a", "doc_b", name="payment_overrides_unique"),
    )
    op.execute("""
CREATE VIEW payment_edges AS
WITH pairs AS (
  SELECT a.id AS a, b.id AS b, a.reference ra, b.reference rb,
         a.amount_kind ka, b.amount_kind kb,
         (a.document_date = b.document_date) AS same_day,
         abs(a.document_date - b.document_date) AS gap
  FROM documents a JOIN documents b
    ON a.id < b.id
   AND a.sender_id = b.sender_id
   AND a.currency IS NOT DISTINCT FROM b.currency
   AND a.amount_total = b.amount_total
  WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL
    AND a.amount_total IS NOT NULL AND a.sender_id IS NOT NULL
), ruled AS (
  SELECT a, b, CASE
    WHEN ra IS NOT NULL AND rb IS NOT NULL AND ra <> rb THEN NULL
    WHEN ra IS NOT NULL AND ra = rb                     THEN 'R2'
    WHEN same_day                                       THEN 'R1'
    WHEN gap <= 60 AND ((ka='payment_due' AND kb='payment_made')
                     OR (ka='payment_made' AND kb='payment_due')) THEN 'R3'
    ELSE NULL END AS rule
  FROM pairs)
SELECT a, b, rule FROM ruled
WHERE rule IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM payment_overrides o
    WHERE o.kind='SPLIT' AND o.doc_a=ruled.a AND o.doc_b=ruled.b)
UNION
SELECT o.doc_a, o.doc_b, 'OVERRIDE'
FROM payment_overrides o
JOIN documents da ON da.id = o.doc_a
JOIN documents db ON db.id = o.doc_b
WHERE o.kind='MERGE' AND da.deleted_at IS NULL AND db.deleted_at IS NULL
""")
    op.execute("""
CREATE VIEW payments AS
WITH RECURSIVE bidir AS (
  SELECT a, b FROM payment_edges UNION SELECT b, a FROM payment_edges),
reach(doc, member) AS (
  SELECT id, id FROM documents WHERE deleted_at IS NULL
  UNION
  SELECT r.doc, e.b FROM reach r JOIN bidir e ON e.a = r.member)
SELECT doc AS document_id, min(member) AS payment_id FROM reach GROUP BY doc
""")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS payments")
    op.execute("DROP VIEW IF EXISTS payment_edges")
    op.drop_table("payment_overrides")
    op.drop_index("ix_documents_reference", table_name="documents")
    op.drop_column("documents", "reference")
    op.drop_column("documents", "amount_kind")
    sa.Enum(name="amount_kind").drop(op.get_bind(), checkfirst=True)
