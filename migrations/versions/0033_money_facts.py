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
         (a.document_date = b.document_date) AS same_day
  FROM documents a JOIN documents b
    ON a.id < b.id
   AND a.sender_id = b.sender_id
   AND a.currency IS NOT DISTINCT FROM b.currency
   AND a.amount_total = b.amount_total
  WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL
    AND a.amount_total IS NOT NULL AND a.sender_id IS NOT NULL
),
-- R3 pairs only MUTUAL NEAREST complementary partners, and "nearest" here is
-- DIRECTIONAL: a payment follows the thing it pays. A recurring same-amount
-- charge documented as invoice-then-receipt puts every cycle's receipt inside
-- 60 days of the NEXT cycle's invoice as well as its own, so an R3 that fires
-- on every complementary pair in the window chains cycle to cycle and the
-- recursive closure below collapses a whole subscription history into one
-- payment. Ranking those candidates by UNSIGNED gap does not separate them:
-- on a 1st/16th cadence both gaps are 15 days, the tie readmits the
-- cross-cycle edge, and twelve cycles come back as nine payments; and in a
-- short February the next cycle's invoice is 13 days from the receipt against
-- its own invoice's 15, so the wrong pair wins outright.
--
-- `sym` is therefore keyed (due, made) rather than being a symmetric
-- self-join, so direction is expressible at all. It ranks a receipt dated on
-- or after its invoice by the days between the two, and a receipt dated
-- before it by 1000 + that distance. The offset exceeds the 60-day window, so
-- EVERY forward candidate outranks EVERY backward one and a backward match is
-- used only where no forward one exists — which is what still merges a
-- prepayment. `best_due`/`best_made` take each document's minimum rank and
-- `mutual` keeps the pairs where the two agree.
--
-- `sym` also excludes VETO'd pairs. A document whose reference contradicts a
-- neighbour's can never merge with it, so letting it hold that neighbour's
-- nearest slot would do nothing but suppress a legitimate merge.
sym AS (
  SELECT d.id AS due, m.id AS made,
         CASE WHEN m.document_date >= d.document_date
              THEN m.document_date - d.document_date
              ELSE 1000 + (d.document_date - m.document_date) END AS rank
  FROM documents d JOIN documents m
    ON d.sender_id = m.sender_id
   AND d.currency IS NOT DISTINCT FROM m.currency AND d.amount_total = m.amount_total
   AND d.amount_kind = 'payment_due' AND m.amount_kind = 'payment_made'
  WHERE d.deleted_at IS NULL AND m.deleted_at IS NULL
    AND d.amount_total IS NOT NULL AND d.sender_id IS NOT NULL
    AND abs(m.document_date - d.document_date) <= 60
    AND NOT (d.reference IS NOT NULL AND m.reference IS NOT NULL AND d.reference <> m.reference)
),
best_due  AS (SELECT due,  min(rank) AS g FROM sym GROUP BY due),
best_made AS (SELECT made, min(rank) AS g FROM sym GROUP BY made),
mutual AS (
  SELECT least(s.due, s.made) AS a, greatest(s.due, s.made) AS b
  FROM sym s
  JOIN best_due  bd ON bd.due  = s.due  AND bd.g = s.rank
  JOIN best_made bm ON bm.made = s.made AND bm.g = s.rank
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
