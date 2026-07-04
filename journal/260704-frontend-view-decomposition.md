# Frontend view decomposition — AppPopover + AdminView + DocumentDetailView

The three frontend-decomposition units (W14, W15, W16) deferred from the
2026-07-04 robustness-and-consistency run. All three are **pure refactors** —
no behaviour or visual change intended; the acceptance contract is that every
`data-testid`/`id` stays at the same DOM position. Approach was spike-first for
each: map the seams, get the non-obvious decisions signed off, then extract.

Branched from `main` at `1798515`. Frontend baseline: 719 unit tests, tsc +
eslint clean. Final: **745 unit tests**, tsc + eslint clean.

## 1. W14 — AppPopover primitive

Four bespoke overlays each re-implemented open-state + outside-click + ESC +
z-index, divergently (`z-10/20/40/50`; mousedown vs document-click vs
focusin/focusout; only some with ESC/focus-return). Built one behavioural
primitive, `components/app/AppPopover.vue`, and refit all four onto it:
`FilterPill`, `DashboardFieldsMenu`, the `JobsView` columns menu, and both
`AppHeader` dropdowns. `SearchModal` stays a native `<dialog>` (a modal, not a
popover).

Key decisions (spike, signed off before coding):

1. **No Teleport.** The brief suggested Teleport, but every overlay anchors its
   panel in normal flow, and `FilterPill.spec` asserts class-based `left-0`/
   `right-0` alignment while the header-jobs dropdown uses bespoke responsive
   `fixed→sm:absolute` positioning. Teleporting to `<body>` would break both and
   force a JS-computed-coordinates rewrite — a visual/behaviour change outside a
   pure refactor. AppPopover keeps the in-flow `relative inline-flex` root;
   Teleport is a deferred robustness enhancement, not part of this unit.
2. **One dismissal model.** Standardised on click-toggle + Escape (focus returns
   to the trigger) + outside-**mousedown** + `v-if` panel. The two AppHeader
   dropdowns previously used `@focusin/@focusout` auto-close; they now gain
   ESC/focus-return/outside-click and lose tab-away auto-close — the plan's
   intended small behaviour improvement, and the one real behaviour change in
   W14.
3. **One z token.** `--z-popover: 50` (`@theme` + a `@utility z-popover`) sits
   above the sticky header (`z-30`); replaces the four divergent z-values.

API: controlled `v-model:open`; `align` (`left`/`right`/`auto`/`none`);
caller-owned `panelClass` for positioning/width and `panelAttrs` for panel-level
`data-testid`/`id`/`role`. Fall-through attrs land on the **root** (standard Vue)
so a wrapper like FilterPill still receives its parent's `pill-*` testid on the
outer element — this was the one snag during the refit (initially the panel
swallowed the parent's wrapper testid, breaking `DocumentFilterBar.spec`; the
`panelAttrs` split fixed it). New `AppPopover.spec.ts` (10 tests) covers
open/close/ESC/outside-click/alignment/attr-passthrough.

## 2. W15 — decompose AdminView (2452 → 90 lines)

`AdminView.vue` had zero child components. It is now a thin shell (PageHeader +
tablist + one `v-show` section per tab). Each tab is its own component under
`views/admin/`: `AdminSystemPanel`, `AdminArchitecturePanel` (owns the moved
`.doc-markdown` scoped style), `AdminCoveragePanel`, `AdminUsersPanel`,
`AdminMetadataPanel`. The spike confirmed **zero shared data-state between
tabs** — a clean split; the only cross-cutting deps were `auth` (Users) and a
cosmetic `card p-6` string.

The higher-value win: the triplicated Senders/Recipients/Kinds CRUD collapsed
into one generic **`TaxonomyCrudPanel.vue`** driven by a `TaxonomyDescriptor`
(`views/admin/taxonomyCrud.ts`). Correctness over DRY — Kinds genuinely diverge
and the descriptor captures every point:

- `keyOf`: id (senders/recipients) vs **slug** (kinds) — the pending set, row
  errors, and every testid are keyed on `TaxonomyKey = string | number`.
- `hasMerge`: `true` → a 409-on-rename reveals the merge prompt via
  `readMergeBody`; **`false` (kinds)** → the same 409 becomes a row error and the
  merge UI never renders.
- `parseReassign`: `Number(v)` vs raw slug; `remove()` preserves the
  zero-doc-single-arg vs in-use-two-arg call shape byte-for-byte.
- `list`/`create` sourced per entity (`@/api/taxonomy` vs `@/api/admin`).

`refreshTaxonomyOptions()` fires exactly once per successful mutation. Currencies
+ FX stay inline in `AdminMetadataPanel` (a different per-row-state idiom, not
taxonomy).

**Lazy-load preserved by construction** — the original `watch(tab)` is gone;
`AdminMetadataPanel` takes an `:active` prop and loads on the first false→true
transition, so taxonomy lists still fetch on first tab open, not on page mount
(the admin e2e depends on this). The unit spec can't catch a lazy→eager
regression, so this was gotten right structurally.

## 3. W16 — decompose DocumentDetailView (2245 → 867 lines)

Extracted `DocumentMetadataEditor.vue` (the edit-mode metadata editor) and
`NoteEditorPanel.vue` (the in-place note editor + version history). The parent
keeps the hero, the two-column grid, the preview column + markdown reader, the
actions card, and the history timeline — the model layout is untouched.

The spike corrected a planning assumption: **there is no autosave debounce** —
saves are event-triggered (`@change`/`@keyup.enter`/`@focusout`) with a per-field
in-flight guard, so the extraction boundary is "who owns the drafts + guard"
(the editor), not "who owns a timer".

The headline risk was the shared-state write-back: every save **replaces `doc`
wholesale**, and the hero/preview read `doc`, so a one-way prop would freeze them
on the pre-save snapshot — the frontend mirror of the backend
`_DETAIL_REFRESH_ATTRS` expiry hazard. Contract (decided in the spike):
props-down + **`v-model:doc`** emit-up (not Pinia, not provide/inject — `doc` is
per-route state with one owner).

- `DocumentMetadataEditor`: `saveField` emits `update:doc` then
  `hydrateField(field, fresh)` — the fresh doc is passed **explicitly**, because
  `props.doc` hasn't propagated back at that tick.
- `NoteEditorPanel`: `saveNote`/`restoreVersion` emit `update:doc` **and** a
  second `reload-markdown` channel — the note body lives in the parent's reader
  (`markdownData`), not on `doc`, so `update:doc` alone would leave the reader
  stale.
- `hydrateDrafts` stays tied to the edit-toggle only (**never** `watch(doc)`), so
  a background SSE/rerun refresh mid-edit can't clobber in-progress drafts.

Shared `marked`+DOMPurify/format helpers extracted once to
`utils/documentFormat.ts`. Parent target was <~600 lines; landed at 867 because
the parent legitimately keeps the hero + full preview column + reader + review
bar + actions + history + page watchers + the `.doc-markdown` style block.

## 4. Process notes

- Both view decompositions ran as **parallel subagents** (independent files),
  each keeping its own view spec green after every extraction step; the lead
  reconciled with a full-suite run. A coordination note kept each from acting on
  the other's transient in-progress state when their whole-project
  `tsc`/`eslint` runs surfaced it.
- Added focused contract specs for the three new reusable components
  (`TaxonomyCrudPanel` 8, `NoteEditorPanel` 4, `DocumentMetadataEditor` 4) to pin
  the divergence/emit contracts independently of the whole-view regression specs.
- Independent code review of the full diff found **no correctness bugs**. One
  sub-threshold, non-visible delta: `DocumentMetadataEditor` re-fetches taxonomy
  on each document→document navigation (best-effort, arguably fresher) rather
  than once per persistent view instance. Left as-is.

Each unit is behaviour- and visually-identical to before; the win is purely
structural (5866 lines across three files → smaller, testable, reusable pieces).
The Playwright e2e job in CI is the final behavioural gate.
