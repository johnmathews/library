"""spend_facts: expose the twin order as `twin_rank`

Revision ID: 0042
Revises: 0041

The view ranks the documents of each payment (line-bearing first, then
``payment_made``, then the lowest id) and marks the first ``is_canonical``.
``structured_query._payment_choices`` ranked a filtered subset of the same
documents with the same three keys, written a second time in SQLAlchemy. A
later edit to one copy and not the other would make chart totals and Ask
totals disagree without any error.

This migration exposes the view's rank as a last column, ``twin_rank``: one
value per document per payment, the same on every line row of that document,
and ``twin_rank = 1`` exactly where ``is_canonical``. ``_payment_choices`` now
orders by it, and its own copy of the order is deleted. Ordering a subset by
an archive-wide total order gives the same first element as re-ranking the
subset by the same keys, so no total changes. The one key that could differ is
``has_lines``, which ``_payment_choices`` computed over the filtered rows: a
document's rows are either its one unsplit row or only its line rows, never
both, so any document with a filtered row has the same ``has_lines`` there as
in the view.

Every existing column keeps its name, type, position and meaning.

``downgrade`` recreates 0041's view. Its SQL is copied below rather than
imported: a migration must not change when a later one is edited.
``test_0042_adds_twin_rank_and_its_downgrade_restores_the_exact_0041_view``
diffs the result against a database migrated straight to 0041.

Create Date: 2026-11-06 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ONE statement per op.execute: Alembic runs over asyncpg, which prepares each
# statement (see 0035).
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
-- Unchanged from 0035: the join to `payments` groups and excludes deleted
-- documents, and `deleted_at IS NULL` is the defence in depth beside it.
eligible AS (
  SELECT d.id, d.sender_id, d.document_date, d.amount_total, d.currency,
         d.amount_kind, d.reference, p.payment_id,
         EXISTS (SELECT 1 FROM spend_lines sl WHERE sl.document_id = d.id) AS has_lines
  FROM documents d
  JOIN payments p ON p.document_id = d.id
  WHERE d.deleted_at IS NULL AND d.amount_total IS NOT NULL
),
-- The one copy of the twin order: line-bearing first, then `payment_made`,
-- then the lowest id. Kept as the rank itself rather than only `= 1`, because
-- the label merge below orders twins by it, and exposed as `twin_rank` so
-- `structured_query._payment_choices` ranks a filtered subset by it instead of
-- restating the order. `e.id` is the last key, so no two documents of a
-- payment share a rank.
--
-- COALESCE(..., false) is load-bearing: `amount_kind = 'payment_made'` is
-- NULL for an undecided document and Postgres sorts NULLs FIRST under DESC.
ranked AS (
  SELECT e.*, row_number() OVER (
           PARTITION BY e.payment_id
           ORDER BY e.has_lines DESC,
                    COALESCE(e.amount_kind = 'payment_made', false) DESC,
                    e.id ASC
         ) AS rank
  FROM eligible e
),
-- One row per (document, facet) for every ranked document's DOCUMENT-level
-- labels. Line labels are left out on purpose: a twin labels the payment, and
-- one line of an itemised invoice is not the payment.
--
-- Read from the label tables, not by unpacking `doc_labels` with
-- `jsonb_each`: the planner estimates 100 rows per set-returning call, and on a
-- synthetic 4,000-document archive that estimate alone lifted every
-- `sum_amount` statement over `jit_above_cost`, adding ~0.3 s of JIT
-- compilation to each (measured: 1.3 s became 2.2 s per total).
group_labels AS (
  SELECT r.payment_id, r.rank, f.key AS facet, fv.key AS value
  FROM ranked r
  JOIN document_labels dl ON dl.document_id = r.id
  JOIN facets f ON f.id = dl.facet_id
  JOIN facet_values fv ON fv.id = dl.facet_value_id
),
-- What the canonical row inherits: on each facet, the best-ranked twin's
-- value. The canonical document's own labels are applied over this below, so
-- it wins wherever it has a value.
inherited AS (
  SELECT payment_id, jsonb_object_agg(facet, value) AS labels
  FROM (
    SELECT DISTINCT ON (payment_id, facet) payment_id, facet, value
    FROM group_labels
    WHERE rank > 1
    ORDER BY payment_id, facet, rank
  ) best
  GROUP BY payment_id
),
-- A payment whose documents give one facet two different values. Counted
-- across all of its documents, canonical included, so it covers both "the
-- receipt says supplies, the bill says services" and two twins disagreeing on
-- a facet the canonical document leaves blank.
conflicted AS (
  SELECT DISTINCT payment_id
  FROM group_labels
  GROUP BY payment_id, facet
  HAVING count(DISTINCT value) > 1
),
twins AS (
  SELECT r.payment_id, array_agg(dl.labels ORDER BY r.rank) AS labels
  FROM ranked r
  JOIN doc_labels dl ON dl.document_id = r.id
  WHERE r.rank > 1
  GROUP BY r.payment_id
),
-- The three joins only ever feed the canonical row: a non-canonical row keeps
-- its own labels, a false flag and no twin labels.
merged AS (
  SELECT r.*, r.rank = 1 AS is_canonical,
         CASE WHEN r.rank = 1 THEN COALESCE(i.labels, '{}'::jsonb)
              ELSE '{}'::jsonb END AS inherited,
         (r.rank = 1 AND c.payment_id IS NOT NULL) AS label_conflict,
         CASE WHEN r.rank = 1 THEN COALESCE(t.labels, '{}'::jsonb[])
              ELSE '{}'::jsonb[] END AS twin_labels
  FROM ranked r
  LEFT JOIN inherited i ON i.payment_id = r.payment_id
  LEFT JOIN conflicted c ON c.payment_id = r.payment_id
  LEFT JOIN twins t ON t.payment_id = r.payment_id
)
-- `||` on jsonb takes the RIGHT operand on a key collision, so each layer
-- overrides the one before it: inherited, then the document's own, then (in
-- the second arm) the line's own.
SELECT m.id AS document_id, NULL::bigint AS line_id, m.payment_id, m.is_canonical,
       m.sender_id, m.document_date AS date, m.amount_total AS amount, m.currency,
       m.amount_kind, m.reference,
       m.inherited || COALESCE(dl.labels, '{}'::jsonb) AS labels,
       m.label_conflict, m.twin_labels, m.rank AS twin_rank
FROM merged m
LEFT JOIN doc_labels dl ON dl.document_id = m.id
WHERE NOT m.has_lines
UNION ALL
SELECT m.id, sl.id, m.payment_id, m.is_canonical,
       m.sender_id, m.document_date, sl.amount, m.currency,
       m.amount_kind, m.reference,
       m.inherited || COALESCE(dl.labels, '{}'::jsonb) || COALESCE(ll.labels, '{}'::jsonb),
       m.label_conflict, m.twin_labels, m.rank
FROM merged m
JOIN spend_lines sl ON sl.document_id = m.id
LEFT JOIN doc_labels dl ON dl.document_id = m.id
LEFT JOIN line_lbls ll ON ll.line_id = sl.id
WHERE m.has_lines
"""

# 0041's view, copied verbatim (comments included) for the downgrade.
_SPEND_FACTS_0041 = """
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
-- Unchanged from 0035: the join to `payments` groups and excludes deleted
-- documents, and `deleted_at IS NULL` is the defence in depth beside it.
eligible AS (
  SELECT d.id, d.sender_id, d.document_date, d.amount_total, d.currency,
         d.amount_kind, d.reference, p.payment_id,
         EXISTS (SELECT 1 FROM spend_lines sl WHERE sl.document_id = d.id) AS has_lines
  FROM documents d
  JOIN payments p ON p.document_id = d.id
  WHERE d.deleted_at IS NULL AND d.amount_total IS NOT NULL
),
-- 0035's ranking, kept as the rank itself rather than only `= 1`, because the
-- label merge below orders twins by it. `e.id` is the last key, so no two
-- documents of a payment share a rank.
ranked AS (
  SELECT e.*, row_number() OVER (
           PARTITION BY e.payment_id
           ORDER BY e.has_lines DESC,
                    COALESCE(e.amount_kind = 'payment_made', false) DESC,
                    e.id ASC
         ) AS rank
  FROM eligible e
),
-- One row per (document, facet) for every ranked document's DOCUMENT-level
-- labels. Line labels are left out on purpose: a twin labels the payment, and
-- one line of an itemised invoice is not the payment.
--
-- Read from the label tables, not by unpacking `doc_labels` with
-- `jsonb_each`: the planner estimates 100 rows per set-returning call, and on a
-- synthetic 4,000-document archive that estimate alone lifted every
-- `sum_amount` statement over `jit_above_cost`, adding ~0.3 s of JIT
-- compilation to each (measured: 1.3 s became 2.2 s per total).
group_labels AS (
  SELECT r.payment_id, r.rank, f.key AS facet, fv.key AS value
  FROM ranked r
  JOIN document_labels dl ON dl.document_id = r.id
  JOIN facets f ON f.id = dl.facet_id
  JOIN facet_values fv ON fv.id = dl.facet_value_id
),
-- What the canonical row inherits: on each facet, the best-ranked twin's
-- value. The canonical document's own labels are applied over this below, so
-- it wins wherever it has a value.
inherited AS (
  SELECT payment_id, jsonb_object_agg(facet, value) AS labels
  FROM (
    SELECT DISTINCT ON (payment_id, facet) payment_id, facet, value
    FROM group_labels
    WHERE rank > 1
    ORDER BY payment_id, facet, rank
  ) best
  GROUP BY payment_id
),
-- A payment whose documents give one facet two different values. Counted
-- across all of its documents, canonical included, so it covers both "the
-- receipt says supplies, the bill says services" and two twins disagreeing on
-- a facet the canonical document leaves blank.
conflicted AS (
  SELECT DISTINCT payment_id
  FROM group_labels
  GROUP BY payment_id, facet
  HAVING count(DISTINCT value) > 1
),
twins AS (
  SELECT r.payment_id, array_agg(dl.labels ORDER BY r.rank) AS labels
  FROM ranked r
  JOIN doc_labels dl ON dl.document_id = r.id
  WHERE r.rank > 1
  GROUP BY r.payment_id
),
-- The three joins only ever feed the canonical row: a non-canonical row keeps
-- its own labels, a false flag and no twin labels.
merged AS (
  SELECT r.*, r.rank = 1 AS is_canonical,
         CASE WHEN r.rank = 1 THEN COALESCE(i.labels, '{}'::jsonb)
              ELSE '{}'::jsonb END AS inherited,
         (r.rank = 1 AND c.payment_id IS NOT NULL) AS label_conflict,
         CASE WHEN r.rank = 1 THEN COALESCE(t.labels, '{}'::jsonb[])
              ELSE '{}'::jsonb[] END AS twin_labels
  FROM ranked r
  LEFT JOIN inherited i ON i.payment_id = r.payment_id
  LEFT JOIN conflicted c ON c.payment_id = r.payment_id
  LEFT JOIN twins t ON t.payment_id = r.payment_id
)
-- `||` on jsonb takes the RIGHT operand on a key collision, so each layer
-- overrides the one before it: inherited, then the document's own, then (in
-- the second arm) the line's own.
SELECT m.id AS document_id, NULL::bigint AS line_id, m.payment_id, m.is_canonical,
       m.sender_id, m.document_date AS date, m.amount_total AS amount, m.currency,
       m.amount_kind, m.reference,
       m.inherited || COALESCE(dl.labels, '{}'::jsonb) AS labels,
       m.label_conflict, m.twin_labels
FROM merged m
LEFT JOIN doc_labels dl ON dl.document_id = m.id
WHERE NOT m.has_lines
UNION ALL
SELECT m.id, sl.id, m.payment_id, m.is_canonical,
       m.sender_id, m.document_date, sl.amount, m.currency,
       m.amount_kind, m.reference,
       m.inherited || COALESCE(dl.labels, '{}'::jsonb) || COALESCE(ll.labels, '{}'::jsonb),
       m.label_conflict, m.twin_labels
FROM merged m
JOIN spend_lines sl ON sl.document_id = m.id
LEFT JOIN doc_labels dl ON dl.document_id = m.id
LEFT JOIN line_lbls ll ON ll.line_id = sl.id
WHERE m.has_lines
"""


def upgrade() -> None:
    # A plain DROP, not CASCADE: nothing in the schema depends on the view, and
    # if something ever does, failing here is better than dropping it silently.
    op.execute("DROP VIEW spend_facts")
    op.execute(_SPEND_FACTS)


def downgrade() -> None:
    op.execute("DROP VIEW spend_facts")
    op.execute(_SPEND_FACTS_0041)
