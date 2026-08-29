"""spend lines, line labels, and the spend_facts relation

Revision ID: 0035
Revises: 0034

Tables and the sum-invariant triggers. The `spend_facts` view is added to this
same revision by the next step of the chart engine.

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
        sa.CheckConstraint("origin IN ('extracted','manual')", name="ck_spend_lines_origin"),
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


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS documents_total_matches_lines_trigger ON documents")
    op.execute("DROP TRIGGER IF EXISTS spend_lines_sum_matches_trigger ON spend_lines")
    op.execute("DROP FUNCTION IF EXISTS spend_lines_sum_matches()")
    op.drop_table("line_labels")
    op.drop_index("ix_spend_lines_document", table_name="spend_lines")
    op.drop_table("spend_lines")
