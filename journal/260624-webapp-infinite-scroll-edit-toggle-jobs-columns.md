# Webapp: infinite scroll, page-wide edit toggle, jobs columns, OCR provenance

Four user-requested UI improvements to the Library webapp, shipped together on
`feat/webapp-list-edit-jobs-improvements`. A fifth request (per-document charts +
a `/charts` aggregate view) was scoped but deliberately deferred to a follow-up
branch (see "Deferred" below). Run artifacts (evaluation + plan) live in
`.engineering-team/runs/manual-20260624T080332Z/`.

## What shipped

### 1. Homepage document list → infinite scroll (`DocumentListView.vue`)
Replaced the numbered `AppPagination` (fixed 25/page) with accumulating infinite
scroll. A foot **sentinel** observed by `@vueuse/core`'s `useIntersectionObserver`
auto-loads the next batch; `items` accumulates and `total` comes from the list
response. A filter change resets the accumulation, aborting any in-flight request
via the existing `AbortController` plus a `generation` counter so a late
"load more" can't append to a newer filter's results. A deep-linked `?page=N`
still loads N batches up front (back/forward and refresh keep the view). `PAGE_SIZE`
stays 25 per batch.

A visible **"Load more"** button and a "Loading more…" indicator back the
observer — both as graceful degradation (jsdom has no real IntersectionObserver,
so the button is also the tested path) and to cover the edge case where a short
first batch leaves the sentinel on-screen (the observer only fires on intersection
*transitions*). The button is the deliberate fallback there rather than adding
layout-measurement hacks.

### 2. Document detail → page-wide edit toggle (`DocumentDetailView.vue`)
Removed the ~10 per-row GOV.UK "Change" buttons (the user's complaint: visual
clutter). A single **Edit / Done** toggle in the Details card header reveals an
inline editor for every field at once. Each field **autosaves independently on
commit** via the existing per-field PATCH — there is no global Save/Cancel:

- Per-field draft state: `drafts` (text + selects), `dateDrafts`, `currencyDraft`.
- `fieldDirty()` guards each save so a focus-through with no real edit never fires
  a PATCH. Tags compare as a *sorted set* (order-insensitive) to avoid no-op PATCHes.
- Single-value fields save on native `change` (fires on blur for text, on selection
  for selects), which bubbles to the editor component. The three-part date input
  saves once on `focusout` *leaving the whole day/month/year group* (checked via
  `relatedTarget` containment) — saving per sub-field `change` would persist
  intermediate dates and could drop a commit while a prior PATCH was in flight.
- Each save replaces `doc` with the server response, re-hydrates that field's draft
  (reflecting canonicalised values like slugified tags), and flashes a brief
  "Saved". Validation/save errors render inline per field.

### 3. OCR-confidence provenance label (`DocumentDetailView.vue`)
Answered a user question and fixed the resulting mislabel. A scanned paper letter
imported from Paperless showed "born-digital text — no OCR run" because the
importer reuses Paperless's own OCR text (`importer/runner.py` reuses
`mapped.content`, engine `paperless-import`) and leaves `ocr_confidence` NULL,
which the UI conflated with a true born-digital upload. Now, when `ocr_confidence`
is null, the System panel splits by `source`: `import` → "Imported (Paperless) —
text layer reused from Paperless, no OCR re-run"; everything else keeps
"Not applicable — born-digital text, no OCR run".

### 4. Jobs page → column control + responsive layout (`JobsView.vue`)
Mirrored the journal-insights webapp convention (`EntryListView.vue`):
- A **Columns** visibility menu (checkbox per column) persisted to `localStorage`
  key `library:jobs-columns`; load merges over defaults so future columns keep
  their default visibility. An outside-click handler (via a wrapper template ref,
  not a document-wide query) closes the menu.
- **Dynamic widths** via `table-fixed` + `<colgroup>` with `clamp()` so the
  Document column (was too wide) clamps to ~10–22rem and truncates with a tooltip;
  narrow columns stay compact.
- **Responsive**: a table on `sm:` and up (`hidden sm:block`), a card/tile list on
  small screens (`sm:hidden`) — headline = Document link + Status badge, then a
  `grid grid-cols-2` meta grid of the remaining visible columns.

## Process notes
- Built via the engineering-team skill (evaluate → plan → develop → wrap-up). The
  three view files are disjoint, so W1 (list) and W3 (jobs) ran as parallel
  subagents while W2+W4 (detail view) were done in the main thread.
- Decisions taken with the user up front: infinite scroll (not a page-size picker),
  precompute+cache for the future chart description, per-field autosave (not a
  batch Save), and quick-wins-first sequencing.
- A code-review pass flagged a real date double-save bug (intermediate/dropped
  PATCHes from the original `@change`-on-wrapper design) — fixed by moving to the
  focusout-once approach above, with a regression test. Also fixed tag-order
  false-dirty and the JobsView outside-click query.

## Verification
Frontend only (no backend change). `npm run type-check`, `npm run lint`,
`npm run test:unit` (319 passing across 43 files, incl. new infinite-scroll,
edit-toggle/autosave, OCR-label, jobs-column, and date-autosave specs), and
`npm run build` all green. The build's >500 kB `DocumentDetailView` chunk warning
is pre-existing (pdf.js + marked), not from this change.

## Deferred — item 5 (per-document charts + `/charts`)
Planned in the run's `improvement-plan.md` §2.3 but not built: a cached,
LLM-generated series description (precompute on a background job when a new doc
joins a `(sender, kind)` series), per-point citation links, the chart wrapped in
the standard tile chrome, and a new `/charts` aggregate view + `GET /api/charts`
endpoint enumerating every eligible series. To be done as a separate branch/PR.
