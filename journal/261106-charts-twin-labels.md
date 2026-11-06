# A payment's counted row carries its twins' labels

> Purpose
>
> Records why charts could drop a payment that Ask counted, the rule
> migration 0041 puts into `spend_facts`, the "labelled differently"
> footer bucket, and how each was proven. Work unit W5 of the MCP
> follow-ups run (findings F4, F5, F6 and F17; decisions D1 and D3).

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-charts`

## 1. The defect

A payment documented twice (a bill and its receipt) is counted once, on its
canonical document. Charts, the footer and `/facets/counts` read only that
row's own labels, but labels are set on each document separately. That
caused three failures:

- **Missing from the chart (F4).** A bill labelled `services` merged with
  an unlabelled receipt was left out of the `services` chart, and the
  footer filed it as "uncategorised".
- **Missing from the footer too (F5).** When the two documents carried
  different values, the payment fell out of the chart and out of the footer
  without trace.
- **Missing from the counts (F17).** A value carried only by a bill merged
  with a receipt was never offered as a chart proposal.

Ask's `sum_amount` counted all of these, so the two surfaces disagreed.

## 2. The decisions

- **D1 (plan gate): merge twin labels in the view.** The alternative was
  choosing the counted twin within each chart's filter, as `sum_amount`
  does. That would fix F5 too, but one payment could then appear in two
  series, and stacks and in + not_in would no longer add up to all
  spending.
- **D3 (asked mid-unit): one view for totals as well as charts.**
  `sum_amount` reads the same view, so the merge changes what it sees too.
  The answer was to let it read the merged labels, which makes Ask, MCP and
  charts agree. A read-only count on production found 21 payments with more
  than one document, 2 F4 cases (both in the same month with the same
  amount) and 5 F5 conflicts. No current total moved.

## 3. The rule (migration 0041)

- **Inheritance.** On each facet the canonical document has no value for,
  its rows take the value its twins carry. Its own value wins on a facet
  both carry. Among twins that disagree, the best-ranked twin wins, using
  the view's existing ranking.
- **Only document-level labels travel.** A receipt labels the payment, not
  one line of the invoice. On a line row the layers are twins, then the
  document, then the line, so a line override still wins.
- **`label_conflict`.** True on the canonical row when two documents of the
  payment give one facet different document-level values.
- **`twin_labels`.** Each twin's document labels, in rank order, also on the
  canonical row.
- **The footer bucket.** A conflicting row that fails the rule, but would
  pass it with a twin's label, goes into a new needs-attention bucket,
  "Labelled differently on a matching document". It is drillable like the
  others. The footer evaluates this over the row's own `twin_labels`, so the
  view is not read a second time.
- **Totals.** `sum_amount` still counts a conflicting payment under both
  values, because it chooses within its filter. This is why chart + footer
  bucket = `sum_amount`.
- **Downgrade.** It recreates 0035's view verbatim. A test diffs it against
  a database migrated straight to 0040.

## 4. Speed

The first version of the view unpacked labels with `jsonb_each`. The planner
estimates that at 100 rows per call, which pushed the money statements'
estimated cost from about 76k to 173k. That is over Postgres's
`jit_above_cost` (100k), so every statement paid about 320 ms of JIT
compilation, and `sum_amount` went from 1.31 s to 2.2 s.

Reading the label tables directly brought the estimate back to 76k, with no
JIT. The final medians were measured on the W7 probe: 4,000 synthetic
documents, after `ANALYZE`.

| Call | Before | After |
| --- | --- | --- |
| `sum_amount`, no filter | 1.32 s | 1.34 s |
| `sum_amount`, facet | 1.31 s | 1.31 s |
| chart series | 0.89 s | 0.91 s |
| chart footer | 0.47 s | 0.44 s |

## 5. Proof

These tests fail on the 0040 view or with the bucket forced off, and pass
on 0041:

- **`sum_amount`:**
  - F4 in the same month, and across a month boundary (the payment lands
    in the canonical document's month, matching the chart);
  - the itemised invoice whose receipt is labelled (140 becomes 60 + 40);
  - a manual merge with unequal amounts (the canonical amount);
  - W4's twin test, now citing the canonical document.
- **The view:** F4, F5, a ranking tie, lines, and a single unmerged
  document.
- **Charts and the footer:** the F5 bucket and its drill, in + not_in
  equals all spending, cross-month buckets adding up, and labels going
  A → B → A.
- **`/facets/counts`:** the twin-only value (F17).
- **Frontend:** vitest cases for the new footer and card row.

The full backend suite and the frontend lint, type-check and unit tests
pass.

## 6. Also settled in this run

W3's last check was observed live at 08:15 CET. A hosted client's first
request was logged as `mcp_rejected status=400 method=server/discover
protocol_version=2026-07-28`, and its fallback followed. That confirms the
first-request 400 is the MCP 2026-07-28 discovery probe, which the
installed SDK refuses.
