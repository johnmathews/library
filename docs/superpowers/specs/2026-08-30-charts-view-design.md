# The spending view

**Status:** design (2026-08-30). Plan 4 of the charts redesign, split into
three (§1). Supersedes nothing; extends
[the charts redesign design](2026-08-28-charts-redesign-design.md) §10.

> **Note on examples.** This repository is public. Every sender name, amount and
> document title below is invented.

Plan 4 of the charts redesign: the user-facing surface for the chart engine
shipped in plan 3 ([the chart engine](../../charts.md), PR #121).

The engine answers "how much am I spending on X per period" across ten routes at
`/api/spending` and has no UI at all. This document designs that UI, and the
five backend additions it turns out to need.

Binding on this work: the redesign spec's
[§9.4, §9.5 and §10](2026-08-28-charts-redesign-design.md). §10 fixes structure
and interaction only — chart marks, palette and tick treatment are settled at
implementation time under the `dataviz` skill, within the semantic constraints
§4.4 below fixes.

## 1. Three plans, not one

Six decisions taken at design time (§2) each chose the fuller option, and
together they span two subsystems that share nothing but an API client. They
ship as three plans:

| | scope | depends on |
| --- | --- | --- |
| **4a** | backend: chart-by-id, split labels and colours on the wire, footer drill, facet counts, colour columns and their CRUD | the shipped engine only |
| **4b** | the `/charts` view: board, workspace, chart, footer, drill panel, draft flow, empty state, reordering | 4a deployed |
| **4c** | the facet-vocabulary panel: rename, alias, merge, delete, colour | 4a deployed |

**4a merges and deploys before 4b starts**, so 4b is written against a frozen,
live API rather than against a plan's description of one. This is a direct
reading of plan 3's post-mortem: twelve defects across the two preceding feature
plans all originated in plan code that was never executed, while the two parts
prototyped against a real database landed clean the first time.

4c is independent of 4b. Either order after 4a.

## 2. Decisions taken before design

Six questions were open when this work started. Each is recorded here with the
option chosen, because each closed off alternatives that a later reader would
otherwise re-open.

### 2.1 The view replaces `/charts` now

`/charts` and `/charts/:seriesId` belong to `ChartsView.vue` and
`SeriesChartView.vue`, which plan 5 deletes along with the rest of the series
stack. The new board takes `/charts` in this plan rather than waiting.

That is affordable because **a series id is never a bare integer**:
`encode_series_id` produces `{sender_id}-{kind_id}-{currency}` and
`encode_authored_series_id` produces `a-{id}`. So the two route shapes coexist,
declared digit-constrained-first, exactly as `/ask/new` already precedes
`/ask/:threadId(\d+)`:

```
/charts                    the new board          (ChartsView.vue deleted)
/charts/:chartId(\d+)      the new workspace      (new)
/charts/:seriesId          SeriesChartView.vue    (survives to plan 5)
```

Keeping `SeriesChartView.vue` alive matters for a reason that is easy to miss:
`DocumentSeriesTrend.vue` on the document detail page renders a
`SeriesChartTile` whose `detailHref` is `/charts/{seriesId}`. Deleting that view
in this plan would leave a dead link on a page this plan does not touch.

So plan 4b's removal is exactly: `ChartsView.vue`, its unit spec, and the e2e
specs that drive the old board. `SeriesChartTile.vue`, `ChartControls.vue`,
`useChartsGrouping`, `useChartsTimeframe` and `DocumentSeriesTrend.vue` all stay
— they have a live consumer on the detail page — and plan 5 removes them with
the backend.

### 2.2 "All spending" is created by the empty state

The `charts` table is empty in production. §10.1 calls "All spending" a seeded
default; it is seeded by **the owner clicking it once** in §10.4's empty state,
where it is the first and pinned proposal.

Rejected: an Alembic data migration, which would hardcode a display currency
nobody chose, would appear in every test database against the shared serial
backend's ordering assertions, and — being a one-shot — would make "seeded
default" mean "seeded once, and gone if deleted". Also rejected: a CLI seed
command, which is a code path with no user-facing trigger, the shape this
repository has shipped-but-unwired twice.

The click POSTs through the ordinary `POST /api/spending`, so the seed exercises
the same save path every other chart uses.

### 2.3 Split values are resolved by the API

`split=sender` emits `CAST(sf.sender_id AS text)`, and a facet split emits the
facet *value key* — so today's legend reads `41` and `ev-charging` rather than a
name and `EV charging`. Resolution happens **in the router**, on the wire (§3.2).

Rejected: a client-side join against `/api/senders` and `/api/facets`. It works,
but it puts "a sender split means an id, a facet split means a value key" into
TypeScript as a second copy of engine semantics. Also rejected: resolving inside
`charts/query.py`, which would make `split_value` a name and break `/cell`'s
round-trip on any renamed or duplicated sender.

### 2.4 All six footer buckets drill through

§9.4 calls uncategorised money "a visible task", and today it is a number with
nowhere to go: no route lists the documents behind a footer bucket, and the
document list has no "facet is unset", no `amount_kind IS NULL` and no
"summable but undated" filter to link into.

One new route lists any bucket (§3.3). It covers `unaccounted` too, which should
always be empty — a bug signal you cannot open is not a signal.

### 2.5 Colour is a nullable override over a derived palette slot

§10.3 wants a split value to carry a stable colour "assigned to the facet value
and stored". `facet_values` has no such column and `senders` has no colour
storage at all.

Both get a **nullable** `colour`. When it is null the renderer derives a palette
slot deterministically from the value's key (or the sender's id), so every
legend is stably and accessibly coloured from the first render with no colour
ever having been set, and the migration invents no data. A stored value is the
owner's deliberate override.

Rejected: `NOT NULL` with the migration assigning slots by ordinal, which makes
the migration own the palette, needs a colour chosen at every future insert, and
requires a second data migration to restyle. Rejected: storing only on
`facet_values`, which leaves the sender split — the axis most likely to want a
recognisable colour — as the one that cannot have one.

### 2.6 The vocabulary panel is its own plan

The colour picker needs somewhere to live, and there is **no facet-vocabulary
management UI in the frontend at all**. `FacetEditor.vue` sets one document's
labels; the six vocabulary CRUD routes — create facet, create value, rename,
alias, merge (with a `dry_run` preview), delete — have no client whatsoever.

That panel is plan 4c. It fills a genuine hole against an API that already
ships, and it is comfortably the size of the board itself, so folding it into 4b
would rebuild the two-subsystems-in-one-PR shape §1's split exists to avoid.

Colour therefore cannot be *set* until 4c ships. Because of §2.5's fallback,
every legend is still correctly and stably coloured from 4b onward.

## 3. Plan 4a: the backend surface

**No change to `charts/query.py`, `charts/rule.py` or `charts/draft.py`.** Every
addition is in the router, or is additive to `charts/footer.py`. The two
invariants the engine holds — the total is invariant across split changes, and
the drill-through sums to the bar — are untouched, and nothing here introduces a
second path to a number the engine already computes.

### 3.1 `GET /api/spending/{id}`

Returns `ChartOut`. `_load_chart` already exists and already 404s by id; this is
five lines. The workspace at `/charts/:chartId` loads through it instead of
paging the list looking for one row.

### 3.2 Split values carry their label and colour

`DataOut` gains one field:

```python
class SplitValueOut(BaseModel):
    """One bucket of the split axis, resolved for display.

    `value` is exactly what `/cell` must be sent back — the sender id as text
    or the facet value key, never the label. Resolution is a display concern
    and lives here rather than in the engine (docs/charts.md §4).
    """

    value: str | None
    label: str
    colour: str | None


splits: list[SplitValueOut]
```

A **list, not a mapping**. The unlabelled bucket is a real bucket whose
`split_value` is `null`, and `null` cannot be a JSON object key — it is also the
bucket whose label a client is least able to invent, since it means "no value
for this facet" under a facet split and "no sender" under `split=sender`. The
API names it.

`CellOutBody` gains the same `label` and `colour` for its one bucket, so a
drilled panel can title itself without re-reading `/data`.

One resolver serves both routes — the same discipline `_SharedArgs` already
enforces for the query arguments, for the same reason. It reads facet value
labels from the vocabulary the router **already loads uncached** for rule
validation (so they cost nothing extra), and senders from one
`SELECT id, name, colour FROM senders WHERE id = ANY(...)` over the ids actually
present in the result.

A `split_value` whose sender row or facet value has since been deleted resolves
to the raw string, never to a `KeyError`. Facet values are deletable at runtime
and a saved chart can rot; a rotted legend entry is a legible defect, a 500 on
every chart in range is not. This is the same failure the `sorted()` over a
`None` currency caused before it was fixed (docs/charts.md §5).

### 3.3 `GET /api/spending/{id}/footer/{bucket}`

Lists the documents behind one footer bucket.

```
GET /api/spending/{id}/footer/{bucket}?from&to&currency&amount_kind&limit&offset
```

`bucket` is one of `excluded`, `unclassified`, `uncategorised`, `undated`,
`unaccounted`, `unconvertible`; anything else is a 422 naming it.
`amount_kind` selects one group out of `excluded`, which is a *list* of groups
(one per kind) rather than a single figure. `limit` ≤ 100, `offset` — the
repository's standing cap.

**This needs no new SQL and no second `CASE`.** `footer.py`'s `_CLASSIFY_SQL`
already selects rows — `document_id, amount, currency, date, amount_kind,
bucket` — and `chart_footer` aggregates them in Python, accumulating
`_Group.documents` as a `set[int]` of distinct canonical documents. The new
function calls the same statement builder and filters the same rows by bucket.

That is the whole point of doing it this way. The classification stays in one
place, so the count and the list cannot disagree; and the invariant

> the drill list's length equals the footer's `documents` for that bucket

is true by construction and assertable directly, which is §8's "the panel must
add up to the bar" one level down. A second query restating the `CASE` would
give a list that fails open — the failure mode this repository has already been
caught by, where a comparison test passes because neither copy exercises the
branch that differs.

Pagination is applied in Python over the same materialised row set
`chart_footer` already builds, rather than by pushing `LIMIT` into the
statement, because a second statement shape is a second thing to keep in step.

**Open to execution, not to reasoning:** whether a document split across spend
lines can contribute two rows to one bucket. `_CLASSIFY_SQL` selects per
`spend_facts` row and `_Group.documents` is a set, so the *count* is already
distinct-by-document; whether the *list* must therefore deduplicate is settled
by prototyping against Postgres before the 4a plan is written, not by argument.

### 3.4 `GET /api/facets/counts`

Per facet value: `documents`, `first_date`, `last_date`. This is what §10.4's
empty state needs to say "15 documents in `software` over 3 months; chart it?".

Counted over **`spend_facts`**, not `document_labels`. A proposal to chart a
value with no money behind it is precisely the noise §10.4 says it is replacing:
the old "candidates" idea failed by proposing series that persisted as noise,
and proposing a chart of a label the archive has no amounts for repeats it in a
new place.

A separate route rather than counts added to `GET /api/facets`, so
`DocumentFilterBar` — which loads the vocabulary on every document list render —
does not start paying for an aggregate it never reads.

### 3.5 Migration 0037: `colour`

Nullable `colour` on `facet_values` and on `senders`, each with an **explicit**
`CheckConstraint` on `^#[0-9a-fA-F]{6}$`. Explicit because a declarative type
alone would not enforce it — `sa.Enum(native_enum=False)` creates no check
constraint, and this column would otherwise accept any string at all.

Wired through:

- `PATCH /api/facets/{facet_key}/values/{value_key}`, which today accepts only
  `label`. `colour` is optional and genuinely nullable, so "clear it" and "do
  not touch it" cannot both be `None` in one field — they are told apart by
  `model_fields_set`, exactly as `ChartPatch.default_split` already is.
- `PATCH /api/senders/{id}` — **new**; `taxonomy.py` is list-only today.
- `FacetValueRef` on `GET /api/facets` and `SenderOption` on `GET /api/senders`
  gain `colour`.

### 3.6 Testing 4a

- The footer drill and the counts route are **prototyped against real
  Postgres** before their plan tasks are written (§3.3's open question is the
  specific reason).
- Every new test gets a mutation check: break a `CASE` branch and confirm the
  aggregate *and* the list both move; delete the sender resolution and confirm
  the legend test goes red rather than passing on raw ids.
- Fixtures are shaped and invented. **This repository is public** and the live
  facet vocabulary contains address-shaped and vehicle-shaped values; no real
  sender name, amount, address or registration reaches a fixture, a doc, a
  commit message or a PR body.
- The full backend suite and `ruff format --check` over the whole repository,
  `migrations/` included, before merge.

## 4. Plan 4b: the view

### 4.1 Shape

```
frontend/src/api/spending.ts          all ten engine routes + 4a's four
frontend/src/views/
  SpendingBoardView.vue               /charts
  SpendingWorkspaceView.vue           /charts/:chartId(\d+)
frontend/src/components/spending/
  SpendingCard.vue                    one board card
  SpendingChart.vue                   the mark, under `dataviz`
  SpendingLegend.vue                  isolate / exclude / colour swatch
  SpendingFooter.vue                  §9.4, all eight fields
  SpendingDrillPanel.vue              §9.5, and the footer's drill
  QuestionDraft.vue                   the /draft flow
  SpendingEmptyState.vue              §10.4
```

`spending.ts` follows `payments.ts` and `facets.ts`: plain typed functions over
`apiFetch`, no store. The board holds its own state; there is no cross-view
state to justify a Pinia store. Every list call caps `limit` at 100 **and
asserts it in a unit test** — a mocked fetch does not enforce the server's cap,
so the assertion is the only thing that does.

### 4.2 Board

One card per saved chart, ordered by `ordinal` then name — never by document
count, which §10.1 calls out explicitly.

Each card shows: the chart's name; a headline figure with a comparison; a
compact chart; the split legend. Edit and delete live in an overflow menu, not
on the card face — §10.3 #5 is that cards show data, not six controls each.

**The headline is the most recent *complete* bucket**, labelled by name
("August"), compared with the bucket before it. The current partial bucket is
drawn on the chart but is not the headline: a partial month against a full one
is the comparison that is always wrong and never looks it.

Reordering is **both** drag-and-drop and a keyboard path. Drag persists
`ordinal` via `PATCH`; "move up" / "move down" in the overflow menu does the
same thing and is what the e2e suite asserts on all three viewport projects,
because pointer-drag on mobile-webkit is the single most flake-prone thing this
suite could contain. The drag path is asserted on chromium only. The keyboard
path is not a fallback bolted on for tests — it is the accessible path, and it
is the reason `ordinal` cannot end up as a column nothing writes.

### 4.3 Workspace

A single toolbar row — grain, range, split, display currency — over the chart,
with §9.4's footer below it, and §9.5's panel opening on a bar click.

**Container queries, not viewport breakpoints.** The content sits in a column
that is viewport-minus-sidebar, which has caught this app out twice (the `/ask`
pane at 332px inside a 1024px viewport; the `PageHeader` toolbar merging at 1280
collapsed and stacking at 1280 expanded). The toolbar and the panel use
`@container`, are measured in a browser rather than assumed, and each guard is
proved to go red before it is trusted. Below the tablet breakpoint the panel
becomes a bottom sheet (§10.5).

Mosaic conventions throughout: native inputs, the shared `.form-*` and `.btn`
classes, uppercase-xs labels, `items-end gap-3` rows, the violet accent,
`data-testid` on everything the e2e suite touches. Assertions are on DOM
outcomes, never on class names — Tailwind's utilities layer beats
`utility-patterns.css` regardless of specificity.

### 4.4 The chart

The mark specification, palette and tick treatment are produced by 4b's first
task **under the `dataviz` skill**, as §10 requires. Four semantic constraints
are fixed here because they are not aesthetic choices:

1. **Stacked bars.** The stack height is the total, and that the total is
   invariant across split changes is the feature's central promise (§9.2).
   Grouped bars would draw the split and leave the promise undrawn.
2. **The y-axis always includes zero.** A split value whose net is negative —
   a refund exceeding the payments in its bucket — draws below the baseline, and
   an axis that starts elsewhere hides the sign that §8.1.1 exists to carry.
3. **Uniform time buckets.** The x-axis is periods, not documents. §10.3 #1 is
   the whole reason the old view was replaced: nothing is 2px wide because two
   invoices landed three days apart.
4. **Colour comes from `SplitValueOut.colour` when set, and from a
   deterministic palette slot derived from `value` when it is null.** Same
   value, same colour, in every chart it appears in (§10.3 #3). Two values
   colliding on a slot within one rendered chart are de-collided at render, so
   the collision is visible rather than silent.

The range filters the data — `from` and `to` go to the API — rather than
clamping the axis, so the headline and the drawing can never disagree
(§10.3 #2, §2.5 of the redesign spec).

### 4.5 The footer

All eight fields, always rendered, in §9.4's three blocks:

```
<total> across <payments> payments from <documents> documents
  including <refund_count> refund(s) netted off   -<netted_refunds>

  excluded from the total
     <excluded[]>, one line per kind
  needs attention
     <unclassified>  <uncategorised>  <undated>  <unaccounted>
  could not be converted
     <unconvertible[]>
```

Four things the renderer must get right, each of which corresponds to a defect
the engine's own review found:

- **A refund is netted, never excluded.** It is *in* the total and it lowers it;
  putting it under "excluded from the total" would read as money the chart
  ignored, which is the opposite of what happened.
- **`unclassified` and `uncategorised` sit under *needs attention*, not under
  *excluded from the total*.** Excluded means correctly not spending; an
  undecided `amount_kind` or a missing label means not yet decided.
- **`unaccounted` should always be empty.** When it is not, the classification
  has a hole and this is the money in it — so it renders under *needs
  attention* with that meaning, not as an ordinary category.
- **`unconvertible` always renders `documents` beside `amount`, and a `null`
  currency is labelled and sorted last.** An unconvertible payment and an equal
  unconvertible refund net to `0.00` across two documents, which without the
  count reads as "nothing missing" while two documents are unrepresented.

**`documents` means three different things in one rendered footer** —
`DataOut.documents` counts payment-group members, a footer group counts
canonical rows, and merged `unconvertible.documents` is a summed upper bound.
Each is correct; they are not a partition of the archive and the UI must never
add them together or present them as parts of one whole.

Every count is a button opening §4.6's panel on 4a's bucket route.

### 4.6 Drill-through

A bar click opens a persistent panel — not a tooltip — listing that cell's
payments, each expandable to its documents, with the facet editor and the
split/merge controls inline. `FacetEditor.vue` and `PaymentGroup.vue` already
exist and are reused rather than reimplemented.

**The panel sends `/data`'s echoed arguments verbatim.** `DataOut` echoes the
*resolved* `grain`, `split`, `currency`, `since` and `until` for exactly this
purpose; sending them back unmodified is what makes the panel provably answer
the same question the bar did. The `period` is the cell's own, never a
user-picked date — an off-boundary `period` is a 422 naming the correct
boundary, and the client surfaces that rather than showing an empty panel.

**What the panel may and may not sum.** `CellOutBody.payments` are apportioned
by largest remainder to sum exactly to `CellOutBody.total`, so rendering them as
a list that adds up is safe by construction. `documents[].amount` must never be
summed to reconstruct the total: a merged pair doubles it, a group member
outside the cell's period is still listed, and an unconvertible member is listed
but not counted. `CellPaymentOut.total` is the only number that matches the bar.

`CellDocumentOut.amount` and `.currency` are **optional** and the panel must
render a document without them. A hand-made `MERGE` override can pull an
amountless document into a group, and that is precisely the merge this panel
exists to expose.

The panel lists every payment as text, so it doubles as the non-visual
representation of the chart rather than needing a separate one (§10.5).

### 4.7 Legend

Swatch, label, value. Click isolates; modifier-click excludes (§10.3 #4).

Isolation is a **client-side display filter**. The headline stays the invariant
total the API reported and a separate line names the current selection. An
isolate that silently rewrote the headline would break the one promise §9.2
makes — and it would do it in the direction that looks most plausible.

In 4c, the swatch is also where a colour is set: the correction is made where
the problem is noticed, which is the same rationale §9.5 gives for putting the
facet editor inside the drill panel.

### 4.8 Draft flow

An "ask a question" input on the board posts to `/api/spending/draft` and
renders one of **three** states. Conflating the last two is the failure §7.5
names:

| state | wire | render |
| --- | --- | --- |
| expressible | `expressible: true`, `rule` and `preview` present | rule, proposed split, preview, save enabled |
| partly expressible | `expressible: false`, `rule` and `preview` **present** | the same, **labelled an approximation**, plus `unknown_terms`; save enabled with the caveat shown |
| collapsed | `expressible: false`, `rule` and `preview` **null** | `unknown_terms` and the message only. **No preview.** Save disabled |

The collapsed case is the one that matters. Every clause was dropped, so the
rule is `Rule(all=[])`, which matches every row — previewing it would answer a
narrow question with the whole archive's total, the most confidently wrong
answer this feature can give. `unknown_terms` is model-authored text; it is
already capped in count and length server-side, and it renders as text, never as
markup.

### 4.9 Empty state

No saved charts renders §10.4's proposals: **"All spending" first and pinned**
(an empty rule split by `category`), then proposals built from
`GET /api/facets/counts` — the values with the most documents, each shown with
its count and date span. Accepting one POSTs it; ignoring them costs nothing,
which is the difference from the old candidates idea that created series which
persisted as noise.

### 4.10 Removal

`ChartsView.vue` and `views/__tests__/ChartsView.spec.ts` are deleted, and the
e2e specs that drive the old board (`charts.spec.ts`, `charts-layout.spec.ts`,
and the `/charts` portions of `smart-groups.spec.ts`) are deleted or rewritten
against the new view.

Nothing else. Per §2.1, `SeriesChartView.vue`, `SeriesChartTile.vue`,
`ChartControls.vue`, `DocumentSeriesTrend.vue` and the two charts composables
all have a live consumer on the document detail page and are plan 5's to remove,
with the backend they read.

### 4.11 Testing 4b

- E2E assertions hold on **all three viewport projects** — chromium 1280,
  mobile-webkit 375, tablet-webkit 656 — and the toolbar and panel collapse
  below the tablet breakpoint is asserted, not assumed.
- Container-query guards are proved to go red before being trusted.
- Fixtures are scoped so they do not pollute the shared serial backend's
  document ordering: a fixture carrying a `document_date` reorders the dashboard
  and breaks specs that click the first tile.
- No `except -> pytest.skip` guards, and no `isVisible()` on a `v-show` element.
- Every unit test gets a mutation check. Several suites in this repository have
  passed with the feature under test entirely disabled.

## 5. Plan 4c: the vocabulary panel

A new route under the settings navigation listing every facet and its values
with the counts from §3.4, offering the six operations whose routes already
ship: create a facet, create a value, rename a value's label, add an alias,
merge one value into another, delete an unused value. Plus `colour`, on facet
values and on senders.

Two things it must carry from §7.5 of the redesign spec:

- **Merge previews before it applies.** `POST .../merge` already accepts
  `dry_run` and answers with the number of labels that would move; the panel
  shows that count and requires a confirmation. No vocabulary edit silently
  rewrites what charts have been showing.
- **Delete is blocked while in use.** The route answers 409 with the reason; the
  panel renders it rather than a generic failure.

The colour input is restricted to the palette rather than being a free hex
field. A free field lets the owner choose a colour invisible in dark mode or
indistinguishable from its neighbour, and nothing in the system could prevent
it. The column still stores hex (§3.5) — what is constrained is the choice, not
the storage.

## 6. What this plan does not do

- **It does not delete the series stack.** That is plan 5, together with the
  `/api/charts` → `/api/spending` rename and the `/charts/:seriesId` route.
- **It does not add an archive-wide backlog.** `unclassified` stays
  window-scoped (docs/charts.md §13): a document with an undecided `amount_kind`
  outside every chart's range is still counted nowhere. §3.3's route lists what
  a chart's window contains, not what the archive holds.
- **It does not set `amount_kind` by hand.** There is still no route for it, and
  adding one belongs with issue #125's vocabulary surfaces.
- **It does not address issues #124 or #126.** #124 item 3 — a skipped
  `amount_total` write being invisible to the owner — touches the document
  timeline, not this view.
