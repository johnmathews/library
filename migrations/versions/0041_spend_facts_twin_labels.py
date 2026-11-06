"""spend_facts: a payment's counted row carries its twins' labels

Revision ID: 0041
Revises: 0040

A payment documented twice (a bill and its receipt) is counted once, on its
canonical document. Until now that row carried only its own document's labels,
so a bill labelled ``services`` merged with an unlabelled receipt put the
payment in no ``services`` chart, and ``/api/facets/counts`` never offered the
value (docs/charts.md §2.2).

The canonical row now inherits, on each facet it has no value for, the value
its twins carry. Its own value wins on a facet both carry; among twins that
disagree, the best-ranked twin wins, by the view's own ranking. Only
document-level labels travel: a receipt labels the payment, not one line of the
invoice it pays, so a twin's line labels stay on that twin.

Two columns are added at the end:

- ``label_conflict`` — on the canonical row, true when two documents of the
  payment carry different document-level values for one facet. The canonical
  row keeps its own value, so the payment is missing from the chart for the
  other value; the chart footer reports it there (``charts/footer.py``).
- ``twin_labels`` — on the canonical row, each twin's document-level labels in
  ranking order, so the footer can ask "would this twin's label have matched?"
  without reading the view a second time.

Non-canonical rows keep their own labels, ``label_conflict`` false and an empty
``twin_labels``. Every existing column keeps its name, type, position and
meaning.

``downgrade`` recreates 0035's view. Its SQL is copied below rather than
imported: a migration must not change when a later one is edited.
``test_0041_downgrade_restores_the_exact_0040_view`` diffs the result against a
database migrated straight to 0040.

Create Date: 2026-11-06 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
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

# 0035's view, copied verbatim (comments included) for the downgrade.
_SPEND_FACTS_0035 = """
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
    # A plain DROP, not CASCADE: nothing in the schema depends on the view, and
    # if something ever does, failing here is better than dropping it silently.
    op.execute("DROP VIEW spend_facts")
    op.execute(_SPEND_FACTS)


def downgrade() -> None:
    op.execute("DROP VIEW spend_facts")
    op.execute(_SPEND_FACTS_0035)
