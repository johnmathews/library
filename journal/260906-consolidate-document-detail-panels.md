# Consolidate the document-detail hero and metadata panels

**Date:** 2026-09-06

## 1. What & why

`/documents/:id` had become undisciplined: the hero panel and the metadata
cards below it showed the same fields. Measured, not estimated — **8 of the 10
hero-picker keys also rendered in a metadata card**, and `title` and `tags`
duplicated *outside* the picker, where a picker-based rule could not see them.

The cause was structural. The hero gained a configurable field picker
(`DEFAULT_HERO_FIELDS`) while the Details card was simultaneously split into
four tiles, and the two field lists were unrelated literals with nothing —
no shared constant, no type, no test — relating them.

**This closes an open follow-up.** `journal/260709-split-details-card-into-section-tiles.md`
§5 ended with: *"Worth a look on the live deploy to confirm the accent-heading
tiles read as intended visually (the user's original concern was aesthetic)."*
That look never happened. Two months later the answer is that they did not, so
this is not churn — it is the verdict on a bet whose own entry flagged it as
unverified. The `Financial` tile is the clearest instance: a whole card, an
accent colour and a drag handle for exactly one field, `amount`.

## 2. What shipped

- **One metadata panel.** The four `metadata-*` tiles and the `facets` card are
  one card whose groups are titled sections. `DocumentMetadataEditor` lost its
  `section` prop and gained a `variant`.
- **One component draws both surfaces.** The hero renders its fields through
  the *same* component (`variant="hero"`). Reusing it is what makes the hero
  editable at all: a hand-rolled hero would have re-implemented kind's inline
  add, the recipient adder, the three-part date group, the amount+currency pair
  and the validation badges, then drifted from the panel's copy.
- **A field renders in exactly one surface.** The hero owns `summary`, `title`
  (as the `<h1>`) and whatever the picker makes visible; the panel is handed
  those keys as `excludeFields`.
- **One Edit mode.** "Edit details" and "Edit layout" were two adjacent buttons
  over two independent flags. `useDocumentLayout` now owns persisted layout
  only; `useMetadataEditMode` is the single flag.
- **Facets autosave**, like every other field in the panel. The "Save labels"
  button is gone.

## 3. The three things that were nearly wrong

1. **De-duplicating on "does this field have a value" would have been a bug.**
   That predicate is reactive on the *document*, so a field would jump from
   panel to hero the instant a save populated it — unmounting the input being
   typed in. The split is computed from the picker's `visible` flag, which only
   changes when the user clicks a checkbox.
2. **An empty hero-owned field would have been unreachable.** The hero omits an
   empty field and the panel omits hero-owned fields, so `amount` on a
   non-financial document would have been editable nowhere. Edit mode therefore
   renders every visible hero field, empty ones included. The four-tile layout
   had the identical hazard and solved it card-side; this is the hero's version
   of that rule.
3. **The layout migration.** Retiring five card ids without one would have
   appended the panel at the *end* of the left column — below History — for
   anyone who had ever rearranged their layout, silently dropping a customised
   `facets` placement. `collapseMetadataCards` rewrites them in place. It was
   prototyped against a real saved layout *while planning*, not after: the
   no-migration case was executed and its wrong answer observed before any code
   was written.

Naming the merged id `metadata` — the pre-2026-07-09 string — let the old
expansion migration be **deleted** rather than maintained alongside its own
inverse.

## 4. Two verification gaps found on the way

Neither was the task, both were worth the detour:

- **The docs staleness gate could not see this change.** `docs/frontend.md`
  documents `DocumentMetadataEditor.vue` and `FacetEditor.vue` at length, but
  its `Covers:` named only `components/layout/` and `components/spending/`.
  Proved with a two-arm probe: touching the metadata editor left the gate green
  while touching the view reddened it. `docs/facets.md` had no `Covers:` line at
  all. Both fixed, and the gate was then *watched going red* before being
  trusted.
- **Two tests could not fail.** `toContain('metadata')` on the persisted layout
  was satisfied by every id this card has ever had; and a drag test's
  `newIndex: 9` was past an 8-card column, so with a shorter column it would
  have stayed green while no longer exercising the branch it is named for.

## 5. Verification

Frontend suite 1453 passed; `vue-tsc` and `eslint` clean; `make lint` green
(ruff, actionlint, mypy, journal index, check_docs).

Every new behavioural test was **observed red** before its fix: the collapse
migration (7 failures), the merged Edit mode (against the pre-merge view), and
both W4 contract tests — empty-field reachability, and no-field-in-both-surfaces
— against the pre-change components.

**Not verified:** no browser was driven and no screenshot taken, so nothing here
claims how the panel renders at any width. The merged panel is taller than the
four cards it replaces and `detail-layout.spec.ts` asserts no horizontal
overflow at 320/375/768/1920px on `mobile-webkit`; that spec needs a real stack
and is the outstanding check. The e2e suite as a whole was not run locally.

## 6. Follow-ups

- Run `E2E_PROJECTS=mobile-webkit npx playwright test detail-layout.spec.ts`
  against a stack (or read CI's e2e job) and record the result.
- **Deliberately not done here:** living-doc status stamps total 135KB across 19
  documents (median 4,446 chars, worst 25,042). Measured during this work,
  unrelated to it, and its own decision.
- **Deliberately not done here:** `DocumentUpdate` has Pydantic's default
  `extra="ignore"`, so `PATCH /api/documents/{id}` silently discards unknown
  body keys and returns 200. Latent rather than live — `ask/engine.py`
  allowlists field names before constructing the model — but a trap for any
  future body extension.
- If the hero field picker proves fiddly in use, note that the whole
  de-duplication apparatus exists to serve it; deleting the picker is the
  simpler design that was considered and not chosen.
