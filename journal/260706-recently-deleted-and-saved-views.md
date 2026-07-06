# Recently Deleted holding area + Saved Views / Custom Dashboards

Two user-requested features, built together in one engineering-team cycle
(evaluate → plan → develop → wrap-up) across seven work units (W1–W7).

## 1. What shipped

### 1.1 Recently Deleted (soft-delete lifecycle)

Deleting a document already set `deleted_at` (soft delete was half-built). This
cycle completed the lifecycle:

1. `GET /api/documents/deleted` — the Recently-Deleted list (inverts the
   `deleted_at IS NULL` predicate every other read path applies), with
   `deleted_at`, `purge_at`, `days_remaining`, and the `retention_days` window.
2. `POST /api/documents/{id}/restore` — clears `deleted_at`, records a
   `restored` event; 404s unless the document exists and is *currently* deleted.
3. `purge_deleted_documents` — a daily Procrastinate cron task (modelled on
   `backfill_budget_skipped`) that hard-deletes documents past the retention
   window: deletes the row (children cascade at the DB level) and unlinks the
   on-disk original + derived artifacts via a new `storage.remove()`.
4. Config `deleted_retention_days` (30) + `deleted_purge_enabled` (kill switch),
   and migration `0023` (index on `documents.deleted_at`).
5. Frontend `RecentlyDeletedView` (`/deleted`) reusing the `.app-doc-grid`
   mosaic, per-tile Restore, countdown, sidebar link.

Documents *and* notes participate — a note is a `Document` with `source=note`,
so it flows through the same path with no extra work.

### 1.2 Saved Views / Custom Dashboards

Per-user named snapshots of the homepage filter/search state.

1. New `saved_views` table (migration `0024`), `SavedView` model with a
   composite `(user_id, sort_order)` index, and a per-user CRUD router
   (`api/saved_views.py`): list / create / update / delete / reorder, every
   query scoped by `user_id` (a foreign view is `404`, never `403`).
2. `filter_state` stores the frontend's canonical URL query
   (`buildDocumentQuery(applied)`) verbatim, so applying a view is
   `router.push({ path: '/', query })` — a lossless round-trip.
3. Frontend `api/savedViews.ts` + `stores/savedViews.ts`, a `SaveViewMenu`
   popover on the homepage toolbar (serialises the current query, optional
   pin), a `SavedViewsView` management page (`/saved-views`: apply / rename /
   delete / pin / reorder), and a sidebar **Dashboards** section that
   `v-for`s the pinned views as custom-dashboard links.

## 2. Key decisions

1. **Per-user, dedicated table.** The recon corrected an early assumption that
   the app is single-user — it has a full `User` model with per-user
   preferences. So saved views are per-user (`user_id` FK), in their own table
   rather than the preferences JSONB blob (cleaner ordering/pinning/querying).
2. **Scheduled purge, not lazy.** The backend already runs Procrastinate with
   daily cron tasks, so the 30-day hard-delete is one more `@job_app.periodic`
   task — no new infrastructure.
3. **sha256-uniqueness makes file purge trivially safe.** The plan flagged a
   `[SUSPECTED]` risk that purge might unlink a file shared by a live document.
   `documents.sha256` is `UNIQUE`, so exactly one row references each stored
   file — the share-safety guard (and its "two docs share a sha256" test) was
   *impossible to construct* and unnecessary. Replaced with a
   missing-file-idempotency test instead. Rows are committed deleted *before*
   files are unlinked, so a unlink failure orphans a file (reclaimable) rather
   than a row (which would break restore).
4. **List-deleted inverts the predicate deliberately.** The codebase has no
   single read choke-point (~18 surfaces each inline `deleted_at IS NULL`), so
   the deleted list is a sibling query, not a reuse of `filter_conditions()`.

## 3. Plan drift / gotchas discovered

1. `GET /api/documents/deleted` must be declared **before**
   `GET /api/documents/{document_id}` or FastAPI would try to parse "deleted"
   as an int id — verified by the passing list-deleted test.
2. Restore refreshes `updated_at` + `events` before `_detail` to avoid the
   post-commit `MissingGreenlet` expiry trap (onupdate col + selectin rel).
3. The purge task uses the module-level `get_sessionmaker()` (settings-derived
   engine), which points at the real DB host in tests — the purge test
   monkeypatches `jobs.get_sessionmaker` to the test-bound factory rather than
   fighting the `lru_cache`/event-loop binding.

## 4. Deviations to note

1. **Save-view control visibility.** `SaveViewMenu` sits in the homepage
   controls row, which is `v-if="items.length"` — so a filter that currently
   returns zero documents can't be saved (matches where `DashboardFieldsMenu`
   already sat). Minor UX edge, left as a product call.
2. **E2e specs unverified locally.** `recently-deleted.spec.ts` and
   `saved-views.spec.ts` parse and register across the chromium/mobile-webkit/
   tablet-webkit matrix but self-skip without `E2E_BASE_URL` (no compose stack
   was spun up here). The saved-views spec reads the sidebar dashboard link by
   attachment + href (not visibility), since the sidebar collapses below `lg`.

## 5. Tests & coverage

1. Backend: **975 passed** (baseline 956; +5 list/restore, +5 purge, +7
   saved-views, +2 config), 88% coverage.
2. Frontend: **862 passed** (baseline 827; +10 Recently-Deleted, +25
   saved-views/dashboards).
3. Lint clean: whole-repo `ruff check`/`format --check` (incl. migrations) and
   `eslint`.

## 6. Wrap-up review

An adversarial code-review pass over the whole diff found **no** correctness,
security, or data-safety issues — it positively verified purge safety
(sha256-uniqueness, commit-before-unlink, real DB cascade), per-user isolation,
route ordering, the restore refresh (no MissingGreenlet), and migration
symmetry. One hardening applied from its observations: `deleted_retention_days`
now has a `ge=0` bound (a negative value would future-date the purge cutoff and
delete every soft-deleted document), with a test. Two other cosmetic
observations (sidebar active-state on `/`, load-on-mount) were left as-is.

## 7. Files of note

- Backend: `api/documents.py` (list-deleted, restore), `api/saved_views.py`
  (new), `jobs.py` (purge task), `storage.py` (`remove`), `models.py`
  (`SavedView`, `deleted_at` index), `config.py`, migrations `0023`/`0024`.
- Frontend: `views/RecentlyDeletedView.vue`, `views/SavedViewsView.vue`,
  `components/SaveViewMenu.vue`, `stores/savedViews.ts`, `api/savedViews.ts`,
  `api/documents.ts`, `components/layout/AppSidebar.vue`, `router/index.ts`,
  `views/DocumentListView.vue`.
- Docs: `api.md` (§1.6 rewrite, §1.20 new), `jobs-and-notifications.md`
  (§1.2.3), `frontend.md` (two view rows).
