# The facet vocabulary panel

**Status:** design (2026-08-30). Plan 4c of the charts redesign. Supersedes
nothing; implements §5 of [the spending view](2026-08-30-charts-view-design.md)
under the rules of [the charts redesign](2026-08-28-charts-redesign-design.md)
§7.5.

> **Note on examples.** This repository is public, and the live vocabulary holds
> address-shaped and vehicle-shaped values. Every facet value, sender name and
> count below is invented.

The six vocabulary CRUD routes, the three suggestion-queue routes and the two
colour routes all ship, are deployed, and have **no client whatsoever**.
`FacetEditor.vue` sets one document's labels; `frontend/src/api/facets.ts`
wraps two read routes and `updateDocumentLabels`. Nothing in the application can
create a facet, create a value, rename one, alias one, merge one, delete one,
set a colour, or answer the labeller when it asks for a value the vocabulary
does not have.

This plan builds that client. It is almost entirely frontend; §3 is the single
backend addition, and §3 exists because the count the panel would otherwise
display is not the count its operations act on.

## 1. Where it lives

A new top-level route **`/vocabulary`**, with a sidebar entry directly above
Settings, and a second route `/vocabulary/:facetKey/:valueKey/merge` for the
merge confirmation page (§6).

Rejected: a seventh tab in `SettingsView.vue`. That file is 57k across six tabs
and every one of them is a *preference* — dashboard fields, appearance,
ask profile, notifications, email triage, LLM backend. The vocabulary is data
the archive is classified by, not a display preference, and putting it there
would grow the largest file in the frontend by a third.

Rejected: a tab in `/admin` beside Metadata, which is the nearest neighbour by
function — `AdminMetadataPanel` already manages senders, recipients and kinds
with a rename/merge/delete shape. But `/admin` is admin-gated by the router
while `/api/facets/*` and `PATCH /api/senders/{id}` are authenticated at include
level and not admin-gated. Putting the panel there would restrict a capability
the API grants every authenticated user, and would bury a chart-authoring tool
inside the administration console.

Spec §5's phrase "under the settings navigation" is read as *reachable from the
same region of the sidebar*, which the placement above Settings satisfies.

### 1.1 Three tabs

Tab selection is local state with no sub-routes, the pattern `AdminView.vue`
already uses (`role="tablist"`, `aria-selected`, `data-testid="vocab-tab-*"`).

| tab | panel | reads | writes |
| --- | --- | --- | --- |
| Facets | `FacetsPanel.vue` | `GET /api/facets`, `GET /api/facets/counts`, `GET /api/facets/label-counts` | create facet, create value, rename, alias, merge (via §6), delete |
| Senders | `SendersPanel.vue` | `GET /api/senders` | `PATCH /api/senders/{id}` |
| Suggestions | `SuggestionsPanel.vue` | `GET /api/facet-suggestions` | accept, dismiss |

Senders are a chart split axis exactly as a facet is, and need the same picker,
but a sender has **only** a colour here: its name is derived from ingested
documents, and renaming, merging and deleting one are admin taxonomy operations
that already have a home. They are a separate tab rather than a section below
the facets because `GET /api/senders` is unpaginated and returns every sender
ever ingested, which on a real archive is plausibly an order of magnitude more
rows than the ~25-value vocabulary that is this panel's actual subject.

The Suggestions tab is not named in spec §5, and is included because it is the
same hole: three shipped routes with no client. `POST
/api/facet-suggestions/{id}/accept` is, per [facets.md](../../facets.md) §3, the
**only sanctioned path that widens the vocabulary** — it derives a clean key and
labels the originating document in one call, which the raw create-value form
does neither of. Without a client the labeller's "I wanted a value you do not
have" signal accumulates permanently unseen.

## 2. What the panel must never get wrong

Three behaviours are load-bearing, in the sense that a panel which got them
subtly wrong would look correct and mislead the owner about their own archive.

- **A merge previews before it applies.** `POST .../merge` accepts `dry_run`
  and answers with the number of labels that would move. Redesign spec §7.5
  requires a diff approved before it is applied; §6 below makes the approval
  target-specific, because a count approved for one target and applied to
  another is worse than no preview at all.
- **A blocked delete states its reason.** `DELETE .../values/{value_key}`
  answers 409 with `detail` reading `"{facet}={value} is on N documents"`. That
  string is rendered verbatim. A generic "could not delete" would hide the only
  number that tells the owner what to do next.
- **Clearing a colour and leaving it alone are different requests.** The API
  distinguishes them by `model_fields_set`, so `{"colour": null}` clears and an
  absent key does not. §4 makes the wrong one unrepresentable in the client
  rather than merely avoided.

## 3. The backend addition: a label-count route

`GET /api/facets/counts` aggregates the `spend_facts` view. That view's
`eligible` CTE requires `amount_total IS NOT NULL` **and** a `payments` row, and
the route filters to `is_canonical`. Every write path this panel offers counts
something else entirely: `delete_value` and `count_labels` both count rows in
`document_labels`, unfiltered.

The two numbers diverge in three ways, all of them in the direction that makes
the panel lie:

1. A value carried only by documents with no amount has **no row at all** in
   `/api/facets/counts`, so it renders as unused. Deleting it answers 409, *"is
   on 37 documents"* — a number the owner had no way to see.
2. A value carried by a soft-deleted or non-canonical document is excluded from
   the money count but still blocks a delete.
3. For a split document, `spend_facts` reads `line_labels`, so a
   `(facet, value)` pair may appear in the money count without appearing in
   `document_labels` at all — the divergence runs in both directions.

A panel whose premise is "the number you see is the number this operation moves"
cannot display only the money count.

**The change is a new route, not a new field on the existing one.**

```
GET /api/facets/label-counts  ->  {"counts": [{"facet_key", "value_key", "labelled"}]}
```

`/api/facets/counts` is left exactly as it is. Widening it to emit rows for
money-less values was the first design and it is wrong twice over. Its
docstring names it as what the empty state proposes charts from, and
`test_a_value_with_no_money_behind_it_is_absent` asserts the money-less values
are absent *on purpose* — "proposing a chart of a value the archive has no
amounts for is exactly the noise §10.4 replaces". Adding those rows would change
what plan 4b's empty state proposes, underneath a plan being written in
parallel, to serve a panel that can just ask its own question.

Two different questions, two routes. The panel calls both and renders
`"37 labelled · 31 in charts"` — never one number.

### 3.1 The new query must be the delete check, not a copy of it

`label_counts` is the grouped form of the predicate `count_labels` already
implements, and the panel's whole claim is that its number is the one delete
enforces. So the plan also **removes an existing second copy**: `delete_value`
today inlines its own `select(count()).where(facet_id, facet_value_id)` that
duplicates `count_labels` exactly. It is changed to call `count_labels`.

That is a deletion, not a comparison test. A test asserting the two agree would
pass whenever neither exercises the branch where they differ — it fails open,
which is why this repository's rule is to delete the second copy instead.

## 4. The client

`frontend/src/api/facets.ts` is **extended**, not replaced; `fetchFacets` is not
rewritten. `FacetValueRef` gains the `colour` field it is currently missing, and
`SenderOption` in `api/taxonomy.ts` likewise.

### 4.1 Absent-versus-null, made unrepresentable

`JSON.stringify` drops `undefined` keys and preserves `null`, so a single
`patchValue(facetKey, valueKey, patch)` *can* express both — and a
`{...form}` spread, where the form object always carries a `colour` key, cannot.
Rather than document that hazard, the client removes it:

```ts
renameValue(facetKey, valueKey, label)                      // body: { label }
setValueColour(facetKey, valueKey, colour: string | null)   // body: { colour }
setSenderColour(id, colour: string | null)                  // body: { colour }
```

Each function sends **exactly one key**. "Clear it" is passing `null`; "leave it
alone" is not calling the function. There is no object to spread and no optional
field to get wrong. A unit test asserts each request body has exactly one key —
a test that goes red the moment someone merges the two functions back together.

If a future caller genuinely needs to set label and colour in one round trip,
that is a third function added then, not an optional field added now.

### 4.2 The rest

Plain typed functions over `apiFetch`, matching `payments.ts`:

```ts
fetchFacetCounts()                              // GET  /api/facets/counts
fetchLabelCounts()                              // GET  /api/facets/label-counts
createFacet(key, label, ordinal?)               // POST /api/facets
createValue(facetKey, key, label)               // POST /api/facets/{f}/values
addAlias(facetKey, valueKey, alias)             // POST .../aliases
mergeValue(facetKey, valueKey, into, dryRun)    // POST .../merge -> { moved }
deleteValue(facetKey, valueKey)                 // DELETE .../{value_key}
listSuggestions() / acceptSuggestion(id) / dismissSuggestion(id)
```

Plan 4b also adds `fetchFacetCounts`. Whoever lands second rebases and keeps one
copy; if 4b lands first, this plan uses 4b's and adds only the `labelled` field
to its return type.

`GET /api/facet-suggestions` caps at 100 server-side. No list call this panel
makes takes a `limit` above 100, asserted in a unit test.

## 5. Colour

### 5.1 The palette module

New: `frontend/src/utils/splitPalette.ts`.

```ts
export interface PaletteSlot { name: string; light: string; dark: string }
export const SPLIT_PALETTE: readonly PaletteSlot[]
export function deriveSlot(key: string): PaletteSlot
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string
```

**Six slots, validated — not chosen by eye.** Run through the `dataviz` skill's
`validate_palette.js` on the **all-pairs** pairlist, which is the correct
pairlist here: a slot is derived by hashing the value key, so any two hues can
end up side by side in a legend, and the adjacent-only pairlist would be
checking an ordering that does not exist.

| slot | name | light | dark |
| --- | --- | --- | --- |
| 1 | blue | `#1283dc` | `#5791ca` |
| 2 | orange | `#ff6f42` | `#b93b09` |
| 3 | green | `#51ae7f` | `#19825f` |
| 4 | indigo | `#4423da` | `#584fcc` |
| 5 | plum | `#993375` | `#ed3297` |
| 6 | olive | `#876708` | `#b08923` |

Both columns report ALL CHECKS PASS on all pairs. Light: worst CVD ΔE 9.9
(protan), worst normal-vision ΔE 19.8. Dark: worst CVD ΔE 9.3 (deutan), worst
normal-vision ΔE 17.2. Two light slots and one dark slot sit below 3:1 against
the chart surface, so the **relief rule** applies — a swatch is never shown
alone; in this panel and in a chart legend it always carries the value's text
label beside it. That obligation is a design constraint, not a note.

Six, not eight: the reference eight-hue set clears the adjacent pairlist but
fails all-pairs (worst normal-vision ΔE 7.1), and no ordering fixes that,
because with all pairs in play the pairlist does not depend on order. Six is the
largest set found that clears every gate in both modes.

Six slots against nineteen `category` values means derived collisions are
certain. That is the expected state, not a defect — §5.3 surfaces them and the
override resolves them.

**Both modes matter to the stored value too.** The database holds one hex per
value, so a naive override would be a light-mode colour rendered on a dark
chart. `resolveSplitColour` therefore resolves in three steps: a null `stored`
derives a slot from `key` and returns that slot's step for the current mode; a
`stored` value that **matches a slot's light hex** is that slot, and returns its
step for the current mode; anything else is returned verbatim, since a hex from
outside the palette (a script, a future free field) has no theme pair to look
up. The light hex is the slot's stored identity.

This module is the **single** definition of the mapping. Plan 4b derives the
same slots for its legend and imports it rather than writing a second copy; the
module name and signatures are handed to 4b's session as soon as this plan is
written. Two implementations of "which colour is this value" that agree today
and diverge later is the failure this repository has recorded five times.

Rejected: reusing `SUGGESTED_COLORS` from `api/settings.ts`, the nine hues used
for per-kind tile colours. Its own docstring says it is "not required to be
mutually colourblind-safe — they are conveniences, not an auto-assigned series",
and an auto-assigned series is exactly what `deriveSlot` produces. It also
includes a slate grey that reads as *disabled* in a chart.

### 5.2 The picker

`components/vocabulary/SplitColourPicker.vue`. Props `modelValue: string | null`
and the value's `key`; emits `string | null`. It offers the palette swatches and
a **Default** choice which emits `null` — clearing the override and returning the
value to its derived slot. The swatch that `deriveSlot` would produce is marked
as the default, so the owner can see what they would return to.

**No free hex field.** The column stores hex and a database `CHECK` enforces the
format, so a free input is not a *storage* risk; it is a legibility risk. It
lets the owner pick something invisible in dark mode or indistinguishable from
its neighbour, and nothing in the system could prevent that. What is constrained
is the choice, not the storage.

The component ships **standalone and unwired to any chart**. Spending-view spec
§4.7 wants the legend swatch to be a colour-setting entry point "in 4c", but
`ChartsView.vue`, `SeriesChart*` and `components/charts/` belong to plans 4b and
5. This plan delivers the component and documents its mount contract; 4b or plan
5 mounts it on the legend. Recorded so the §4.7 sentence is not read later as an
unbuilt promise.

### 5.3 Collisions are surfaced

`category` alone holds nineteen values over a palette of roughly a dozen hues,
so two values in one facet deriving the same colour is not an edge case, it is
arithmetic. The Facets tab marks any two values **within the same facet** whose
resolved colour matches.

That marker is the point of the feature: a picker on its own tells the owner
what colour a value has, and never that two of them are the same. A collision
between values in *different* facets is not marked — they never share a legend.

## 6. The merge page

Route `/vocabulary/:facetKey/:valueKey/merge`, reached from a value's Merge
action. A confirmation page with its own URL rather than a modal, following the
convention `router/index.ts` states on the document-delete route: "GOV.UK
pattern: destructive actions get a confirmation PAGE with its own URL
(back-button friendly), never a JS-only modal." Merge is the one irreversible
operation this panel offers.

Full width also means the diff is legible at 375px without nesting a
multi-column layout inside a viewport-minus-sidebar column — the breakpoint
hazard §8.3 covers.

### 6.1 The diff is four parts, and only one comes from the server

Reading `vocabulary.merge_values`, an applied merge does four things. The dry run
reports the first; the other three are computable from the vocabulary the page
has already loaded:

| part | source |
| --- | --- |
| N labels re-pointed from source to target | `dry_run` response `moved` |
| the target gains the source's **key** as an alias | client: source key |
| the target gains the source's aliases, skipping any it already has | client: set difference |
| the source value row is deleted, **with its colour override** | client: source `colour` |

The fourth is worth stating in the UI because it is invisible in the API's
answer and irreversible: a colour deliberately chosen for the source is gone,
and the merged documents take the target's colour.

No `document_labels` primary-key conflict is possible — its key is
`(document_id, facet_id)` and a merge changes only `facet_value_id` — so the
diff has no failure branch to describe.

### 6.2 The approval is target-specific

**Apply is disabled until a dry run for the currently selected target has
returned, and changing the target invalidates the previous count.**

Without that invalidation the page shows a count computed for target A beside an
Apply button that merges into target B. It would pass any test that selects one
target and applies it, and it would be wrong exactly when an owner changes their
mind — which is the whole reason a preview exists. This gets an explicit test
that goes red when the invalidation is removed.

The target select lists every other value in the facet, so the self-merge 409 is
unreachable through the UI; the handler is written anyway, since the route can
answer it.

## 7. The remaining operations

**Rename** — inline input on the row, `renameValue`. Free: labels reference
`facet_value_id`, never the display text.

**Alias** — inline input, `addAlias`. The route is idempotent server-side
(`ON CONFLICT DO NOTHING`), so adding an alias the value already has returns 200
and looks like a successful addition. The panel checks the loaded vocabulary
first and says *"already an alias"* rather than reporting a phantom success.

**Delete** — inline confirm, then `deleteValue`. A 409 renders `ApiError.detail`
verbatim. Not a generic message, and not a re-worded one.

**Create a value** — the key field is prefilled by slugifying the label and
remains editable; the server is the sole judge (422 on a key that violates
`^[a-z0-9_-]+$`, 409 on a duplicate). The client does **not** reimplement
`derive_value_key` as an authority: a second copy of a normalisation rule fails
open, agreeing with the original until the day it does not.

**Create a facet** — key, label and ordinal. The success state states plainly
that **no document carries the new facet until a labelling pass runs**
(`library label-archive`, CLI-only). Creating a facet is free and does nothing;
a UI that reported only success would be silently untrue, which is the
`docs/charts.md` §13 "nothing is excluded silently" rule applied to a create.

**Accept / dismiss a suggestion** — accept shows the derived key it will create
before creating it, since `accept_suggestion` both widens the vocabulary and
labels a document. Its 409 (derived key already exists) and 422 (nothing usable
in the label) are rendered as themselves.

## 8. Testing

### 8.1 Backend

Pytest for `/api/facets/label-counts`, with fixtures built to be adversarial
rather than favourable — each one a case where it must disagree with
`/api/facets/counts`, since a fixture where the two agree proves nothing:

- a value labelled **only** on a document with no `amount_total` — present here,
  absent from `/api/facets/counts` entirely;
- a value on a **soft-deleted** document — counted here, excluded there, and
  blocking a delete;
- a **split** document whose `line_labels` name a value its `document_labels` do
  not — present in `/api/facets/counts`, absent here;
- a value in the vocabulary that no document carries — absent from both, and
  deletable.

Plus the regression this route exists to prevent: seed the amountless case,
assert `label-counts` reports N, then assert `DELETE` answers 409 naming **the
same N**. That ties the displayed number to the enforced one, which is the
route's entire claim.

`/api/facets/counts` keeps its existing tests unchanged and unbroken — if any of
them go red, the change has altered a contract plan 4b depends on.

Every value invented. No real vehicle, property or person value enters a
fixture; the repository is public and GitGuardian does not catch this class.

### 8.2 Frontend

Vitest: `splitPalette` (determinism across calls, stability for a fixed key,
every derived colour inside the palette); each client function's exact request
body, including the one-key assertion of §4.1 and that `null` survives
serialisation while an omitted field does not; the merge page's state machine,
including the target-change invalidation of §6.2; the 409 detail rendering.

Playwright across all three viewport projects (chromium 1280, mobile-webkit 375,
tablet-webkit 656): each of the six write operations end to end, plus accepting
a suggestion, plus a delete that is refused and shows its reason.

**Every test is mutation-checked** — break the implementation, watch the test go
red, restore it. Plan 4a found eleven defects across eight tasks and every one
originated in plan text that had never been executed; three of its tests passed
on an empty database or with the feature disabled. A test that has not been seen
to fail has not been shown to test anything.

### 8.3 Layout

A value row carries a swatch, label, key, aliases, two counts, dates and four
actions, inside a column that is the viewport minus the sidebar. `md:`/`lg:` are
viewport queries and have been wrong here twice for exactly this reason. The row
uses `@container`, is measured in a real browser at 375, 656 and 1280, and each
guard is proved to go red before it is trusted.

Assertions are on DOM outcomes, never on class names: Tailwind's utilities layer
beats `utility-patterns.css` regardless of specificity, so a class assertion can
pass while the element renders differently.

## 9. What this plan does not do

- **It does not wire the legend swatch** (§5.2). The component ships; 4b or plan
  5 mounts it.
- **It does not rename or merge senders.** Those are admin taxonomy operations
  with their own merge semantics and their own panel. This plan sets a sender's
  colour and nothing else.
- **It does not split a value.** There is no split call and there should not be:
  only a model re-reading each document can decide which of two new values it
  belongs to. The panel's create-value plus `library label-archive --relabel` is
  the documented path ([facets.md](../../facets.md) §4).
- **It does not run a labelling pass.** No route exposes one; `label-archive` is
  CLI-only, and §7 makes that explicit at the point where it matters.
- **It does not edit `amount_kind`.** Still no route, and it belongs with issue
  #125's vocabulary surfaces.
