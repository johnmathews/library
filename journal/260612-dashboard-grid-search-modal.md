# Dashboard tile grid + navbar search modal

**Date:** 2026-06-12

## What changed

The documents homepage went from a sidebar-plus-list layout to a
dashboard: every document is a tile in a responsive grid using the full
content width, and the search form moved out of the page into a modal
opened from a new **Search** item in the service navigation (between
Documents and Upload), or by pressing `/`.

- `frontend/src/views/DocumentListView.vue` — sidebar and form removed;
  tile grid (`app-doc-grid`/`app-doc-card`), count line, plain
  "Filtered by …, … · Clear filters" summary, both empty states,
  pagination and `?highlight=` detail links kept.
- `frontend/src/components/SearchModal.vue` (new) — native `<dialog>`
  via `showModal()`; query input + kind/sender/tag/language/date
  filters; Search / Clear / Cancel; pre-fills from the route query;
  submit pushes the query to `/` (URL-synced view refetches) and
  closes; focus returns to the opener.
- `frontend/src/composables/taxonomyOptions.ts` (new) — lazy, cached,
  app-wide kinds/senders/tags fetch shared by the modal and the filter
  summary.
- `GovServiceNavigation` + `ServiceNavigationItem` grew `button: true`
  items (`app-nav-button` styling, `aria-haspopup="dialog"`).
- `main.scss` — `app-doc-grid`, `app-doc-card*`, `app-filter-summary`,
  `app-nav-button`, `app-modal`; the old `app-doc-list*` block removed.
- Docs: `docs/frontend.md` §1.2.6 (grid), §1.2.7 (modal + nav button),
  §1.4.1/§1.4.1.1 rewritten, §1.6/§1.7/§1.8 touched up.

## Decisions and rationale

- **Native `<dialog>`, not a JS focus-trap.** GOV.UK has no modal
  component (its guidance prefers pages — the delete flow stays a
  confirmation page). For a non-destructive, pre-filled search form a
  modal is the cheaper interaction. `showModal()` gives focus
  containment, ESC and `::backdrop` from the platform; the only ARIA
  needed is `aria-labelledby`. Focus return to the opener is done
  explicitly on the `close` event so behaviour is deterministic across
  engines (and testable in jsdom).
- **Trigger semantics.** The nav Search item is a real `<button>` with
  `aria-haspopup="dialog"`; `aria-expanded` was deliberately not used —
  per the ARIA APG it belongs to disclosure widgets, not modal dialogs.
  The GOV.UK service-navigation link mixins only style `:link/:visited`,
  so `app-nav-button` restyles the button to match the links (same
  recipe as the component's own mobile menu toggle).
- **Stretched-link tiles, one anchor each.** The title link's `::after`
  overlay makes the whole card clickable (≥44px target) without nested
  links; `:focus-within` draws the GOV.UK yellow/black focus around the
  entire tile.
- **Plain filter summary, not chips.** A `govuk-body-s` "Filtered by …
  · Clear filters" line keeps it GOV.UK-plain; kind/sender/tag values
  resolve to names through the shared taxonomy cache, fetched only when
  such a filter is active.
- **Grid breakpoints:** 1 / 2 / 3 / 4 columns at <641 / 641 / 769 /
  1400px — GOV.UK's tablet/desktop breakpoints plus the app's existing
  wide-desktop extension.

## Testing notes

- jsdom 29.1.1 implements only the `open` property on
  `HTMLDialogElement` — no `show()`/`showModal()`/`close()` at all — so
  the modal/app specs stub a minimal happy-path approximation
  (showModal sets `open`, close removes it and fires the `close`
  event). Worth re-checking on future jsdom upgrades.
- e2e: the search steps in `library.spec.ts` now go through the modal;
  tile selectors replaced the row selectors; `responsive.spec.ts`
  additionally asserts the grid's computed column count per matrix
  project (1 / 3 / 3, and 4-up at 1920×1080).
