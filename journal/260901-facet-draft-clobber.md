# The facet Save button that could be disabled forever

**Date:** 2026-09-01
**Branch:** `fix/144-facet-draft-clobber`
**Issue:** [#144](https://github.com/johnmathews/library/issues/144)

## 1. What went

`FacetEditor.vue` re-hydrated its draft from `props.labels` unconditionally:

```ts
watch(() => props.labels, (next) => { draft.value = { ...next } })
```

The comment above it argued this was safe — "the draft itself only ever changes
via `onSelect`, so this never clobbers an in-progress edit with itself." That
holds only if `props.labels` never arrives *after* a selection, and nothing
guarantees it does not. It now skips while the owner has touched the draft, and
clears that flag in the two places the server legitimately becomes the truth
again: after a save round-trips, and when the page switches document.

## 2. The race is between two fetches nobody ordered

`DocumentDetailView.vue` feeds this component from two independent sources:

- `facets` — the vocabulary, fetched once in `onMounted`;
- `facetLabels` — this document's labels, fetched in the route watcher.

The selects become interactable as soon as the **vocabulary** lands. The
**labels** can land after that, and on a cold backend they do. So the sequence
is: the owner picks a value, the label fetch resolves, the watch fires, the
draft resets to the server's empty map.

What makes it worse than a lost keystroke is what happens next. Save is
disabled on `saving || !hasChanges`, and `hasChanges` compares draft against
`props.labels` — which now agree. So the button greys out and **stays** grey.
There is no error, because nothing failed. The owner is looking at a document
with no label, a Save button that will not enable, and no way to tell why.

## 3. It was already reding CI, which is how it was found

This surfaced as an e2e flake, not a bug report: `facets.spec.ts:41` burning its
full 180s timeout, then passing on retry in 3.3s. Playwright's trace showed the
button resolving immediately and then reporting `element is not enabled` on 336
consecutive polls.

Three details in issue #144 pointed at a product bug rather than a slow page:

- **180s then 3.3s** is a lost race, not a slow machine. The retry wins because
  the backend is warm and the labels fetch gets in first.
- The DOM snapshot showed `disabled` with `aria-disabled="true"` — genuinely
  `hasChanges === false`, not an overlay or a hit-testing problem.
- It reproduced identically on chromium, tablet-webkit and mobile-webkit, so
  nothing about it was viewport-specific.

The spec was right and the component was wrong, which is the reason not to
"fix" this by making the test wait harder. Since e2e was sharded three ways the
pipeline's wall clock is its longest shard, so this one flake was also the
difference between a ~5m and a ~9m job — and on one PR it failed both attempts
and red-ed a one-line lockfile bump.

## 4. Not over-correcting

The failure mode of the obvious fix is freezing the editor: a draft that never
re-hydrates would leave the card showing a stale value after a save, and would
carry an unsaved selection across to the *next* document — offering to save one
document's label onto another.

So the two tests that pin the other direction were written alongside the two
that reproduce the bug, and they passed *before* the fix as well as after.
That is the point of them: they are the ones that would have gone red if the
`touched` flag had been too sticky. Clearing it before `emit('saved')` rather
than after matters for the same reason — the parent assigns `labels` inside
that handler, so a flag still set at that moment would suppress the very
re-hydration the save exists to produce.

## 5. Verification

Four new unit tests in `FacetEditor.spec.ts`. The two reproducing the race were
run red first — the select reverted to `''`, and the `PUT` was never issued at
all, which is exactly the user-visible symptom. Frontend suite green: 112 files,
1420 tests. `npm run lint` and `npm run type-check` clean. Documented in
[docs/facets.md](../docs/facets.md) §2.2, which is new.
