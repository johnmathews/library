# Dashboard sort + card-fields picker, admin reference CRUD, series-aware currency normalize

**Date:** 2026-07-02
**Branch:** `worktree-eng-dashboard-and-admin-features`
**Cycle:** engineering-team (evaluate → plan → develop → wrap-up), five work units.

## What shipped

Five related features, built as work units W1–W5 (run dir
`.engineering-team/runs/manual-20260702T160000Z/` has the evaluation report and
improvement plan).

1. **W1 — Financial-panel alignment fix.** In `/document/N` edit mode the
   Currency input rendered ~2 lines below Amount because it alone omitted
   `hide-label` and carried an inline `hint`, while every sibling editor defers
   its label to the outer uppercase `<dt>`. Gave Currency `hide-label`, dropped
   the hint, kept guidance via `placeholder="EUR"`. Guard test is structural
   (asserts both labels are `sr-only`, no header hint) because JSDOM has no
   layout — `offsetTop` is always 0.

2. **W2 — Homepage sort control.** `sort` (`document_date` | `added_date`) ×
   `direction` (`asc` | `desc`), end to end. Backend: `DocumentSort`/
   `SortDirection` enums + a `_order_by()` helper in `search.py` that keeps
   `document_date` NULLS LAST in *both* directions and uses `id` as a
   same-direction deterministic tiebreaker; the FTS/rank branch is untouched, so
   **relevance always wins while `q` is present** and the control is disabled in
   the UI then. Frontend: `sort`/`dir` round-trip through the URL via
   `documentQuery.ts`, omitted at their defaults, and **excluded from
   `hasActiveFilters`** (sort is not a filter).

3. **W3 — Per-user card-fields picker.** The stored `dashboard_fields` list is
   now **order-significant**: `DocumentListView` renders its card meta row by
   iterating `auth.dashboardFields` (the ungated "Needs review" badge stays
   pinned first, outside the field set). A mosaic **Fields** popover
   (`DashboardFieldsMenu`) toggles + reorders via a reusable
   `DashboardFieldsEditor` (checkbox + SortableJS drag + accessible Up/Down
   buttons + Reset), persisting through the existing `PUT /api/settings` →
   `auth.applyPreferences` path — no new endpoint (the `_clean` validator already
   preserved order). Settings → Dashboard tab reuses the same editor. Added
   `sortablejs` (`useSortable` lives in `@vueuse/integrations`, not
   `@vueuse/core`, so it was simpler to drive Sortable directly).

4. **W4 — Reference-entity admin CRUD.** Filled the gaps around the existing
   recipient pattern: `create_recipient`; senders create/rename-or-merge/
   delete-with-reassign; kinds **name-only** rename (slug immutable, a name
   collision is a hard 409 — no kind merge) + reassign-by-slug delete. All FKs
   are `ON DELETE SET NULL`, so deleting a reference row never deletes documents.
   A shared `_acquire_admin_lock()` now guards every reference mutation **and was
   retrofitted onto the existing recipient routes**. `AdminView`'s Metadata tab
   grew Senders/Recipients/Kinds cards.

5. **W5 — Series-aware currency normalize.** Currency is free-text but part of
   series identity, so a rename is a whole-store rewrite (`currencies.py`):
   plain-updates `documents`/`authored_series`/suggestions; **merges** the
   `series_insights` cache (drops a `from`-row that would collide with an
   existing `to`-bucket, keeps the survivor — it regenerates on next indexing);
   **refuses** up front (409, lists conflicts) if it would collide with a
   user-authored membership/meta override — no user data is dropped; never
   touches `fx_rates` (flags `fx_rate_missing`). Behind a dedicated advisory
   lock. `AdminView` gained a Currencies card with a confirm-step normalise form.

## Decisions worth remembering

- **Two brief inaccuracies caught during evaluation.** The taxonomy service is
  `src/library/taxonomy.py` (no `services/` package), and the existing pg
  advisory lock guarded **user-role** mutations only — the recipient routes it
  said to "model on" took *no* lock. Surfaced both; the user chose to add the
  lock to the new reference mutations and retrofit recipients (D2).
- **Currency scope (D1) → series-aware, and override collisions REFUSE.** The
  user picked the fuller, riskier scope, then chose refuse-on-override-collision
  over drop-and-report, so the operation never deletes user-authored series
  overrides.
- **Collision SQL matches the constraints.** The series unique keys are
  `NULLS NOT DISTINCT`; `sender_id`/`kind_id`/`document_id` are all non-nullable
  (so `IS NOT DISTINCT FROM` ≡ `=` there) and only `currency` is nullable but is
  always a validated non-null code in the rename path. The `series_insights`
  DELETE-then-UPDATE is provably clash-free (after the DELETE no colliding
  companion remains). Table names in the f-string SQL are module constants, not
  user input.

## Gotchas re-confirmed

- **Coverage under `TestClient` undercounts request-handler lines.** The project
  runs `coverage run -m pytest` with the default thread-local tracer and no
  `concurrency = ["thread"]`; Starlette's `TestClient` runs handlers in a worker
  thread, so new API-handler/service lines exercised only via the client show as
  "uncovered" in a per-file report even though their assertions verify real DB
  state. The full-suite aggregate is **85%** (at the `fail_under=85` gate). New
  taxonomy/currency services are behaviourally tested through the admin API.
- **Kind names are standardised.** `rename_kind` runs `standardize_kind_name`
  (sentence case) like `create_kind`, so "W4 Kind Renamed" stores as
  "W4 kind renamed" — tests assert the standardised form.
- **PEP 695 generics.** ruff wanted `class CreateEntityResult[Entity: (Sender,
  Recipient)]` (new syntax) over `Generic[TypeVar]`.

## Tests / checks

- Backend: **835 passed** (baseline 805; +30). New: sort ordering (4 combos +
  search-override + 422), dashboard order round-trip, full sender/kind/
  recipient-create admin suite mirroring recipients, currency unit + series-aware
  integration (insight merge, override-conflict refusal, FX warning, validation,
  gating).
- Frontend: **642 passed** (vitest), type-check + ESLint clean. New:
  DashboardFieldsEditor unit, DocumentListView sort + ordered-render + Fields
  popover, documentQuery round-trip, AdminView senders/kinds/currencies.
- ruff check + format clean over the whole repo (incl. migrations/).
- No schema migrations — every feature uses existing columns.
- Security scan: no secrets in the diff; `.gitignore` covers sensitive patterns.
- Code review (subagent): no issues at the ≥80% confidence threshold.
- Docs updated: `docs/api.md` (sort params §1.3.1, admin endpoints §1.18.3-6),
  `docs/admin.md` (§1.2.2-5), `docs/frontend.md` (sort control, card-fields
  picker, Metadata tab). Doc-audit subagent caught + fixed an impossible currency
  example and two stale `frontend.md` claims (render order, Settings tab).

## Follow-ups (not done)

- FX rates are never seeded for a newly-normalised code — the UI warns, but
  there's no "seed a rate" affordance. If cross-currency series FX becomes common
  after normalising, add one.
- The currency rename's TOCTOU window (an unrelated insert into an override table
  mid-rename, not blocked by the lock) would surface as a 500, not corruption —
  fine for a single-user library, revisit if it ever goes multi-user.
