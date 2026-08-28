# Charts: spending questions over faceted documents

**Status:** design, awaiting review (2026-08-28)

Replaces the emergent-series `/charts` feature entirely. Nothing from the current
implementation is migrated.

> **Note on examples.** This repository is public. Every sender name, amount and
> document title below is invented. Counts and proportions are real, measured
> against the live archive on 2026-08-28.

## 1. Problem

`/charts` cannot answer the questions it exists to answer. The three the owner
actually asks are:

- *How much am I spending on AI subscriptions each month?*
- *How much am I spending on accountancy fees each year?*
- *How much am I spending on charging the EV?*

None is answerable today, for three reasons living in three different layers.

**There is no "amount I spent."** `documents.amount_total` holds whatever number
the extractor found: an invoice total, a tax assessment, an insurance coverage
ceiling, a €0.00 nil-return confirmation. All are summed together.

**There is no category.** `kind` is a document-shape label (`invoice`, `receipt`,
`other`), not a spending category. "AI subscriptions" and "accountancy fees" do
not exist anywhere in the schema. The free-text `tags` field is unusable (§2.3).

**There is no query model.** The view renders pre-grouped `(sender, kind)` tiles
and nothing else. There is no aggregate, no cross-series comparison, and no way
to ask a question.

## 2. Findings

Measured against the live archive: 257 documents, 174 amount-bearing.

### 2.1 Series identity is mechanical and wrong

`api/charts.py::_eligible_series` is the whole detector:

```sql
GROUP BY sender_id, kind_id HAVING count(*) >= 3   -- amount-bearing, non-deleted
```

`series.py::summarize_series` then narrows twice more, silently: it keeps only
the most populous `(sender_id, kind_id)` group, then only the dominant currency
bucket. `SeriesCoverage` records what was dropped; the UI never shows it.

Consequences observed live:

1. **Currency splitting deletes the interesting series.** One vendor's
   `(sender, invoice)` group holds 11 documents — 7 in USD (small pay-as-you-go
   top-ups) and 4 in EUR (the actual recurring subscription). The USD bucket
   wins on count, so the chart shows the top-ups and the subscription is
   discarded. A second group for the same vendor is dropped entirely: 3
   documents, dominant currency bucket of 2, below the threshold of 3.
2. **`kind` splits one payment stream across tiles.** The same vendor appears
   under both `invoice` and `receipt`. One government sender is smeared across
   `invoice`, `letter`, `other` and `utility-bill`.
3. **`other` is a garbage bucket that becomes a chart.** 55 of 257 documents are
   `kind=other` — the second-largest kind. One such "series" charts six
   unrelated one-off tax events as a trend.
4. **Nothing checks that a series recurs.** `classify_cadence` computes a label
   which is only ever *displayed*. The largest chart on the page — 31 documents
   of irregular project fees spanning six years — is headed
   `"<sender> · irregular series"`. The page states it is not a series and
   charts it anyway.
5. **Ordering is by document count**, so the least meaningful series is always
   first. There is no sort, filter, or way to hide a chart.

### 2.2 The LLM layer confabulates

`series_insight.py::build_series_prompt` passes sender, kind, currency, cadence,
count, five summary statistics and a `date=amount` timeline. The model never sees
a document, a title, or any text. It therefore invents a causal story for
whatever numbers it is handed. Observed in the live cache:

- An insurance comparison site's three documents — whose amounts are **coverage
  ceilings**, not payments — were described as energy costs that had "surged
  dramatically… a ninefold increase, with consumption or rates now stabilizing."
  Every clause is fabricated.
- One description notes invoices "often arrive in pairs on the same date": the
  model observed the duplicate-document problem (§2.4) and rationalised it as a
  real billing pattern.
- One description says "with only two observed payments" for a series whose
  count it was told was three.
- One description offers advice, which its system prompt forbids.

`series.py::odd_ones_out` is deliberately LLM-free, with a comment recording a
past hallucination. The headline prose on every tile has no such guard.

### 2.3 The tag vocabulary has collapsed

**771 distinct tags. 454 used exactly once (59%).** Four distinct failure modes:

| failure | evidence |
| --- | --- |
| synonym sprawl | seven separate tags for vehicle servicing; five for accountancy |
| encoding and spelling | a mojibake variant of a car marque alongside its correct spelling |
| axes jumbled together | places, people, years, scope, document-shape and vendors share one flat namespace |
| redundant with columns | years duplicate `document_date`; vendors duplicate `sender`; `invoice`/`receipt` duplicate `kind`; `paperless:*` is import residue; one tag applies to 65 documents and carries no information |

Roughly 40% of the vocabulary duplicates a column that already exists.

### 2.4 One payment is documented more than once

20 groups of documents share an exact `(sender, date, amount, currency)`,
covering 40 documents — **23% of all amount-bearing documents**. Inspection of
every group shows all 20 are one real-world event documented twice: an emailed
invoice and a downloaded receipt, a policy cover sheet and its premium
specification, a booking confirmation and its payment confirmation.

`documents.sha256` is unique, so these are not byte-identical re-uploads — they
arrive by different routes as genuinely different files. In two cases the
extractor gave the same underlying charge two different titles.

Only 3 of the 20 groups have differing `kind` values, so `kind` cannot be used to
identify the pairing.

### 2.5 Presentation

- Bars are placed on a continuous time axis, one per document, so irregular
  events produce hairline bars where two documents fall days apart and large
  gaps elsewhere. `maxBarThickness: 32` and a 160px tile compound this.
- The timeframe control **clamps the axis without recomputing the statistics**.
  At the default of "last 12 months", a tile reports a document count, median and
  trend drawn from six years of data above a chart showing one year.
- Y axes are not shared and ticks carry no currency, so tiles are not comparable
  even though the x-axis window is.
- Grouping sums per period, which combined with §2.4 double-counts monthly
  totals.
- Every tile carries edit, delete, open, add-documents, suggestions and
  odd-ones-out controls. The page reads as an admin screen.
- Drill-through exists only as a hover tooltip, capped with "+N more" and
  unreachable by keyboard.
- There is **no aggregate view at all** — no total spend over time, no spend by
  category, no comparison across series.

### 2.6 Root cause

One decision, made when no better tool existed: **series identity is
`(sender, kind, currency)` plus a count threshold.** Everything downstream —
the statistics, the prose, the tile grid — faithfully renders groups that are not
real. The embedding machinery for a better answer already exists in
`semantic_membership.py` but is wired only to manual curation, and its own
docstring records the constraint: *"the LLM never decides membership."*

## 3. Goals

1. Answer "how much am I spending on X per period", where X is a semantic
   category, not a sender.
2. Drill up and down the time axis: week, month, quarter, year.
3. Split by a second axis chosen per question — provider, cost type, personal
   vs business — and toggle it without changing the total.
4. Reach the source documents behind any number in two clicks.
5. Never exclude money silently. Every exclusion is reported where it affects a
   number.
6. Give the archive a controlled label vocabulary usable by search and filters,
   not only by charts.

## 4. Non-goals

- Statistical verdicts: mean, median, stdev, z-score, "vs usual", year-over-year.
  These existed because the old design had nothing better to say about a group it
  did not understand.
- LLM prose narrating a chart. The model labels documents, where its output is a
  checkable value from a closed set. It is not asked to describe data it cannot
  see (§2.2).
- Budgets, forecasts, alerts.
- Multi-user scoping. Library remains a single shared family archive
  (`architecture.md` §1.5).

## 5. Architecture

Three layers. Two are independent and converge on the third.

```
A  facets                        B  money facts
   controlled vocabulary            amount_kind, reference
   labels on documents              payment identity
   labels on spend lines            spend lines
   search + filter surfaces         spend_facts view
              └───────────┬───────────┘
                          v
              C  chart engine
                 rule over spend_facts
                 time axis x split axis
                 drill-through
                          v
              D  /charts board + workspace
```

### 5.1 Where a label lives

Labels live on the **document**. 83 of 257 documents have no amount at all and
must still be labelled for search, so labels cannot live only on money.

**Spend lines exist only where money divides.** For the common case there is no
line row. When a document does split, each line carries labels only for the
facets that differ and inherits the rest:

```
document #77   Northwind Accounting   EUR 4,000.00
    labels:  category=accountancy   scope=business
    lines:   EUR 2,400.00   (no override)      -> accountancy, business
             EUR 1,600.00   scope=personal     -> accountancy, personal
```

**One relation feeds every chart.** The `spend_facts` view unions unsplit
documents (synthesising one row from `amount_total`) with split lines (applying
inheritance), so no `COALESCE` branch is scattered through query code and the
inheritance rule has exactly one place to be tested.

```
spend_facts (view)
  document_id | line_id? | payment_id | is_canonical
  sender_id | date | amount | currency
  amount_kind | reference
  labels  jsonb    -- {"category":"accountancy","scope":"business",...}
```

**Labels are one `jsonb` column, not one column per facet.** Facets are created
and deleted at runtime, so a view with a fixed column per facet would need
regenerating on every vocabulary edit. A rule reads
`labels->>'category' = 'accountancy'` and a split axis is
`GROUP BY labels->>'scope'`; a GIN index on `labels` serves both. This replaces
an earlier draft of this section that specified one column per facet — it does
not survive contact with §7.5's CRUD.

**Canonical document within a merged payment.** When two documents are one
payment (§8.3) only one may contribute its money, or the merge would not have
removed the double count. The canonical one is chosen as: a **line-bearing
document wins** (otherwise merging an itemised invoice with its receipt would
discard the split), then `payment_made` over `payment_due`, then lowest id.
`spend_facts.is_canonical` carries the result and every chart sum filters on it.

### 5.2 Prototype results

The schema, both views and the merge rules of §8.3 were built and executed
against PostgreSQL 17 before this spec was finalised, using shaped fixtures that
mirror every ambiguous case in the live archive. Recorded here because §13
requires it and because two findings above came out of it rather than out of
reasoning.

Verified green:

| check | result |
| --- | --- |
| R1 / R2 / R3 fire on the pairs they should, and only those | 5 merges, correct rule each |
| two same-amount purchases four days apart, both `payment_made` | kept separate |
| four same-amount invoices, one same-day pair | only that pair merged |
| VETO: same sender, date and amount, differing references | kept separate |
| label inheritance across a split document | inherited facet kept, overridden facet replaced |
| total invariant across split axes | identical under no split, by `scope`, by `sender` |
| double-count | merged pair contributes once, not twice |
| footer | `coverage_limit` and uncategorised money both reported |

Verified to reject:

| attempted | rejected by |
| --- | --- |
| two values of one facet on one document | primary key |
| label whose `facet_id` disagrees with its value | composite foreign key |
| lines that do not sum to `amount_total` | constraint trigger |
| deleting a facet value still in use | foreign key |

`SPLIT` and `MERGE` overrides were confirmed to un-merge an automatic pair and
to join a pair no rule merges, respectively.

## 6. Data model

```
facets              key, label, ordinal
facet_values        facet_id, key, label, parent_id NULL, ordinal
facet_value_aliases facet_value_id, alias
document_labels     document_id, facet_id, facet_value_id
                        UNIQUE (document_id, facet_id)
spend_lines         document_id, amount, note, origin (extracted|manual)
line_labels         line_id, facet_id, facet_value_id
                        UNIQUE (line_id, facet_id)
payment_overrides   kind (MERGE|SPLIT), document_ids, created_at
charts              name, question_text, rule, default_grain,
                    default_split, display_currency, ordinal
```

Notes on three columns that are load-bearing:

- **`UNIQUE (…, facet_id)`** makes "one value per facet" a database guarantee,
  not a convention. This is the invariant `GROUP BY` depends on to avoid
  double-counting. `facet_id` is denormalised onto the label tables so the
  constraint can be expressed.
- **`facet_values.parent_id`** is nullable and unused at ship. It exists so that
  moving `category` to two levels later requires populating parents only — no
  schema migration, no relabelling, and existing rules keep working because a
  leaf value's identity never changes.
- **`facet_value_aliases`** lets one value be recognised by several surface
  forms — a vehicle by its registration plate, its marque, and the mojibake
  spelling of that marque. Aliases feed the labelling prompt and search.
- **A label's `facet_id` must agree with its value's facet.** `facet_values`
  carries a redundant `UNIQUE (id, facet_id)` so the label tables can hold a
  composite foreign key on `(facet_value_id, facet_id)`. Without it, a row can
  claim facet `scope` while pointing at a `category` value, and the `GROUP BY`
  invariant silently breaks.
- **`sum(lines) = amount_total`** is a `DEFERRABLE INITIALLY DEFERRED`
  constraint trigger, so a multi-row split inserts as one transaction rather
  than failing on the first line.

Documents gain `amount_kind` and `reference` (§8).

## 7. The facet vocabulary

### 7.1 Principle

**Tags inform the vocabulary; documents determine the labels.** The 771 existing
tags are used once, as evidence for which dimensions matter, and then discarded.
Every document is re-labelled from its own content against the new vocabulary.
Mapping a corrupt tag onto a clean value would launder a bad label into a
good-looking one.

**A dimension qualifies as a facet only if it is not already a column.** This
test alone removes roughly 40% of the existing vocabulary, which is deleted
rather than migrated (§2.3).

### 7.2 Proposed facets

Derived from the live tag distribution. Subject to revision during
implementation; the CRUD in §7.5 exists so revision is cheap.

| facet | values | nullable |
| --- | --- | --- |
| `category` | accountancy, tax, vehicle-service, ev-charging, insurance, healthcare, software, energy, housing, parking, fines, pension, banking, travel | no |
| `scope` | personal, business | no |
| `cost_type` | subscription, usage, one-off | yes |
| `vehicle` | one value per vehicle | yes |
| `property` | one value per address owned | yes |
| `person` | one value per household member | yes |

Roughly 20 values ship across the three impersonal facets, replacing 771 tags.
`vehicle`, `property` and `person` ship as facets with **no values**: theirs name
real vehicles, addresses and people, which must not enter a public repository, so
they are created at runtime.

Two judgement calls recorded:

- **`accountancy` and `tax` are separate categories.** Buying accounting advice
  is not the same spend as paying a tax assessment. Merging them would make the
  "accountancy fees per year" chart wrong.
- **`vehicle` is a facet, not a vendor tag.** It answers a question no column
  can: total cost of ownership per vehicle, across servicing, charging,
  insurance and tax. Its values carry registration plates as aliases.

- **`person` is a facet despite `recipients` existing**, which §7.1 requires
  justifying. `recipient` records who a document was *addressed to*; `person`
  records *whose cost it is*. They diverge in practice: on the live archive one
  household member is the recipient of 162 documents spanning household,
  business and another member's vehicle costs, while the second member is the
  recipient of only 6 documents but is named by tags 9 times. The lookup also
  holds third parties who are not household members at all. `person` is
  orthogonal to `scope` — a business cost is still attributable to someone.

`property` covers addresses owned. Place names that denote a workplace or a
correspondence origin are **not** property values and are dropped — a
distinction the tag list cannot make and the document can, which is §7.1 in
miniature.

**Related cleanup, in scope for layer A.** The `recipients` table has the same
drift as the tags: five separate rows spelling one person's name five ways,
covering 210 documents between them, plus two rows for one company.
The alias mechanism of §6 solves this identically, so recipient consolidation
rides along with the facet work rather than becoming separate follow-up.

### 7.3 The labelling pass

All documents go through the model in batches with the closed vocabulary in the
system prompt: title, summary, sender, kind, amount and an OCR excerpt in; one
value per facet out, plus a confidence and a one-line reason. Roughly 80k tokens
for the initial archive. Low-confidence results land in the existing review queue
rather than being guessed at.

New documents are labelled at ingest by the same path.

### 7.4 Preventing re-drift

The vocabulary is a **closed set**. The extractor receives the allowed values and
must select one or return `unknown`; it is structurally unable to invent an
eighth synonym for vehicle servicing. When it wants a value that does not exist
it returns `unknown` plus a *suggestion*, which queues for approval rather than
silently entering the vocabulary.

This single constraint is the difference between this design and the 771 tags.

### 7.5 Vocabulary CRUD

| operation | cost |
| --- | --- |
| rename a value's display label | free — labels reference `facet_value_id`, not text |
| add an alias | free |
| merge two values | cheap — re-point labels, retain the old key as an alias |
| create a facet or value | labelling pass for the new dimension only |
| split one value into two | requires a re-label of affected documents |
| delete | blocked while in use, or forces a re-label |

Every operation runs through a job that produces a **diff approved before it is
applied**, so no vocabulary edit can silently rewrite what charts have been
showing.

Introducing a facet or value also happens on demand: when a question cannot be
expressed in the current vocabulary, the system says so and proposes the new
facet or value rather than approximating.

## 8. Money facts and payment identity

### 8.1 `amount_kind`

Added to the extraction schema and to `documents`. Declares what a number means:

| value | summed? |
| --- | --- |
| `payment_due` | yes |
| `payment_made` | yes |
| `assessment` | yes |
| `coverage_limit` | no |
| `balance` | no |
| `estimate` | no |
| `none` | no |

This is the gate that stops an insurance coverage ceiling from ever entering a
spending total (§2.2).

### 8.2 `reference`

The document's own invoice, order or booking number, extracted in the same pass.
It is the strongest available evidence for payment identity and is date-independent.

### 8.3 Payment identity

Two documents describe one payment when any rule fires and the veto does not.

| # | rule | mode |
| --- | --- | --- |
| R1 | same `(sender, date, amount, currency)` | auto |
| R2 | same `sender` + same non-null `reference` | auto, **any date gap** |
| R3 | same `(sender, amount, currency)`, **complementary `amount_kind`** (`payment_due` <-> `payment_made`), later date >= earlier, gap <= 60 days | auto |
| R4 | same sender and amount, same `amount_kind`, or gap > 60 days | **proposed** in the drill-through panel, never applied automatically |
| VETO | both documents carry a `reference` and they differ | never merge, even if R1 or R3 fires |

**Why R3 is safe.** An invoice and its receipt are never the same kind of amount:
one is `payment_due`, the other `payment_made`. Two genuinely separate purchases
of the same value are always the *same* kind. Complementarity is what separates
the two cases without relying on a date tolerance, and no date tolerance can work
alone — the archive contains two same-amount purchases four days apart that must
stay separate, and a probable invoice/receipt pair fifteen days apart that should
merge.

Verified against every ambiguous case in the live archive: all 20 known duplicate
groups collapse correctly, and every same-amount recurring charge stays separate.

`amount_kind` correctness is therefore load-bearing. It gets its own extraction
validation and enters the review queue when the model is unsure. It is a simpler
judgement than `kind`, which is already known to misclassify receipts as invoices
(§2.4).

**Known limits, stated rather than hidden:** a partial payment (one invoice
settled by two smaller receipts) does not match on amount; an invoice billed in
one currency and paid in another does not match either. Both surface as R4
proposals.

### 8.4 Spend lines

`sum(lines.amount) == document.amount_total` is enforced at write. A document
either has no lines (the common case, one synthetic row in `spend_facts`) or a
complete set. Lines are created two ways: extraction reads the document's own
itemisation, or the owner allocates by hand.

## 9. The chart engine

### 9.1 A chart is a saved question

```
name          "AI subscriptions"
question      "money I spend on AI tools and subscriptions"
rule          category = software AND cost_type IN (subscription, usage)
grain         month
split         cost_type
currency      EUR
```

Created by typing the question in plain language. The model drafts a rule against
the current vocabulary and proposes the axes that actually vary within the
result. The resolved documents and total are shown **before** saving.

### 9.2 Two orthogonal axes

The time axis and the split axis are independent, so drilling one never disturbs
the other and **the total is invariant across split changes**:

```
AI subscriptions - monthly - split by cost_type      EUR 412 in August
   subscription   EUR 212
   usage          EUR 200

same chart, split by sender                          EUR 412   <- unchanged
   Vendor A       EUR 312
   Vendor B       EUR 100
```

`sender` is available as a split axis alongside every facet, at no cost — it is a
real column. Clicking a legend entry isolates or excludes it, so a
single-provider view requires no new chart.

### 9.3 Currency

Amounts convert to the chart's display currency at the rate on **each document's
own date**, via the existing `library.fx` (date-aware, base USD, returns `None`
when a rate is unknown). Unconvertible amounts are reported in the footer, never
dropped and never counted 1:1.

### 9.4 Nothing is excluded silently

Every chart carries a footer accounting for everything its rule touched but its
total did not:

```
EUR 1,204.18 across 14 payments from 17 documents

  excluded from the total
     2 coverage_limit   EUR 20,000.00
     1 estimate            EUR 450.00
  needs attention
     3 documents uncategorised   EUR 89.20
```

The last line is the most important. A document the model failed to label matches
no rule, so without this it disappears from every chart with no way to notice.
Reporting uncategorised money inside the chart whose date and currency window
contains it turns the archive's worst failure mode into a visible task.

### 9.5 Drill-through

Clicking a bar opens a persistent panel — not a tooltip — listing that cell's
payments, each expandable to its documents, with the facet editor and the
`[split]` / `[merge]` controls inline. Every number reaches its source documents
in two clicks, and a correction is made where the problem was noticed.

### 9.6 API

One resource, replacing the whole `/api/charts` + `/api/series` surface:

```
GET    /api/charts                saved questions
POST   /api/charts/draft          question text -> proposed rule, axes, preview
POST   /api/charts                save
GET    /api/charts/{id}/data      ?grain&split&from&to&currency
GET    /api/charts/{id}/cell      ?period&split_value -> payments -> documents
PATCH  /api/charts/{id}
DELETE /api/charts/{id}
```

List endpoints keep the existing `limit <= 100` cap.

## 10. The `/charts` view

Chart mark specification, palette and tick treatment are settled at
implementation time under the `dataviz` skill. This section fixes structure and
interaction only.

### 10.1 Board

One card per saved question, sized to be readable, ordered by the owner (pinned
or dragged) and never by document count. Each card shows a headline figure for
the current period with a comparison, a compact chart, and its split legend.
Cards show data; edit and delete live in an overflow menu and in the workspace.

**"All spending" is a seeded default** — an empty rule split by `category`. It is
the aggregate view the current page lacks entirely, and costs nothing extra
because it is the same engine.

### 10.2 Workspace

Full canvas, with a single toolbar row: grain (week/month/quarter/year), range,
split, display currency. Below the chart, the §9.4 footer. Clicking a bar opens
the §9.5 panel.

### 10.3 What this fixes

1. **Bars are periods, not documents.** The x-axis is uniform buckets, so nothing
   is 2px wide because two invoices landed three days apart.
2. **The range filters the data rather than clamping the axis**, so headline
   figures and the chart can never disagree (§2.5).
3. **Split values carry a stable colour**, assigned to the facet value and stored,
   so a provider is the same colour in every chart it appears in.
4. **Legend entries isolate or exclude on click.**
5. **Cards show data, not six controls each.**
6. **Drill-through is a click and a persistent panel.**

### 10.4 Empty state

No charts yet produces *proposed questions* derived from the labelled archive —
"15 documents in `software` over 3 months; chart it?" — which the owner accepts
or ignores. This is the old "candidates" idea pointed at the right thing: it
proposes questions worth asking, rather than creating series that persist as
noise.

### 10.5 Responsive

The workspace toolbar and drill-through panel use **container queries**, not
viewport breakpoints: content sits in a viewport-minus-sidebar column, which has
caught this app out twice. The panel becomes a bottom sheet below the tablet
breakpoint. Measured in a browser, not assumed.

The panel lists every payment as text, so it doubles as the non-visual
representation of the chart rather than requiring a separate implementation.

## 11. Migration and rollout

Build order: A and B in parallel, then C, then D, then E.

```
A  facets          schema + CRUD, seed vocabulary, label archive,
                   review queue, search filters
B  money facts     amount_kind + reference in extraction, backfill,
                   payment rules, spend_lines, spend_facts view
C  chart engine    rule, query, API
D  view            board + workspace
E  removal         delete the old series stack
```

**Every migration is additive.** Nothing is dropped until the replacement has run
against the live archive. The new view ships behind a setting; once flipped, the
old stack is deleted in a **separate follow-up PR**: `series.py`,
`series_insight.py`, `series_match.py`, `semantic_membership.py`,
`api/series.py`, the series routes in `api/charts.py`, `SeriesChartTile.vue`,
`SeriesChartView.vue`, `DocumentSeriesTrend.vue`, and the authored-series,
suggestion, exclusion, insight and membership-override tables.

Backfill cost is trivial at this scale: the full archive through labelling plus
the amount-bearing subset through amount re-extraction, well under one dollar.

**Nothing migrates from the current charts.** The existing tiles, authored
series, smart group, suggestions and odd-ones-out are discarded. None encodes a
decision worth preserving; the one authored group is re-asked as a question.

**Deployment specifics** this repository has been caught by: any file read at
runtime from inside the image needs a `Dockerfile COPY` (committing it ships
nothing), and `make deploy` requires CI's `promote` job to have actually
succeeded.

**Documentation**: new `docs/charts.md`; `docs/smart-groups.md` archived with a
superseded header; the series sections of `docs/architecture.md` and
`docs/api.md` rewritten; a journal entry.

## 12. Failure handling

| failure | response |
| --- | --- |
| model returns a value outside the closed set | rejected; stored as `unknown` plus a queued suggestion. The vocabulary is never auto-extended |
| model is unsure | `unknown` plus review queue, never a guess — a confident wrong label silently moves money between charts |
| rule references a deleted facet value | chart renders an **error naming the value**, not an empty chart |
| `sum(lines) != amount_total` | rejected at write; document flagged |
| missing FX rate | amount reported as unconvertible in the footer; never dropped, never counted 1:1 |
| payment merged wrongly | visible as a group in the drill-through panel; `[split]` stores an exception |
| money with no category | reported in the footer of every chart whose date and currency window contains it, plus a global uncategorised queue |
| labelling job fails midway | idempotent per document; re-runnable; partial results are valid |

The through-line: **every exclusion is reported at the point it affects a
number.** The current system narrows three times in silence, which is why a wrong
chart is indistinguishable from a right one.

## 13. Testing

Three areas carry real risk and each gets a specific treatment.

**Payment merge rules** get a table-driven regression suite built from the
genuinely ambiguous shapes found in the live archive: two same-amount purchases
four days apart that must stay separate; an invoice and a differently-kinded
document six days apart that must merge; four same-amount invoices where only the
same-day pair merges; a reference-matched pair months apart. Entered as **shaped
fixtures with invented senders and amounts** — this repository is public.

**Labelling** gets an eval with a hand-checked gold set and a per-facet
precision/recall floor, in the shape of the existing Ask recall baseline —
including its lesson: compute what a **random labeller** scores first, so the
floor discriminates rather than passing on chance.

**The `spend_facts` view** — label inheritance, the payment collapse, the
`UNIQUE (…, facet_id)` constraints — **is prototyped against Postgres before the
implementation plan is written**, not specified from reasoning. Direct lesson
from the last two feature plans in this repository: twelve defects all originated
in plan code that was never executed, while the two parts prototyped against a
real database landed clean first time.

Standing constraints:

- Full backend suite and `ruff format --check` over the whole repository,
  including `migrations/`, before merge.
- E2E assertions written for all three viewport projects; the workspace toolbar
  and drill-through panel collapse below the tablet breakpoint.
- Test fixtures scoped so they do not pollute the shared serial backend's
  document ordering.
- No `except -> pytest.skip` guards; they read as green while hiding breakage.
- Any new `*_model` setting needs a matching `MODEL_PRICING_USD_PER_MTOK` row or
  the app refuses to boot.
- Backend coverage runs with `concurrency = ["greenlet", "thread"]`.

## 14. Open questions

1. **`category` granularity.** Fourteen values is a lot for one facet and may
   want a second level. Shipping flat, with `parent_id` present but unused, so
   hierarchy is a data change rather than a migration (§6).
2. **Does `property` earn its keep?** It survives only if the addresses are ones
   the owner holds. Low cost either way; deletable via §7.5.
3. **Line extraction scope.** Default: extraction proposes spend lines **only
   when a document's items cross a facet boundary** — a mixed-scope invoice, or
   one carrying both a subscription and a usage charge. Proposing lines for
   every itemised document is more uniform but produces far more rows to review
   for no analytical gain, since lines that all share a label sum to the
   document total anyway. Widen only if the narrow rule is observed to miss
   real splits.
