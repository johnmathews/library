# The chart engine

**Status:** active. **Last updated:** 2026-08-30 (spending-view backend, Task 8: §11's route table gains `GET /spending/{id}` and `GET /spending/{id}/footer/{bucket}`, now **twelve** routes not ten; new §7.1 documents the footer drill route — `_accounted_rows` as the one shared classify/convert/sign step, the split-document-emits-one-row-per-line shape and why the drill list deduplicates by `document_id` and sums a document's rows, and `FooterDocumentsOut.total` reporting the bucket's full size before paging; §11 gains `DataOut.splits`/`SplitValueOut` and `CellOutBody`'s `label`/`colour`; §13 drops "a sender split emits ids, not names" — fixed by Task 5 — and gains "`unconvertible` has no drill-through" in its place. `migrations/versions/0037_split_colour.py` joins **Covers**. The live-archive verification of the pre-existing engine is in [journal/260830-chart-engine.md](../journal/260830-chart-engine.md); this branch's own work is in [journal/260830-spending-view-backend.md](../journal/260830-spending-view-backend.md).) Earlier the same day — §10.1 now enumerates **five** writers of `amount_total`, not four — Ask's document-edit tool is the fifth, and the only one that does not translate the trigger's refusal into an answer the owner can act on; §13 gains that limit and the two adjacent ones found in the same review, re-extraction's partial skip of `amount_total` while still writing `currency`, and `skipped_fields` landing outside the `extraction_completed` event detail. The live-archive verification of this engine is [journal/260830-chart-engine.md](../journal/260830-chart-engine.md). Earlier (2026-08-29) — initial version — the `spend_facts` relation and the canonical-document rule, the `spend_lines`/`line_labels` write path and its two deferred sum triggers, rule translation, the two orthogonal axes and the invariant total, per-document-date conversion, the footer's **eight** categories, the drill-through, LLM rule drafting against the closed vocabulary, the ten `/api/spending` + `/api/documents/{id}/spend-lines` routes, and the `line_labels` index measured and declined. Design: [superpowers/specs/2026-08-28-charts-redesign-design.md](superpowers/specs/2026-08-28-charts-redesign-design.md) §5, §8.4 and §9; plan: [superpowers/plans/2026-08-29-charts-engine.md](superpowers/plans/2026-08-29-charts-engine.md). What an amount *means* is [money-facts.md](money-facts.md); this document is what a chart does with it.)
**Last verified:** 2026-08-30 — method: read `src/library/api/spending.py` end to end for the two new routes and the changed response models — `get_chart` (§11's new row), `chart_footer_bucket`, `FooterDocumentOut`/`FooterDocumentsOut`, `_resolve_splits`, `SplitValueOut`, `DataOut.splits` and `CellOutBody`'s `label`/`colour` fields; read `_accounted_rows`, `chart_footer_documents` and `_resolved_bucket` in `src/library/charts/footer.py` in full for §7.1; read `migrations/versions/0037_split_colour.py` and the `colour` columns on `Sender`/`FacetValue` in `src/library/models.py`. Route count re-taken from `grep -c '@router\.' src/library/api/spending.py` → 12. The split-document-emits-two-rows claim in §7.1 is covered by an executed assertion in `tests/test_chart_footer.py::test_a_split_document_appears_once_with_its_rows_summed` (a `100.00` document split `60.00`/`40.00`, both lines unlabelled, produces one `FooterDocument` with `amount=100.00`) and by the parametrised `test_the_list_length_equals_the_footer_s_count`; `GET /spending/{id}` is covered by `tests/test_api_spending.py::test_one_chart_can_be_read_by_id` and `test_reading_an_unknown_chart_is_a_404`; the drill route's paging/`total`/unknown-bucket/window-agreement claims are covered by `test_the_footer_route_lists_the_documents_behind_a_count`, `test_the_footer_route_caps_its_limit_at_100`, `test_the_footer_route_reports_the_buckets_full_size_before_paging` and `test_the_footer_route_and_the_footer_count_agree_after_a_window_narrows`, all in `tests/test_api_spending.py`. The mutation checks behind the "one shared classification" and "the dedup is load-bearing" claims are recorded in `.superpowers/sdd/2026-08-30-spending-view-backend/task-6-report.md` (Step 7 (a) and (b)) rather than re-run in this pass. Full backend suite run as part of this pass: see the journal entry for the verbatim count. Confirmed no real sender, amount or reference appears in the text added. Earlier the same day — method: the fifth `amount_total` writer was read rather than taken on report — `src/library/ask/engine.py`'s document-edit tool, where `_WRITABLE_FIELDS = tuple(DocumentUpdate.model_fields)`, `DocumentUpdate.amount_total` is a real field (`src/library/schemas.py`), and the tool's `await session.commit()` sits outside any SQLSTATE handling, so the trigger's refusal escapes the whole turn. `src/library/api/documents.py` and `src/library/extraction/apply.py` are now named here because §10.1 makes claims about both and the earlier method line named neither: read `update_document` in full (it commits through `commit_allocation` and translates `AllocationError` to a 400, so row two is right) and `apply.py`'s `scalar_values` loop in full, which confirms the skip is `amount_total`-only — `currency` and `amount_kind` are set on the same pass — and that `skipped_fields` reaches `document.extra["extraction"]` but is absent from the `extraction_completed` detail dict passed to `_record_event`, which is §13's third new limit. Row four re-checked against `src/library/importer/runner.py`'s `amount_total is None` guard. No code changed in this pass; the full backend suite stands at 2182 passed at `b32a67c`. Both docs gates run green in this pass. Confirmed no real sender, amount or reference appears in the text added. Earlier the same day — method: this document went red on `main` not from any drift in the code it covers, but because PR #121 squash-merged as commit `b32a67c`, dated 2026-08-30 UTC, one day after every stamp written on the branch — the `--since=<bare date>` failure mode already on record, here landing on the commit-date comparison `stale-covered-code`/`stale-doc-edit` both make. Re-verified in full rather than merely re-dated: re-read `src/library/charts/rule.py`, `query.py`, `footer.py` and `draft.py`, `src/library/spend_lines.py`, and `src/library/api/spending.py` end to end against the tree at `b32a67c`, and diffed every numbered claim in this document against them clause by clause — the canonical tie-break's `COALESCE(..., false)`, the `not_in` NULL arm, the three split expressions, the eight footer buckets and their `CASE` order, the ten routes, and the write path's trigger/escape-hatch/refusal table — all still match. Re-read the `spend_facts` view, `spend_lines_sum_matches()` and both constraint triggers in `migrations/versions/0035_spend_facts.py`, and the `charts` table in `0036_charts.py`, line by line. Route count re-taken from `grep -c '@router\.' src/library/api/spending.py` → still 10. No prose changed as a result — the code this document describes is unchanged from the PR that was reviewed under the 2026-08-29 stamp below; only the commit's calendar date moved. Both docs gates run green in this pass. Confirmed no real sender, amount or reference appears in the text. Earlier (2026-08-29) — method: read `src/library/charts/rule.py`, `query.py`, `footer.py` and `draft.py` in full, `src/library/spend_lines.py` in full, and `src/library/api/spending.py` in full (every response model, `_resolve_query`, `_merge_unconvertible`, `_footer_out`, `_data_out`, `_rendered_shares`, `_commit_allocation` and all ten route bodies); read the `spend_facts` view, the `spend_lines_sum_matches()` function and both constraint triggers in `migrations/versions/0035_spend_facts.py` line by line, and `0036_charts.py` for the `charts` table. Route count taken from `grep -c '@router\.' src/library/api/spending.py` → 10. The §11 `EXPLAIN` plans are the verbatim output of two runs against a freshly migrated, seeded and `ANALYZE`d `pgvector/pgvector:pg17`, not a reconstruction. Every behavioural claim below is covered by an executed assertion in `tests/test_spend_facts.py`, `tests/test_spend_lines.py`, `tests/test_chart_rule.py`, `tests/test_chart_query.py`, `tests/test_chart_footer.py`, `tests/test_chart_draft.py`, `tests/test_chart_model.py` and `tests/test_api_spending.py`; the full backend suite ran green (2182 passed) at the commit this document describes. Both docs gates run green in this pass. Confirmed no real sender, amount or reference appears in the text.
**Covers:** src/library/charts/, src/library/api/spending.py, src/library/spend_lines.py, migrations/versions/0035_spend_facts.py, migrations/versions/0036_charts.py, migrations/versions/0037_split_colour.py

> **Note on examples.** This repository is public. Every sender name, amount and
> reference number below is invented.

## 1. A chart is a saved question

The archive's old chart stack asked "which documents look alike?" and drew the
answer. This one asks a question in the owner's words and turns it into a
predicate over money:

```
name           AI subscriptions
question       money I spend on AI tools and subscriptions
rule           category IN (software) AND cost_type IN (subscription, usage)
default_grain  month
default_split  cost_type
currency       EUR
```

That row is the `charts` table (migration `0036`); `rule` is a serialised
[`Rule`](#3-the-rule-a-question-as-a-predicate) in a `JSONB` column. `name` is
unique. `default_grain` and `default_split` are *starting positions* only —
changing either at request time never changes the total (§4), which is why they
are called defaults and why the two axes are stored beside the rule rather than
inside it.

Three questions this feature exists to answer, and which no earlier surface
could: what the household spends on AI subscriptions per month, what it spends
on accountancy per year, and what it spends charging the car.

## 2. `spend_facts`: one relation, not scattered `COALESCE`s

Every chart query reads exactly one relation, the `spend_facts` view
(migration `0035`). Nothing else in the codebase issues SQL against it except
`src/library/charts/query.py` and `src/library/charts/footer.py`.

```
spend_facts (view)
  document_id | line_id | payment_id | is_canonical
  sender_id   | date    | amount     | currency
  amount_kind | reference
  labels  jsonb   -- {"category":"accountancy","scope":"business"}
```

The view is a `UNION ALL` of two arms: **unsplit documents**, which contribute
one synthetic row built from `amount_total`, and **the lines of split
documents**, one row each. The alternative — a `COALESCE` between the two in
every query — puts the same branch in every consumer, and puts label
inheritance in every consumer too. Here it exists once and has exactly one
place to be tested.

Only rows a chart could ever count reach the view at all: the `eligible` CTE
requires `deleted_at IS NULL` and `amount_total IS NOT NULL`. A document with
no amount has no `spend_facts` row — which matters in one place, §8.

### 2.1 Spend lines and label inheritance

Labels live on the **document**. Most documents in the archive carry no amount
at all and must still be labelled for search, so labels cannot live only on
money. A document splits only when its money genuinely divides:

```
document   Northwind Accounting   EUR 4,000.00
  labels:  category=accountancy   scope=business
  lines:   EUR 2,400.00   (no override)   -> accountancy, business
           EUR 1,600.00   scope=personal   -> accountancy, personal
```

Inheritance is one operator. The line arm computes
`COALESCE(document_labels, '{}') || COALESCE(line_labels, '{}')`, and `||` on
`jsonb` takes the **right** operand on a key collision — so a line overrides the
facets it names and inherits every facet it does not. A line row carries its
*document's* date, currency, sender, `amount_kind`, `reference` and
`payment_id`; only `amount` and `labels` are its own.

A document has either **no lines at all** or a complete set summing to
`amount_total`. There is no partial state, and that is enforced in the database
— see §10.

### 2.2 The canonical document, and its three tie-breaks

When two documents are one payment ([money-facts.md](money-facts.md) §4), only
one of them may contribute its money, or the merge would not have removed the
double count. `spend_facts.is_canonical` carries that choice and **every chart
sum filters on it**:

```sql
row_number() OVER (
  PARTITION BY e.payment_id
  ORDER BY e.has_lines DESC,
           COALESCE(e.amount_kind = 'payment_made', false) DESC,
           e.id ASC
) = 1 AS is_canonical
```

1. **A line-bearing document wins.** Otherwise merging an itemised invoice with
   its receipt would discard the itemisation, and a chart split by the facet the
   lines carry would lose the split entirely.
2. **`payment_made` beats `payment_due`.** A receipt is the better record of
   what was actually paid.
3. **Lowest id.** A tie-break that always decides, so the view is deterministic.

`COALESCE(..., false)` in the second key is **load-bearing**, and it is the kind
of defect that only execution finds. `amount_kind = 'payment_made'` is NULL for
an undecided document, and Postgres sorts NULLs **first** under `DESC` — so
without the `COALESCE` an undecided document outranks a receipt, becomes
canonical, and the whole payment is represented by a kind that is never summed.
The money vanishes from every chart with nothing reporting it. Confirmed red
under mutation (`tests/test_spend_facts.py`).

### 2.3 Soft deletes: the filter and the join are mutually redundant

The `eligible` CTE both joins to `payments` and filters `deleted_at IS NULL`.
An earlier draft of the design spec said the filter was redundant and "the
guarantee comes from the join". Mutation says otherwise, and the correction is
worth stating because it changes what must not be touched:

- Removing the **filter** alone changes no result.
- Removing the **join** alone does not readmit a deleted document either — five
  *merge* tests go red instead.

`payments` builds its recursive reachability from `documents WHERE deleted_at IS
NULL`, and every `payment_edges` arm filters `deleted_at` on both endpoints, so
each of the two independently excludes a deleted twin; only removing **both**
readmits one. What the join is indispensable for is payment **identity**:
without it there is no `payment_id`, `PARTITION BY` degenerates to one partition
per document, and every merged payment silently un-merges. That is a much
stronger reason to keep it than "it excludes deletions".

## 3. The rule: a question as a predicate

`src/library/charts/rule.py` is pure — no session, no I/O — so the translation
is exhaustively testable without a database.

```python
class Clause(BaseModel):
    facet: str
    op: Literal["in", "not_in"] = "in"
    values: list[str]

class Rule(BaseModel):
    all: list[Clause] = []      # ANDed; empty matches every row
```

`rule_predicate(rule)` returns a SQL fragment over the alias `sf` plus its bind
parameters. Facet and value keys are **always bound, never interpolated** —
they reach this function from an LLM draft as readily as from the owner.

An empty `Rule` returns `TRUE`: that is the "all spending" chart, and it is also
why a *failed* draft must never be saved as one (§9). A clause with no values is
a `RuleError` rather than a fragment that matches nothing.

The `not_in` arm is the part that is easy to get wrong:

```sql
(sf.labels->>:f0 IS NULL OR NOT (sf.labels->>:f0 = ANY(:v0)))
```

An unlabelled row has `labels->>facet IS NULL`, and `NULL <> ANY(...)` is NULL,
not TRUE. Without the explicit `IS NULL` arm an unlabelled row satisfies neither
a `not_in` rule nor its complement and disappears from **both** — money in a
hole between two predicates that are each individually correct. With it, the
three row classes (labelled-in-set, labelled-out-of-set, unlabelled) each
satisfy exactly one of `in`/`not_in`, which is the mechanical basis of §4's
invariance.

## 4. Two orthogonal axes, and why the total is invariant

A chart has a **time axis** (`grain`: `week`, `month`, `quarter`, `year`) and a
**split axis** (`split`: a facet key, the literal `sender`, or none). They are
independent, and the promise is that

> **the total is identical under every split.**

That holds only because the split is a `GROUP BY` over precisely the rows the
flat total sums — never an extra filter — and because an unlabelled row lands in
a `NULL` bucket rather than being dropped:

```
AI subscriptions — monthly, split by cost_type      EUR 412.00 in August
    subscription   EUR 212.00
    usage          EUR 200.00

same chart, split by sender                         EUR 412.00   <- unchanged
    Vendor A       EUR 312.00
    Vendor B       EUR 100.00
```

Three split expressions exist and the caller's axis name chooses between them as
a whole literal; the name itself is bound as `:split`, never spliced into the
column list:

| `split` | expression |
| --- | --- |
| `None` | `CAST(NULL AS text)` — one bucket |
| `"sender"` | `CAST(sf.sender_id AS text)` |
| a facet key | `sf.labels->>:split` |

`sender` is available at no cost because `spend_facts` carries `sender_id`. Note
that it emits the **id as text**, so `split_value` is `"41"`, not a sender's
name, and buckets sort lexicographically. Resolving ids to names is a display
concern and is deliberately not done in the engine.

`grain` is bound straight through as `grain.value` into `date_trunc`. There is
no lookup table mapping `Grain` members to strings: such a table could only
restate the enum, and a transposed entry (`WEEK: "month"`) would misbucket every
week, quarter and year chart while passing every test that exercised only
months. `date_trunc('week', ...)` is ISO — buckets start on Monday.

The bucket expression itself is written **once**
(`_PERIOD_EXPR_TEMPLATE`, over whichever day expression is substituted for it),
because it is three things at once: a selected column, the drill-through's
filter, and — through `charts.query.period_start` — the boundary the API
validates a requested `period` against. Two copies is how the panel comes to
open a bucket the chart never drew, or the API comes to refuse one it did. The
boundary is therefore asked of Postgres rather than recomputed in Python: a
second, correct-looking definition of the bucket in another language is exactly
what dropping the grain lookup table removed.

## 5. Currency: each amount converts at its own date

Amounts convert to the chart's display currency through `library.fx`
(date-aware, base USD), at the rate on **each document's own date** — never at
the period's. Converting a period's sum at one rate is a different number
whenever a rate moves inside the bucket, and it is a *plausible* number, which
is worse.

The query therefore selects **rows, not sums**, and aggregates in Python after
each row has been converted. `_converted()` is one function read by both the
series query and the drill-through: written twice, a divergence on the date
argument survived mutation testing green, because `convert_amount` short-circuits
when the two currencies match and a single-currency fixture never reads the date
at all.

An amount `library.fx` cannot convert is **reported, never dropped and never
counted 1:1** — it joins `unconvertible`:

```python
class Unconvertible(BaseModel):
    currency: str | None
    amount: Decimal
    documents: int
```

Three things about this class are the ones a reader most needs:

- **`currency` may be `None`** — an amount carrying *no currency at all*.
  `documents.currency` is nullable and the `amount_currency_coupling` extraction
  check is a **warn**, not a block, so an amount-bearing, dated, summable
  document with no currency is a permitted live state that reaches `spend_facts`
  untouched. It has no usable rate, which is exactly what this class means, so
  it is reported here. It sorts **last** and must be rendered, not filtered: a
  bare `sorted()` over the currency keys raises `TypeError` as soon as a `None`
  meets a real code, which was a 500 on every chart route in range of one such
  document before it was fixed.
- **`amount` is signed** for summable kinds and a **magnitude** for
  non-summable ones (a `coverage_limit` has no sign to give). The API merges the
  engine's list with the footer's by currency, so after that merge the number
  means "**money the chart could not express**", not "the net missing from the
  total".
- **`documents` must always be rendered beside `amount`.** An unconvertible
  payment and an equal unconvertible refund net to `amount = 0.00,
  documents = 2` — which reads as "nothing missing" while two documents are
  unrepresented. After the API's merge, `documents` is an **upper bound** on the
  distinct documents behind the amount, never an understatement: exactly one
  shape can be counted twice — a spend-line-split document with one line counted
  and another uncategorised, in a rateless currency — because every other footer
  bucket is a property of the *document* and so cannot co-occur with a counted
  line. Making it exact needs `Unconvertible` to carry document ids and merge as
  a union, which is an engine change nobody has needed yet.

## 6. Payments and documents: two counts that are not additive

`Series` reports `payments` and `documents`, and they are different questions:

- **`payments`** — how many distinct payment groups reached the total.
- **`documents`** — how many live documents belong to those groups, which is the
  larger number ("15 payments from 18 documents"). It is deliberately *not* the
  count of rows summed: the query reads only canonical rows, so a merged pair
  contributes one row and counting rows would report 1 for the 2 documents the
  owner can see. It comes from a second query over `payments`.

The footer's groups (§7) count something else again — **canonical rows** in each
group. A document split across spend lines can have one line counted and another
uncategorised, so it appears in `Series.documents` *and* in a footer group.

> **`Series.documents` and the footer's `documents` are not a partition of the
> archive and must never be added together.**

## 7. The footer: eight categories, and why a refund is netted

`src/library/charts/footer.py` is the most important module in the feature, and
it is deliberately separate from the query. It answers the opposite question —
what the total *missed* — and mixing the two is how "nothing is excluded
silently" quietly stops being true: a refactor of the sum has no reason to keep
the accounting correct, and no test notices.

> A document the model failed to label matches no rule, so without this it
> disappears from every chart with no way to notice.

**The categories are a partition, and that is the whole design.** One SQL
statement classifies every row the chart touched into exactly one bucket via a
single `CASE`, so a row cannot be counted twice and — the failure this module
exists to prevent — cannot fall between two `WHERE` clauses that were each
written correctly.

| bucket | what it is | footer field |
| --- | --- | --- |
| `counted` | already in the total | — (the query's job) |
| `netted_refund` | in the total, and lowering it | `netted_refunds` + `refund_count` |
| `excluded` | a kind that never enters a total | `excluded` (one group per kind) |
| `unclassified` | `amount_kind IS NULL` — *not yet decided* | `unclassified` |
| `undated` | summable, but no date to bucket it by | `undated` |
| `uncategorised` | summable, unlabelled for a facet the rule names | `uncategorised` |
| `outside` | dated outside the chart's window | — not this chart's claim |
| `unaccounted` | the `ELSE`: a shape nobody predicted | `unaccounted` |

Plus `unconvertible` (§5), merged with the query's own list. **Eight fields, all
always present on the wire.** An absent field and an empty one are different
claims, and only one of them is "nothing was excluded".

**`unclassified` and `unaccounted` are the two the design did not originally
have, and both exist because money was falling through.**

- `unclassified` — a document with an amount and an undecided `amount_kind` is
  summed by nothing; `excluded` filtered `amount_kind IS NOT NULL` while the
  other categories all required a *summable* kind, so such a document appeared
  in **no** line of the accounting at all. On the class of document a
  partly-backfilled archive has most of. It belongs under *needs attention*
  rather than *excluded from the total*: excluded means "correctly not
  spending", undecided means "not yet decided".
- `unaccounted` — the `CASE`'s `ELSE`. It is unreachable today, and that is
  exactly why it must surface rather than be filtered out: an `ELSE` that is
  dropped by the outer `WHERE` is a decoration, not a safety net, and no test
  can catch it being dropped. Routing a seeded amount to an unknown bucket name
  proved the difference — with the `ELSE` reported it appears as an
  `unaccounted` group and the accounting still balances; without it, the balance
  was short by exactly that amount, silently. **`unaccounted` should always be
  null. If it is not, the classification has a hole and this is the money in
  it.**

**A refund is netted, not excluded.** It *is* in the total and the point of a
signed `amount_kind` is that it lowers it, so it is reported in the header block
beside the total (`including 1 refund netted off −EUR 49.00`) as a positive
magnitude with its count. Listing it under "excluded from the total" would read
as money the chart ignored, which is the opposite of what happened. One
consequence for the UI: a split value whose net is negative draws below the
baseline, so the y-axis of any chart containing a refund must include zero. A
refund the chart could **not** convert is not netted off anything and is left to
`unconvertible` instead.

**Touched, not merely matching.** The rows the footer considers are those the
rule matches *or* that are missing a label for a facet the rule names
(`NOT (sf.labels ?& CAST(:facets AS text[]))`). Restricting to matches alone
would hide every unlabelled row behind the very label it is missing — which is
the gap; widening to the whole archive would make every chart report money
belonging to a different question. The consequence is intended: unlabelled
excluded money appears in the footer of *every* chart whose date and currency
window contains it.

Branch order inside the `CASE` is load-bearing: `outside` first but only for
rows that *have* a date (an undated row sits in no window, so no window may drop
it); kind before rule, so a coverage limit is reported as excluded rather than
competing with the label branches; and rule before `uncategorised`, so an
unlabelled row a `not_in` rule already counted (§3) is not *also* reported as a
gap the chart did not have. Counted money is not missing money.

The set of facets the rule names is supplied by the **router**, not derived in
`footer.py` — which trusts its caller. Passing an empty set for a facet-bearing
rule switches off the reporting of uncategorised money with no error and no test
in `footer.py` able to notice, so `_ChartQuery.facets_in_rule` is the one place
it is computed.

### 7.1 Drilling into a footer bucket

`GET /spending/{id}/footer/{bucket}` is `uncategorised`'s (and every other
excluded bucket's) equivalent of §8: without it a footer count is a number
with nowhere to go, and `uncategorised` in particular is described in §7 as
*a visible task* precisely because it tends to be large.

The footer's per-row classify/convert/sign step is shared, not duplicated:
`_accounted_rows` is the **one** execution of the footer's `CASE` statement
and the one conversion/sign step, called by both `chart_footer` (which
aggregates its output into `Footer`'s eight fields) and
`chart_footer_documents` (which filters and deduplicates the same output into
a document list). There is exactly one classification to disagree with
itself, which is what makes "the panel must add up to the bar" true of the
footer as well as of `/cell`. `_resolved_bucket` — mapping any bucket name
`footer.py` does not recognise to `unaccounted` — is shared for the same
reason: an unforeseen bucket name must not make the footer report
`unaccounted` money while the drill route silently returns nothing for it.

**A document split across spend lines emits one row per line into a bucket.**
A `100.00` document split `60.00`/`40.00`, neither line labelled, under a rule
naming `category`, produces **two** `uncategorised` rows sharing one
`document_id`, while `Footer`'s `_Group.documents` — a `set[int]` — reports
`1`. Proved by executing this shape against Postgres before it was planned
([journal/260830-spending-view-backend.md](../journal/260830-spending-view-backend.md)).
So the drill list **deduplicates by `document_id`**, and a listed
document's `amount` is the **sum of its rows in that bucket** (`100.00`), not
one row's (`60.00`) — rendering a single row's amount would print a number
that appears nowhere in the footer's own accounting.

`FooterDocumentsOut.total` is the bucket's full size **before** paging: a
bucket with more documents than `limit` still returns only a page, and
without `total` a client cannot tell a complete list of 3 from the first 100
of 340. `limit` is capped at 100, the same bound every other list endpoint
uses. `amount_kind` selects one group out of `excluded` (a list of groups
rather than one figure); it is ignored for every other bucket, which has
exactly one.

`unconvertible` is deliberately **not** one of `_FOOTER_BUCKETS` — see §13.

## 8. Drill-through: the panel must add up to the bar

`GET /spending/{id}/cell` lists the payments behind one cell, each expandable to
its documents. Its one requirement is that it lists exactly the rows the bar
summed, and the way that is guaranteed is structural rather than tested:
`chart_series` and `chart_cell` both call one `_rows_query`, and the
drill-through **appends** two conditions to the shared `WHERE` (`period` and
`split_value`) rather than restating any of it. The narrowing is passed as a
`bool`, not a SQL string: `AND` binds tighter than `OR`, so a caller-supplied
fragment of `" OR TRUE"` would defeat the entire `WHERE` and hand the panel the
whole archive. A flag makes a widening narrowing unrepresentable.

Two details:

- `split_value` is compared with `IS NOT DISTINCT FROM`, never `=`. `None` means
  the **unlabelled** bucket, and `= NULL` is never true — the one cell whose
  rows are hardest to find by hand would be the one cell the panel could not
  open.
- A `period` that is not the grain's boundary is a **422 naming the correct
  boundary**, not an empty list. `date_trunc(grain, date) = period` matches
  nothing mid-bucket, and an empty panel under a non-empty bar reads as "you
  spent nothing here". The boundary comes from `period_start`, i.e. from the
  same expression the chart bucketed with (§4).

Each payment lists **every live document in its group, canonical or not**. Only
the canonical row carried the money in, so `CellPayment.total` comes from that
row alone — but the panel is where a wrong merge is noticed and split, and a
list showing only the canonical half would hide the only evidence the merge was
wrong. The document list is read from `payments` joined to `documents`, **not**
from `spend_facts`, for exactly that reason: a hand-made `MERGE` override joins
two live documents with none of the rules' `amount_total IS NOT NULL`
precondition, so an amountless document has no `spend_facts` row at all — and it
is precisely the hand-made merge this panel exists to expose. `amount` and
`currency` on a listed document are therefore **optional**; declaring them
non-optional turns drilling into such a cell into a 500.

A row `library.fx` cannot convert is skipped here exactly as the series skips
it, so a payment *all* of whose rows are unconvertible does not appear at all —
matching the chart, which did not count it either. The footer accounts for it.

Never sum `documents[].amount` to reconstruct the bar: a merged pair doubles it,
a group member outside the cell's period or split bucket is still listed, and an
unconvertible member is listed but not counted. `CellPayment.total` is the only
number that matches.

## 9. Drafting a rule from a question

`src/library/charts/draft.py` turns plain language into a `Rule` against the
**current** vocabulary. Two structural decisions carry the guarantee:

- The API backend calls `client.messages.parse()` with a Pydantic
  `output_format`. `messages.create()` plus `json.loads` has shipped twice in
  this repository and been reverted twice — the model wraps its otherwise
  correct JSON in a ```` ```json ```` fence. The subscription backend cannot use
  `parse()` and therefore raises `DraftError` *before* any model call rather
  than degrading, because a tolerant free-text parser would silently produce the
  broadest possible chart.
- The `output_format` schema is deliberately **permissive**; it is not the
  closed-vocabulary gate. The gate is `filter_drafted_rule`, applied to the
  response after it comes back. The prompt is a request; the filter is the
  guarantee. And the filter copies the **vocabulary object's** key, never the
  model's string — so no model-produced text can reach a saved rule at all.
  Aliases resolve to their canonical key; an unrecognised *operator* is dropped
  rather than read as `in`, because reading one as `in` would invert an
  exclusion into an inclusion — the one rewrite that moves money *into* a chart.

Everything dropped is returned as `unknown_terms`, capped in count and length
(it is unbounded model-authored text that will be rendered). A blank or
whitespace-only term is reported as `(blank)` rather than dropped silently: an
unreported drop is a silently narrowed question.

The branch that matters is on `unknown_terms`, **before** any preview. When
every clause is dropped the result is `Rule(all=[])` — which matches every row
and is indistinguishable from "all spending". So a collapsed draft returns
`rule: null`, `preview: null`, `expressible: false`: nothing saveable, and the
archive's total never appears in the response to a question nobody could
answer. `expressible` is false whenever *anything* was dropped, even when a
surviving rule is still previewed, because a preview built from part of a
question is an approximation and the client has to say so.

`draft.py` uses `settings.extraction_model`. It deliberately introduces no
`*_model` setting of its own: every one of those needs a matching row in
`MODEL_PRICING_USD_PER_MTOK` or the app refuses to boot.

## 10. Spend lines: the write path

`src/library/spend_lines.py` replaces a document's **whole** allocation. A
partial write has no meaning, so there is no patch-one-line operation.

`sum(lines.amount) = documents.amount_total` is enforced by the database, by one
plpgsql function bound to **two** `DEFERRABLE INITIALLY DEFERRED` constraint
triggers — on `spend_lines`, and mirrored on `documents (AFTER UPDATE OF
amount_total)`. The mirror is not belt-and-braces: `amount_total` is writable
from `PATCH /api/documents/{id}`, from re-extraction and from the importer, so
without it, allocating 100.00 across 60.00/40.00 and then correcting the
document total to 120.00 succeeds — leaving lines that sum to 100.00 against a
total of 120.00, and `spend_facts` emitting the line rows, so the document
contributes the wrong number to every chart with nothing in the footer. Deferral
is required because a two-line split inserts as one transaction and an immediate
check would fail on the first row.

The trigger's escape hatch tests for the **absence of lines**, not for a zero
sum:

```sql
IF EXISTS (SELECT 1 FROM spend_lines WHERE document_id = doc_id)
   AND line_total IS DISTINCT FROM doc_total THEN
```

A fully cleared allocation must be legal, but `line_total = 0` is a different
predicate: a document legitimately allocated as `[0.00, 0.00]` or
`[50.00, −50.00]` would short-circuit, and its `amount_total` could then be
corrected to anything at all. `EXISTS` says what is meant.

Everything that can be refused **by name** is refused before the first row is
written, because the trigger is a backstop and not an error message — it fires
at `COMMIT` and arrives under asyncpg as a bare `DBAPIError`, which is a 500
where the caller deserves a 400. The order is: the document exists and has an
amount → every line's scale → the sum → **every label resolved** → clear →
insert. Resolving all labels first removes a real window: resolving inline
leaves a caller who catches the error holding a session whose old allocation is
already deleted and whose new one is half written. And a line amount with more
than two decimal places is **rejected**, not quantised — `33.333` three times
sums to `100.000` in Python while the stored `Numeric(14,2)` rows sum to `99.99`
— because rounding the owner's numbers without saying so is exactly the silence
this feature exists to remove.

### 10.1 What the mirror trigger's refusal looks like to each writer

The trigger fires at `COMMIT`, so it is the *committer* that has to explain it,
not the statement that broke the invariant. `spend_lines.commit_allocation` is
that one place: it checks SQLSTATE `P0001` — plpgsql's `RAISE`, and in this
schema nothing else — turns it into `AllocationError` carrying the caller's own
wording, and **re-raises everything else untouched**, so a deadlock, a lock
timeout or a foreign-key violation still reaches a 5xx instead of being reported
as an allocation problem the owner caused.

There are **five** writers of `amount_total`, and they answer differently,
because a refusal only helps if the person reading it can act on it. Four of
them answer; the fifth does not.

| writer | answer |
|---|---|
| `PUT`/`DELETE /api/documents/{id}/spend-lines` | 400, "the spend lines do not sum to the document total" |
| `PATCH /api/documents/{id}` | 400, naming the allocation and saying to clear or replace the lines first |
| re-extraction (`extraction/apply.py`) | the write is **skipped**, and reported in `extra["extraction"]["skipped_fields"]` |
| the importer (`importer/runner.py`) | nothing to do — it writes only when `amount_total IS NULL`, and an allocated document has one |
| Ask's document-edit tool (`ask/engine.py`) | **nothing — this one is unguarded.** `_WRITABLE_FIELDS` is `tuple(DocumentUpdate.model_fields)`, which includes `amount_total`, and the tool commits without the SQLSTATE check, so the trigger's `DBAPIError` escapes the whole Ask turn as a 500 with a poisoned session |

The fifth row is pre-existing — it predates this engine and the trigger it now
trips over — and it is unreachable until the archive holds its first allocated
document, of which there are none. It is a known limit, not shipped behaviour
anyone has seen: see §13.

Extraction skips rather than fails because a 400 has no reader in a background
job: raising would abort the same commit that records `extraction_completed` and
discard an otherwise good extraction over one field. An allocation is the
owner's own arithmetic over the amount, so it outranks a re-read of the page for
exactly the reason `extra["user_edited_fields"]` does — but the skip is written
onto the document rather than passed over, because an amount quietly left behind
is the silence this feature exists to remove.

## 11. The API

Twelve routes, mounted at **`/api/spending`** rather than the design's
`/api/charts`: the old series stack still owns `/api/charts` across thirteen
routes, and this router takes that prefix when that one is deleted.

| method | path | notes |
| --- | --- | --- |
| `GET` | `/api/spending` | saved questions; `limit` ≤ 100, `offset` |
| `POST` | `/api/spending` | save (201); duplicate `name` → 409 |
| `GET` | `/api/spending/{id}` | one saved question |
| `PATCH` | `/api/spending/{id}` | every field optional |
| `DELETE` | `/api/spending/{id}` | 204 |
| `GET` | `/api/spending/{id}/data` | `?grain&split&from&to&currency` |
| `GET` | `/api/spending/{id}/cell` | `?period&split_value` + all of `/data`'s |
| `GET` | `/api/spending/{id}/footer/{bucket}` | the documents behind a footer count; `limit` ≤ 100 |
| `POST` | `/api/spending/draft` | question → rule, split, preview |
| `GET` | `/api/documents/{id}/spend-lines` | a document's allocation |
| `PUT` | `/api/documents/{id}/spend-lines` | replace the whole allocation |
| `DELETE` | `/api/documents/{id}/spend-lines` | return to unsplit (204) |

`GET /spending/{id}` exists so the workspace can load a chart directly by id
rather than paging the list — a page-scoped `GET /spending?limit=…` would stop
finding a chart as soon as there are more of them than the page size.

**No router builds SQL.** Every number comes from `charts/query.py`,
`charts/footer.py`, `charts/draft.py` or `spend_lines.py`; this module parses,
validates, calls them and serialises. Five things it is responsible for that
nothing underneath it can be:

1. **`facets_in_rule`** for the footer (§7).
2. **`/cell` asking `/data`'s exact question.** The argument set is built once
   per request as a frozen `_ChartQuery`, and `shared()` returns a `TypedDict`
   unpacked into *both* engine calls — so adding an argument to the shared
   predicate without adding it here is a `mypy` error rather than a panel that
   quietly answers a different question. `/data` also **echoes the resolved**
   `grain`, `split`, `currency`, `since` and `until` so a client can drill with
   them verbatim.
3. **All eight footer fields** (§7). A serialiser written from a minimal reading
   drops `unclassified` and `unaccounted` and silently restores the bugs they
   were added for.
4. **An empty rule means all spending** (§9).
5. **The footer drill inherits `/data`'s exact resolution.** `/footer/{bucket}`
   calls `_resolve_query` with `split=None` — which means *take the chart's
   default split*, not *no split axis*; the empty string is what clears an
   axis. `chart_footer_documents` itself takes no split argument, so the
   resolved value is inert in the query, but resolving it still runs
   `_validate_split` against the chart's default, so a chart whose default
   split names a facet deleted at runtime is a **422 from both routes**,
   rather than a footer panel opening under a chart that will not draw.

`DataOut.splits: list[SplitValueOut]` names and colours every split bucket
present in `cells`, resolved the same way `/cell`'s `split_value` is (§4): a
sender id becomes the sender's name, a facet value key becomes its label, and
the unlabelled bucket resolves to a fixed placeholder string with `value:
null`. `SplitValueOut.colour` is a stored override, `null` meaning the client
derives a stable palette slot from `value`. `splits` is `[]` for an unsplit
chart — there is no axis, so there are no buckets to name, which is a
different claim from a split axis whose only bucket is the unlabelled one.
`CellOutBody` carries the same `label`/`colour` pair for its own one bucket,
so a drilled panel can title itself without a second read of `/data`.

Validation worth knowing about:

- A rule naming a facet or value that is not in the vocabulary is a **422 naming
  it**, on the **read** path as well as at save. Facet values are deletable at
  runtime, so a saved chart can rot; an empty chart is indistinguishable from
  "you spent nothing on that". For the same reason the vocabulary read is
  **uncached** — a cache would resurrect a deleted value and hand back exactly
  the empty chart this check exists to prevent.
- `split=""` (the empty string) means "no split axis" on a chart that defaults
  to one; an omitted `split` takes the chart's default. An unknown split axis is
  a 422.
- `since > until` is a 422 rather than an empty chart.
- On `PUT /spend-lines`, every refusal `spend_lines.py` can name is a **400**.
  The deferred sum trigger is translated to a 400 too — but **only** on
  `SQLSTATE P0001`, a plpgsql `RAISE`, which in this schema is the sum trigger
  and nothing else. A broad `except DBAPIError` also catches deadlocks, lock
  timeouts, dropped connections and foreign-key violations and would report each
  of them to the owner as "the lines do not sum" — a wrong diagnosis that never
  reaches a 5xx, hiding a real defect behind a plausible message. Anything else
  re-raises. Postgres' raw text is not echoed back.

### 11.1 Money on the wire

Money is quantised to cents **at the serialiser only**. The engine returns
unquantised `Decimal`s on purpose, because rounding per cell would break
`total == sum(cells)`.

That leaves one hazard, and it is fixed by construction rather than by care:
quantising the cells and the total *independently* can disagree, because
`fx.convert` does not round. Two cells of `10.005` render as `"10.01"` and
`"10.01"` under a headline of `"20.01"`. So **the headline total is the sum of
the rendered cells**, computed after them. The engine's own invariant is
untouched; only what the client reads is reconciled.

`/cell` does the same at one level down, by **largest-remainder
apportionment**: the payments' rendered shares are adjusted so they sum exactly
to the bar the owner clicked. The trade is explicit — a single payment can
display a cent away from its own exact converted value. That is the right side
of it: a drill-through that contradicts the bar it opened is the worse failure
and is precisely what §8 exists to prevent, and a payment's "exact" displayed
value is itself a conversion artefact. Ties break on position, so the ordering
is stable across calls.

## 12. Performance: an index measured, and declined

The design spec originally said "a GIN index on `labels` serves both" a rule and
a split axis. It cannot: `labels` is computed by `jsonb_object_agg` *inside* the
view, so there is no stored column to index, and Postgres rejects `CREATE INDEX`
on a view outright. The question that remained was whether the aggregation
needed an index on `line_labels (line_id)`.

Measured with `EXPLAIN (ANALYZE, BUFFERS)` against a freshly migrated, seeded
and `ANALYZE`d Postgres 17, on the shape a chart actually runs. A sequential
scan **does** appear at 1,800 `line_labels` rows — but at a realistic 36,000
rows, with **no new index present**, the planner uses the primary key, whose
leading column is already `line_id`:

```
->  GroupAggregate ...
      Group Key: ll.line_id
      ->  Index Scan using pk_line_labels on line_labels ll
            (cost=0.29..2077.65 rows=36000 width=16) (actual time=0.005..5.248 rows=36000)
Execution Time: 97.964 ms
```

With `ix_line_labels_line` added:

```
->  GroupAggregate ...
      Group Key: ll.line_id
      ->  Index Scan using ix_line_labels_line on line_labels ll
            (cost=0.29..1539.12 rows=36000 width=16) (actual time=0.017..5.523 rows=36000)
Execution Time: 97.703 ms
```

97.96 ms → 97.70 ms, for 728 kB and a B-tree write on every `line_labels`
insert. **The index was not added.** The seq scan at 1,800 rows is a table-size
artefact, not a missing-index symptom: `line_lbls` is an unfiltered whole-table
`GROUP BY ll.line_id`, so every row must be read whatever the access path and no
index can remove work — it can only change how the rows are fetched. Forcing
`enable_seqscan = off` at 36,000 rows produces the identical plan node, which
confirms there is no access path the new index unlocks. The correction to the
spec is therefore not "index the joins some other way"; it is that **the primary
keys already cover them**.

Add it later in one line if a real workload ever says otherwise.

## 13. Known limits

- **A payment group with two signs.** The sign precondition
  ([money-facts.md](money-facts.md) §4, §5) does not constrain the manual
  `MERGE` override arm, so a human merge of two undecided documents followed by
  a `backfill-amounts` run that classifies one as `refund` produces a group
  whose sign is undefined. Only the canonical document's sign reaches a total.
- **An unmerged invoice/receipt pair double-counts**, and the footer cannot
  report it: two unmerged documents are two ordinary counted payments,
  indistinguishable from two genuine ones ([money-facts.md](money-facts.md) §5).
- **The unconvertible `documents` count is an upper bound** after the API's
  merge (§5).
- **`unconvertible` has no drill-through.** Every other footer bucket lists
  its documents; `unconvertible` is not a `_CLASSIFY_SQL` bucket but a merge
  of two separately-reported lists (§5), so listing it needs `Unconvertible`
  to carry document ids and merge as a union — the same engine change the
  upper-bound `documents` count already wants.
- **`unclassified` is window-scoped.** A document with an undecided
  `amount_kind` outside every chart's range is still counted nowhere; there is
  no archive-wide backlog count and no way to set `amount_kind` by hand
  ([money-facts.md](money-facts.md) §5.1).
- **Two `in` clauses on one facet** are ANDed into a permanently empty chart,
  since a document takes at most one value per facet. The draft filter does not
  collapse them. **The footer cannot explain it either.** Its `uncategorised`
  arm is scoped by `WHERE ((rule) OR unlabelled)`, and a rule no labelled row
  can satisfy leaves only the unlabelled rows admitted — so every labelled
  document is absent from the total *and* from the accounting under it. That is
  §12's "indistinguishable from you spent nothing", reached through the draft
  path: the one shape where the footer's own guarantee does not apply.
- **Extraction does not propose spend lines yet.** `SpendLineOrigin.EXTRACTED`
  exists in the enum, but every line shipped today is `manual`.
- **The fifth `amount_total` writer is unguarded** (§10.1). Ask's document-edit
  tool commits without translating SQLSTATE `P0001`, so on an allocated document
  an amount correction made through Ask fails as a 500 with a poisoned session
  rather than the named 400 the other writers give. Pre-existing, and unreachable
  until the first allocation exists — the archive has none. The fix is to reuse
  the same helper `spend_lines.commit_allocation` uses.
- **Re-extraction's skip is partial.** It skips `amount_total` on an allocated
  document but still writes `currency` and `amount_kind`, so a re-read that finds
  a different currency leaves the currency disagreeing with the denomination of
  the lines — and `amount_currency_coupling` will not fire, because both fields
  are set. The clean form is to skip `currency` alongside.
- **`skipped_fields` is recorded where nobody reads it.** It lands in
  `extra["extraction"]`, not in the `extraction_completed` event detail, which is
  the surface the document timeline renders — so today the owner is told nothing
  about the field that was left behind.
