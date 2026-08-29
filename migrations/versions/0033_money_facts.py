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
),
-- R3 pairs only MUTUAL NEAREST complementary partners. A monthly charge
-- documented as invoice-then-receipt puts every cycle's receipt within 60
-- days of the NEXT cycle's invoice (a receipt on the 3rd is 29 days from the
-- 1st), so an R3 that fires on every complementary pair inside the window
-- chains cycle to cycle and the recursive closure below collapses a whole
-- subscription history into one payment. `sym` is the symmetric set of
-- candidate complementary pairs, `best` each document's smallest gap to any
-- of them, and `mutual` the pairs where each document is the other's nearest
-- — the receipt on the 3rd pairs with its own cycle's invoice (2 days), never
-- the next cycle's (29 days).
sym AS (
  SELECT a.id AS x, b.id AS y, abs(a.document_date - b.document_date) AS gap
  FROM documents a JOIN documents b
    ON a.id <> b.id AND a.sender_id = b.sender_id
   AND a.currency IS NOT DISTINCT FROM b.currency AND a.amount_total = b.amount_total
  WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL
    AND abs(a.document_date - b.document_date) <= 60
    AND ((a.amount_kind='payment_due' AND b.amount_kind='payment_made')
      OR (a.amount_kind='payment_made' AND b.amount_kind='payment_due'))
), best AS (SELECT x, min(gap) AS g FROM sym GROUP BY x),
mutual AS (
  SELECT least(s.x,s.y) AS a, greatest(s.x,s.y) AS b
  FROM sym s JOIN best bx ON bx.x=s.x AND bx.g=s.gap JOIN best by ON by.x=s.y AND by.g=s.gap
  WHERE s.x < s.y
), ruled AS (
  SELECT p.a, p.b, CASE
    WHEN p.ra IS NOT NULL AND p.rb IS NOT NULL AND p.ra <> p.rb THEN NULL
    WHEN p.ra IS NOT NULL AND p.ra = p.rb                       THEN 'R2'
    WHEN p.same_day                                             THEN 'R1'
    WHEN m.a IS NOT NULL                                        THEN 'R3'
    ELSE NULL END AS rule
  FROM pairs p LEFT JOIN mutual m ON m.a=p.a AND m.b=p.b)
SELECT a, b, rule FROM ruled
WHERE rule IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM payment_overrides o
    WHERE o.kind='SPLIT' AND o.doc_a=ruled.a AND o.doc_b=ruled.b)
UNION
-- The LATEST correction on a pair wins. The rule-derived arm above is
-- suppressed by any SPLIT at all; this arm re-adds the edge explicitly when
-- the MERGE is the more recent of the two, which is how a MERGE undoes a
-- SPLIT. Without the NOT EXISTS here the guard applied to the rule arm only,
-- so a SPLIT recorded AFTER a MERGE changed nothing: the override edge
-- survived, the pair stayed merged, and "Not the same payment" answered 200
-- while doing nothing at all. A tie (identical timestamps) falls to the
-- SPLIT: not merging is the safe direction.
SELECT o.doc_a, o.doc_b, 'OVERRIDE'
FROM payment_overrides o
JOIN documents da ON da.id = o.doc_a
JOIN documents db ON db.id = o.doc_b
WHERE o.kind='MERGE' AND da.deleted_at IS NULL AND db.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM payment_overrides s
                  WHERE s.kind='SPLIT' AND s.doc_a=o.doc_a AND s.doc_b=o.doc_b
                    AND s.created_at >= o.created_at)
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
