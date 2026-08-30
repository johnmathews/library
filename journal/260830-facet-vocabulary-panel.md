# The facet vocabulary panel

**Date:** 2026-08-30
**Branch:** `vocabulary-panel`

> **Note on examples.** This repository is public. Every facet value, alias,
> colour and count below is invented; none describes a real vehicle,
> property, person, sender or amount in the archive.

## 1. What shipped

Plan 4c of the charts redesign, eleven tasks on top of plan 4b's spending-view
backend (`60d6c95`): a client for six vocabulary CRUD routes, three
suggestion-queue routes and two colour routes that had shipped and deployed
with nothing calling them — `FacetEditor.vue` could set one document's
labels and nothing else could create a facet, create a value, rename one,
alias one, merge one, delete one, or set a colour.

A new top-level route, `/vocabulary` (sidebar, directly above Settings), with
three tabs — Facets, Senders, Suggestions — and a second route,
`/vocabulary/:facetKey/:valueKey/merge`, for the merge confirmation page.
`frontend/src/utils/splitPalette.ts` (a validated six-slot categorical
palette and its key-derived default), `SplitColourPicker.vue` (palette-only,
no free hex field, shipped wired into this panel and nowhere else yet), and
`frontend/src/utils/slugify.ts` (shared between the create-value form and the
suggestion-accept flow). One backend addition: `GET /api/facets/label-counts`,
and the deletion of a second copy of its underlying count that had been
sitting inline in `delete_value`. An end-to-end Playwright spec
(`e2e/vocabulary.spec.ts`) walking the whole journey — create, rename, alias,
colour, label, merge, blocked delete, successful delete — across all three
viewport projects.

## 2. The label-count route: two questions, two tables

The plan's one backend task existed because of a fact the panel would
otherwise have gotten wrong silently: the number a chart-proposal route would
show and the number a delete operation enforces are not the same number, and
conflating them makes the panel lie in the direction that hides real state
from the owner.

`GET /api/facets/counts` (plan 4b) aggregates `spend_facts`. Its `eligible`
CTE requires `amount_total IS NOT NULL` and a join to `payments` that
excludes soft-deleted documents, and the route itself filters to
`is_canonical`. Every write operation the panel offers — rename, alias,
merge, delete — acts on `document_labels` directly, with none of that
filtering. The two disagree three ways, all of them in the direction that
would make a panel built on the money count understate what a delete would
refuse: a value carried only by amountless documents has no row at all in the
money count; a value on a soft-deleted document is excluded there but still
blocks a delete; and for a split document `spend_facts` reads per-line
labels, so the divergence runs in both directions rather than only toward
under-counting.

The first design considered was widening `/api/facets/counts` to also emit
rows for money-less values. That is wrong twice over. It would have broken
`tests/test_api_spending.py::test_a_value_with_no_money_behind_it_is_absent`,
which asserts a money-less value's absence from that route **on purpose** —
the route's own docstring names it as what the spending-view empty state
proposes charts from, and proposing a chart of a value the archive has no
amounts for is exactly the noise that empty state exists to not show. And it
would have changed that contract underneath plan 4b, which was being written
in parallel and owns it. Two different questions get two different routes
instead: `GET /api/facets/label-counts` counts `document_labels` rows
directly, grouped by `(facet_key, value_key)`, unfiltered — the same
predicate `count_labels` (the existing per-value in-use check) already
implements, in its grouped form.

The regression this route exists to prevent has its own test, tying the
displayed number to the enforced one in one assertion: seed a value on three
amountless documents, read `label-counts`, then `DELETE` the value and assert
the `409`'s `detail` names the exact same number the panel just displayed.

## 3. Colour: a six-slot palette, validated not chosen

A value's stored `colour` (migration 0037, plan 4b) is nullable, and null is
the normal state — the client derives a stable slot from the value's `key`
so a legend is coloured consistently before anyone has picked anything by
hand. The picker built on top of it (`SplitColourPicker.vue`) offers exactly
six palette swatches plus a Default choice, and no free hex field: the
database's `CHECK` constraint already makes storage safe, so a free input's
only remaining risk is legibility, which a constrained choice removes and a
format constraint cannot.

The six slots were chosen by running the `dataviz` skill's
`validate_palette.js` on the **all-pairs** pairlist, not the adjacent-only
one. That choice of pairlist is forced by how a slot gets assigned: `deriveSlot`
hashes the value's key, so any two colours in a facet's legend can end up
next to each other with no fixed ordering between them — there is no
adjacency to check, only every possible pairing. Both light and dark modes
report ALL CHECKS PASS on all pairs (light: worst CVD ΔE 9.9, worst
normal-vision ΔE 19.8; dark: worst CVD ΔE 9.3, worst normal-vision ΔE 17.2).
Three of the twelve steps across both modes fall below a 3:1 contrast ratio
against the chart surface, so the relief rule applies as a hard constraint
rather than a note: a swatch is never the sole carrier of a value's identity
— in this panel and in a future chart legend it always sits beside the
value's text label.

An eight-hue reference set was tried first, since more slots means fewer
collisions against a facet like `category` with roughly twenty values. It
clears the adjacent-pairlist gate but fails all-pairs outright (worst
normal-vision ΔE 7.1), and no re-ordering of the eight hues fixes that,
because once every pair is in play the pairlist no longer depends on order —
there is nothing left to reorder around. Six is the largest set found that
clears every gate in both modes. Six slots against nineteen `category` values
means a derived-colour collision between two values in the same facet is
certain, not an edge case, so the Facets tab marks any two values within one
facet whose *resolved* colour matches — a picker alone can tell an owner what
colour a value has, never that two values share it.

## 4. Absent versus null, made unrepresentable

The API tells "clear the colour" (`{"colour": null}`) apart from "leave it
alone" (the key absent from the body) by `model_fields_set`, not by value —
`null` has to mean something a missing key doesn't. The obvious client shape,
one `patchValue(facetKey, valueKey, patch)` taking `{label?, colour?}`, can
express both in principle, but a caller that builds its body by spreading a
live form object — which always carries a `colour` key, `null` or not — loses
the distinction the moment it does. Rather than document that hazard, the
plan removed it: three narrow functions (`renameValue`, `setValueColour`,
`setSenderColour`), each sending exactly one key. "Clear it" is calling the
colour function with `null`; "leave it alone" is not calling it. There is no
object to spread and no field to get wrong.

The mutation meant to prove the merged-function version was actually
dangerous was itself a near-miss. The plan's literal Step-5 mutation called a
merged `patchValue({label: 'X', colour: undefined})` and expected the
one-key body assertion to fail. It didn't: `apiFetch` sends
`JSON.stringify(body)`, and `JSON.stringify` silently drops any key whose
value is `undefined`, so `{label: 'X', colour: undefined}` serialises to
`{"label":"X"}` — indistinguishable from what the narrow function sends. The
implementer built the mutation that actually exercises the failure mode the
design worries about — spreading a form object carrying a *concrete*, unset
colour value (`{colour: '#1283dc', label}`) — and that one failed as
predicted (`expected [ 'colour', 'label' ] to deeply equal [ 'label' ]`). The
two-narrow-functions decision stands; it is now evidenced by the mutation
that actually reaches the danger, not the one that happened to read as
equivalent to it.

## 5. The second copy deleted in `delete_value`

`delete_value`'s in-use check had been an inline
`select(count()).where(facet_id, facet_value_id)` — its own copy of exactly
what `count_labels` already computes. Since `label_counts` (the new route's
backing query) is `count_labels`'s grouped form, and the whole claim of the
new route is that its number is the one `delete_value` enforces, that inline
query became a real risk: two independently-maintained counts that agree
today and could silently diverge later. It was deleted, and `delete_value`
now calls `count_labels` directly. The repository's rule here is not to add
a test asserting the two agree — such a test only proves something on the
one code path both queries currently exercise the same way, and passes
happily forever if they're ever edited to diverge, which is exactly the
failure it exists to catch.

## 6. Three specified mutations that didn't discriminate

Every test in this plan carried a mutation check in its brief — break the
implementation the test is meant to pin, watch the test fail, restore it, and
report the observed failure text. Three of those specified mutations turned
out not to discriminate as written, and each time the implementer found (and
ran) one that did instead of reporting a false pass.

**Unfalsifiable by construction.** The merge confirmation page's `canApply`
is specified as `previewFor.value !== null && previewFor.value === target.value`
— "there is a completed preview, and it belongs to the value currently
selected." The brief's mutation swapped that for `moved.value !== null`. All
nine given tests still passed. Tracing why: `moved` and `previewFor` are set
in exactly two places in the file, always as an adjacent pair — reset
together at the top of the target watcher, assigned together once a dry run
resolves. Under the code as written, the two formulas are not merely
untested-differently, they are the same boolean in every reachable state; no
test against this implementation can tell them apart without the
implementation itself changing so the two variables can diverge. The reviewer
independently confirmed the equivalence and left `canApply` as specified —
more self-documenting even though logically redundant here — and converted
the finding into a real, previously-missing test: the async stale-response
guard (`if (target.value !== next) return`) that the co-assignment observation
led straight to. Deleting that guard and resolving two dry runs out of order
in a scratch probe did discriminate: `merge-diff` disappeared and
`merge-apply` stayed disabled, even though the user never touched the
selector — a stale response for a superseded target was silently blanking a
valid, currently-showing preview. That test is now committed
(`ValueMergeView.spec.ts`, "never lets a stale response for a superseded
target attach to the current preview").

**A no-op from `JSON.stringify`.** Covered in §4 above: the client's
one-key-body mutation, as specified, serialised identically to the code it
was meant to catch drifting from.

**The wrong field, coincidentally correct on the fixture.** The Facets tab's
collision marker is specified to bucket by each value's *resolved* colour
(`resolveSplitColour(...)`, which follows a stored override or falls back to
the derived slot). The brief's mutation swapped that for the raw stored
`value.colour` field. It did not fail: the test fixture's two colliding
values happen to already share the same *stored* colour, so bucketing on the
raw field still finds the same collision by coincidence — not because the
mutation preserves the right behaviour, but because the fixture didn't
distinguish "same resolved colour" from "same stored colour." The brief's own
corrected variant — bucket by `value.key` instead, which is unique per value
by construction and so can never produce a collision — did fail
(`expected false to be true`, the collision marker never rendered), and that
is the mutation actually run and recorded.

## 7. The container-query proof

`FacetsPanel.vue`'s value row lives inside a card inside the viewport-minus-
sidebar content column, so it uses a container query (`@container` on the
card, `@md:flex-row` on the row) rather than a viewport one — the same class
of hazard this repository has already recorded twice for the header toolbar.
Both implementers assigned to prove it in a real browser were blocked
(Playwright's shared Chrome profile locked by a concurrent worktree session,
the Chrome extension not connected), so the controller drove it directly with
`@playwright/test` and route-stubbed fixtures. Measured card widths at the
three e2e viewport projects: 960px at 1280, 608px at 656, 343px at 375, row
direction `row / row / column`. Swapping `@md:` for a plain `md:` left the
card widths unchanged but flipped the 656 case to a stacked column — 656px is
below the viewport `md` breakpoint (768px) even though the card itself, at
608px, is well past the container `@md` breakpoint (448px). 656 is exactly
the tablet-webkit e2e project's width, so this was not a hypothetical
mismatch. Restored and reconfirmed row layout at all three widths.

## 8. Gates

Docs gates run clean for this task:
`uv run python scripts/check_docs.py`,
`uv run python scripts/build_journal_index.py --check`. The four targeted
`GET /api/facets/label-counts` tests
(`tests/test_api_spending.py -k "label_count or displayed_count"`) and the
eight frontend spec files touched by this plan
(`FacetsPanel`, `SendersPanel`, `SuggestionsPanel`, `VocabularyView`,
`ValueMergeView`, `splitPalette`, `slugify`, `SplitColourPicker` — 66 tests)
were re-run green while writing this entry. The full backend suite, the full
frontend suite, `npm run lint`/`type-check`, and the full e2e run (all three
viewport projects) are the standing pre-merge gate this branch still owes,
tracked in the plan's final-verification checklist rather than repeated here.
