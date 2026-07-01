# Metadata dates, document history timeline, and projects UX

Date: 2026-07-01. Engineering-team cycle (evaluate → plan → develop → wrap-up),
branch `worktree-eng-metadata-dates-history-projects`, six work units (W1–W6).

## 1. What shipped

Two feature requests, driven by a single insight from reconnaissance: **most of
this was already half-built at the data layer**, so the work was overwhelmingly
frontend + exposing one existing column. No database migration.

### 1.1 Clearer document dates (W1)

The document row already had three timestamps; only two were surfaced clearly.
Now the detail view's hero stat row reads as a distinct trio:

- **Document date** — `document_date`, the date on the document (LLM-extracted, editable).
- **Ingested** — `created_at`, set once at ingestion (read-only).
- **Last edited** — `updated_at` (read-only, date+time).

`updated_at` existed in the DB (`onupdate=func.now()`) but was in no schema, type,
or UI. It's now on `DocumentDetail` (+ MCP `_document_summary`). "Last edited"
already bumped on tag/project-only edits because the PATCH handler unconditionally
rewrites `document.extra` — a regression test now locks that contract in.

### 1.2 Per-document history timeline (W2)

`ingestion_events` is an append-only audit trail already returned in the detail
payload; it was only reachable via the separate `/jobs` view. New
`DocumentHistoryTimeline.vue` renders it on the detail page as humanized
milestones (Ingested, OCR complete, Description & metadata added, Indexed for
search, Edited, Projects changed…), hiding the noisy per-stage `status_changed`
transitions and `*_skipped` events by default, with a "Show all events"
disclosure over the complete raw log. Frontend-only — no backend change.

### 1.3 Projects: multiselect editor, multi-filter, index page (W3–W5)

Projects were already a proper many-to-many (`projects` + `document_projects`,
migration 0011); the API already supported inline-add, select-existing, and
belong-to-many. The gaps were all UX:

- **W3** — new reusable `AppMultiSelect` token component (chips + filter menu +
  "Create …" for unknown names) replaces the comma-text projects editor. Backed
  by the existing comma-string draft via a computed proxy, so dirty-check / PATCH
  / re-hydrate logic is unchanged; the taxonomy cache refreshes after a save.
- **W4** — the document list `?project=` filter is now **repeatable with OR/union
  semantics** (a document in *any* selected project matches), diverging from the
  tag filter's AND. `DocumentFilters.project_slug` → `project_slugs` (Sequence);
  the filter-bar Project pill became an `AppCheckboxes` multi-select mirroring the
  Tag pill.
- **W5** — new `/projects` index page (`ProjectsListView`) with document counts,
  links to the project-filtered dashboard, an "include archived" toggle, and
  admin-only CRUD (create, inline rename/description, archive/unarchive, two-step
  delete confirm — no blocking dialog). New sidebar nav link.

### 1.4 Docs (W6)

`docs/api.md` (updated_at on detail; repeatable OR `?project=`) and
`docs/frontend.md` (date trio, history timeline, projects multiselect, `/projects`
page + nav, multi-select filter pill).

## 2. Key decisions

- **Multi-project filter = OR (union), not AND.** Documents rarely belong to
  several projects, so intersection would usually return nothing; union is the
  intuitive "show me everything across these projects." Deliberate divergence
  from the tag filter's AND, documented in `docs/api.md`. Reversible (one-line SQL).
- **"Last edited" counts tag/project edits.** Confirmed with the user; already
  true via the unconditional `extra` rewrite on PATCH — locked with a test.
- **Timeline = curated + show-all**, not raw firehose. Milestone label map on the
  frontend; unknown event names fall back to a humanized label rather than being
  dropped, so future milestone events aren't silently hidden.

## 3. The bug the full suite caught (W6)

W1 added `document.updated_at` to `_detail()`. Because `updated_at` has a SQL
`onupdate`, SQLAlchemy expires it after any UPDATE (it can't know the
server-computed value), and with `expire_on_commit=False` the stale attribute
lazy-loads on access — illegal in the sync `_detail()` after an `await commit`,
raising `MissingGreenlet`. The documents PATCH path was fixed inline (added
`updated_at` to its post-commit `refresh`), but the **notes** endpoints share
`_detail()` via a `_DETAIL_RELATIONSHIPS` refresh list that didn't include it —
11 notes tests failed only in the full run. Fixed by adding `updated_at` to that
list (renamed `_DETAIL_REFRESH_ATTRS`). Lesson reinforced: run the *full* backend
suite before merge — a per-file green is not enough when a shared serializer changes.

## 4. Verification

- Backend: 776 passed, 86% coverage; `ruff check`/`format` clean over the whole repo.
- Frontend: `vue-tsc` clean, `eslint` clean, 594 unit tests pass (new specs for
  `AppMultiSelect`, `DocumentHistoryTimeline`, `ProjectsListView`, plus updated
  filter/detail/query specs).
- e2e (`projects.spec.ts`) updated for the multiselect editor, the checkbox filter
  pill, and a `/projects` index check — self-skips without `E2E_BASE_URL`.

## 5. Follow-ons (out of scope this round)

- `AppMultiSelect` was built generically but wired only to projects; adopting it
  for the tags editor is a natural next step.
- No project access-control changes — projects/documents remain a shared family
  archive.
