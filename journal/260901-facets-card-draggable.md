# Making the Facets card movable without moving it

**Date:** 2026-09-01
**Branch:** `feat/139-facets-card-draggable`
**Issue:** [#139](https://github.com/johnmathews/library/issues/139)

## 1. What went

The Facets card on `/documents/:id` was pinned below the metadata tiles and
could not be dragged. It is now card id `facets` in `useDocumentLayout`'s
`DEFAULT_CARD_COLUMNS`, rendered from the shared `<DefineCard>` template like
every other card, so **Edit layout** moves it within the metadata column or
across into the preview column.

The original rationale — that a facet label is "a distinct concept from the
per-field Details metadata those tiles edit" — was already recorded in
`docs/frontend.md` as a **known limitation rather than a design choice**. It is
edited on the same page, in the same column, for the same reasons as the
metadata tiles, and it is the primary editing surface for the charts feature.

## 2. The migration question answered itself, once asked in the right order

Every existing user has a persisted `cardColumns` with no `facets` entry, so
where it lands on their next visit is a migration question, and the issue was
right to flag it: `reconcileCardColumns` appends an unknown-but-known-to-
defaults card to the **end** of its default column, which would normally be the
wrong place — that is exactly why `migrateMetadataCard` exists, expanding the
legacy `metadata` id *in place* so a user who had moved the Details card kept
its spot.

But for this card the end is not the wrong place. Before the change it rendered
*after* every card in the left column. So listing it **last** in
`DEFAULT_CARD_COLUMNS.left` makes "where reconciliation puts it" and "where it
should go" the same place, and there is nothing to migrate. Every stored layout
lands exactly where it already rendered.

That is also the better product answer. The ask was to make the card
*movable*, and a default that also *moved* it would rearrange everyone's page
as a side effect of granting them the ability to rearrange it themselves.

## 3. The index-arithmetic trap was real, but pointed one card over

The issue carried a warning, lifted from the mount-site comment: the Facets
card is a real DOM child of `#document-metadata-column`, which SortableJS is
bound to, so it counts as a sibling when computing `evt.newIndex` even though
it can never be dragged. Removing it therefore changes that arithmetic, and the
existing test that passes is pinning the *degraded* behaviour, not the correct
one.

Reading the template showed there are **two** such siblings, not one:
`FacetEditor` and `PaymentGroup`, both rendered after the `v-for`. So promoting
one leaves the other, and the picture is:

- indices **within** the card list are now exact rather than merely harmless,
  because the card that used to sit among them is now one of them;
- `PaymentGroup` still inflates, but it renders after every card, so the only
  index it can inflate is one already past the end — which
  `presentIndexToFullIndex`'s out-of-range branch turns into "append", the
  same answer as before.

Strictly better, then, but the test that documented this had to be rewritten
rather than left passing: its name, its worked example and its `newIndex` all
described the wrong sibling afterwards. A test that still passes for a reason
that has changed is worse than one that fails.

## 4. Four existing tests broke, and one of them deserved to

Adding a card to the defaults reds every test that hardcodes a full reconciled
column. Three were plain expectation updates. The fourth was more interesting:

```ts
setColumn('left', [...DEFAULT_CARD_COLUMNS.left].reverse())
// …
expect(columns.left[0]).toBe('history')
```

That probe silently encoded "history happens to be last in the defaults". It
was testing a round-trip, so it now compares the whole array against the
reversed defaults — which says what it means and does not break the next time a
card is appended.

## 5. Verification

Eight new tests. Five in `DocumentDetailView.spec.ts`: the drag handle appears
in edit mode (and not in read mode), the default position is unchanged, the
editor renders exactly **once** (the failure mode of promoting a card while
leaving its old mount site in place is two live editors), a drag to the top of
the column moves it *and renders it there*, and a cross-column drag carries it
into the preview column. Three in `useDocumentLayout.spec.ts` covering the
reconciliation cases: a pre-#139 stored layout, a rearranged one, and one where
the user has already moved the card. All were run red first.

`DrillCellBody.vue` mounts `FacetEditor` directly rather than through the card
system, so the spending drill-through is unaffected; read to confirm, and its
tests are green. Frontend suite: 112 files, 1424 tests. `npm run lint` and
`npm run type-check` clean.
