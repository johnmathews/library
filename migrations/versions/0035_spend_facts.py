"""spend lines, line labels, and the spend_facts relation

Revision ID: 0035
Revises: 0034

Tables, the sum-invariant triggers, and the `spend_facts` view — the one
relation every chart query reads (spec §5.1).

`sum(lines.amount) = documents.amount_total` is enforced from BOTH sides, by
one function bound to two constraint triggers. Enforcing it only on
`spend_lines` leaves it half-kept: `documents.amount_total` is writable from
three live paths (`PATCH /api/documents/{id}`, re-extraction, the importer), so
allocating 100 across 60/40 and then correcting the document total to 120 would
succeed with the lines still summing to 100 and every chart total for that
document quietly wrong.

Both triggers are DEFERRABLE INITIALLY DEFERRED: a two-line split inserts as
one transaction, and an immediate check would fail on the first row, because
that row alone never equals the document total.
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


# One function, two tables. `NEW.document_id` does not exist on `documents`,
# and `NEW.id` means different things on the two tables, so the document id is
# resolved by branching on TG_TABLE_NAME. On DELETE, `NEW` is NULL and the
# COALESCE falls through to OLD (verified by execution against Postgres 17;
# a NULL record's field reads as NULL rather than raising).
#
# The escape hatch tests for the ABSENCE OF LINES, not for a zero sum. A fully
# cleared allocation must be legal — deleting every line, and cascading the
# lines away with their document, must not fire the check — but `line_total = 0`
# is not the same predicate: a document legitimately allocated across lines
# summing to zero (`0.00` split `[0.00, 0.00]`, or `[50.00, -50.00]`) would then
# let its `amount_total` be corrected to anything at all, and it would contribute
# 0 to every chart while `spend_facts` emitted its line rows instead of the
# synthetic one. That is the exact silent-wrong-total this trigger exists to
# prevent, arriving through the guard's own door. `EXISTS` says what is meant.
_SUM_FUNCTION = """
CREATE FUNCTION spend_lines_sum_matches() RETURNS trigger AS $$
DECLARE
  doc_total numeric(14,2);
  line_total numeric(14,2);
  doc_id bigint;
BEGIN
  IF TG_TABLE_NAME = 'documents' THEN
    doc_id := COALESCE(NEW.id, OLD.id);
  ELSE
    doc_id := COALESCE(NEW.document_id, OLD.document_id);
  END IF;
  SELECT amount_total INTO doc_total FROM documents WHERE id = doc_id;
  SELECT COALESCE(sum(amount), 0) INTO line_total
    FROM spend_lines WHERE document_id = doc_id;
  IF EXISTS (SELECT 1 FROM spend_lines WHERE document_id = doc_id)
     AND line_total IS DISTINCT FROM doc_total THEN
    RAISE EXCEPTION
      'spend lines for document % sum to % but the document total is %',
      doc_id, line_total, doc_total;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

_LINES_TRIGGER = """
CREATE CONSTRAINT TRIGGER spend_lines_sum_matches_trigger
AFTER INSERT OR UPDATE OR DELETE ON spend_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION spend_lines_sum_matches()
"""

_DOCUMENTS_TRIGGER = """
CREATE CONSTRAINT TRIGGER documents_total_matches_lines_trigger
AFTER UPDATE OF amount_total ON documents
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION spend_lines_sum_matches()
"""


# `spend_facts` is the one relation every chart reads (spec §5.1). It unions
# unsplit documents — synthesising a row from `amount_total` — with the lines of
# split documents, so no COALESCE branch is scattered through query code and
# label inheritance has exactly one place to be tested.
#
# ONE statement, so one op.execute (see the note above the trigger calls).
_SPEND_FACTS = """
CREATE VIEW spend_facts AS
WITH doc_labels AS (
  SELECT dl.document_id, jsonb_object_agg(f.key, fv.key) AS labels
  FROM document_labels dl
  JOIN facets f ON f.id = dl.facet_id
  JOIN facet_values fv ON fv.id = dl.facet_value_id
  GROUP BY dl.document_id
),
line_lbls AS (
  SELECT ll.line_id, jsonb_object_agg(f.key, fv.key) AS labels
  FROM line_labels ll
  JOIN facets f ON f.id = ll.facet_id
  JOIN facet_values fv ON fv.id = ll.facet_value_id
  GROUP BY ll.line_id
),
-- The join to `payments` is what supplies the payment grouping, and it also
-- excludes deleted documents: `payments` builds its reachability from
-- `documents WHERE deleted_at IS NULL`, so a deleted document has no row there
-- at all. The `deleted_at IS NULL` filter below is defence in depth — with the
-- join present, removing it changes no result (proved by mutation). Neither is
-- free to remove on its own account: with the join weakened to a LEFT JOIN the
-- filter is the only thing keeping a deleted twin out of the ranking, and with
-- the filter removed the join is. Removing BOTH readmits a deleted document,
-- which the deleted-twin test catches red.
eligible AS (
  SELECT d.id, d.sender_id, d.document_date, d.amount_total, d.currency,
         d.amount_kind, d.reference, p.payment_id,
         EXISTS (SELECT 1 FROM spend_lines sl WHERE sl.document_id = d.id) AS has_lines
  FROM documents d
  JOIN payments p ON p.document_id = d.id
  WHERE d.deleted_at IS NULL AND d.amount_total IS NOT NULL
),
-- Exactly one document per payment contributes its money, or the merge would
-- not have removed the double count. A line-bearing document wins first, or
-- merging an itemised invoice with its receipt would discard the split.
--
-- COALESCE(..., false) is load-bearing: `amount_kind = 'payment_made'` is
-- NULL for an undecided document and Postgres sorts NULLs FIRST under DESC,
-- so without it an undecided document becomes canonical and the payment is
-- represented by a kind that is never summed. Confirmed by mutation.
ranked AS (
  SELECT e.*, row_number() OVER (
           PARTITION BY e.payment_id
           ORDER BY e.has_lines DESC,
                    COALESCE(e.amount_kind = 'payment_made', false) DESC,
                    e.id ASC
         ) = 1 AS is_canonical
  FROM eligible e
)
SELECT r.id AS document_id, NULL::bigint AS line_id, r.payment_id, r.is_canonical,
       r.sender_id, r.document_date AS date, r.amount_total AS amount, r.currency,
       r.amount_kind, r.reference,
       COALESCE(dl.labels, '{}'::jsonb) AS labels
FROM ranked r
LEFT JOIN doc_labels dl ON dl.document_id = r.id
WHERE NOT r.has_lines
UNION ALL
-- `||` on jsonb takes the RIGHT operand on a key collision, which is exactly
-- the inheritance rule: a line overrides the facets it names and inherits
-- the rest from its document.
SELECT r.id, sl.id, r.payment_id, r.is_canonical,
       r.sender_id, r.document_date, sl.amount, r.currency,
       r.amount_kind, r.reference,
       COALESCE(dl.labels, '{}'::jsonb) || COALESCE(ll.labels, '{}'::jsonb)
FROM ranked r
JOIN spend_lines sl ON sl.document_id = r.id
LEFT JOIN doc_labels dl ON dl.document_id = r.id
LEFT JOIN line_lbls ll ON ll.line_id = sl.id
WHERE r.has_lines
"""


def upgrade() -> None:
    op.create_table(
        "spend_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False, server_default="manual"),
        # name is convention-relative, not the literal database name: the "ck"
        # naming convention already prefixes it to `ck_spend_lines_origin`.
        # An already-prefixed name here would be prefixed twice.
        sa.CheckConstraint("origin IN ('extracted','manual')", name="origin"),
    )
    op.create_index("ix_spend_lines_document", "spend_lines", ["document_id"])
    op.create_table(
        "line_labels",
        sa.Column(
            "line_id",
            sa.BigInteger(),
            sa.ForeignKey("spend_lines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("facet_value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="line_labels_value_facet",
        ),
    )
    # One statement per op.execute. Alembic runs over **asyncpg** here
    # (migrations/env.py), which prepares every statement, so a multi-statement
    # string fails with "cannot insert multiple commands into a prepared
    # statement" — observed, not assumed. The `$$`-quoted body is itself one
    # statement, so it survives the split intact.
    op.execute(_SUM_FUNCTION)
    op.execute(_LINES_TRIGGER)
    op.execute(_DOCUMENTS_TRIGGER)
    op.execute(_SPEND_FACTS)


def downgrade() -> None:
    # First: `spend_facts` depends on `spend_lines`, `line_labels` and the
    # `payments` view, so it has to come down before any of them.
    op.execute("DROP VIEW IF EXISTS spend_facts")
    op.execute("DROP TRIGGER IF EXISTS documents_total_matches_lines_trigger ON documents")
    op.execute("DROP TRIGGER IF EXISTS spend_lines_sum_matches_trigger ON spend_lines")
    op.execute("DROP FUNCTION IF EXISTS spend_lines_sum_matches()")
    op.drop_table("line_labels")
    op.drop_index("ix_spend_lines_document", table_name="spend_lines")
    op.drop_table("spend_lines")
