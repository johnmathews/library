# Jobs view: system-task labels, filters, and an app-wide live-update sweep

Run via the engineering-team skill (run dir `manual-20260624T194552Z`). Six work
units on branch `worktree-eng-jobs-view-and-live-update`.

## 1. Why

Two problems with the Jobs view (`/jobs`):

1. Toggling **Show system tasks** filled the table with near-empty rows —
   document-less jobs (email poll, series insight, doc-less thumbnails) have null
   document/cost/error, so most cells were em-dashes.
2. No way to filter to a single document's history or to one task type.

And a broader question: does the app keep itself up to date as a document
processes, or does the user have to refresh? A sweep found the live-update story
was **partial** — the header indicator and terminal toasts worked, but `JobsView`
only refetched on `activeCount` change (missing intra-pipeline stage transitions
and all system-task activity), and neither the document list nor the detail page
subscribed to the SSE stream, so their status badges went stale until reload.

## 2. What shipped

- **W1 — backend `/api/jobs` filters + history.** Added `document_id` and
  `task_name` query params via a dynamic query builder (`_build_query`), plus
  `GET /api/jobs/task-names`. `document_id` switches to **uncollapsed history
  mode** (every job for one document, newest first). The default collapsed-view
  output is byte-for-byte unchanged.
- **W2 — JobsView system labels + filter bar.** Document-less rows now render a
  grey **`System`** chip + humanised task name instead of empty cells. A filter
  bar adds a task-type `AppSelect` (fed by `/api/jobs/task-names`) and a document
  typeahead (searches `/api/documents?q=`); both live in the URL query, so
  `/jobs?document_id=<id>` deep-links a document's history with a removable chip.
- **W3 — jobsStore `lastEvent`.** The store now exposes a `lastEvent` ref bumped
  on *every* SSE document event (not just terminal ones) — the foundation the
  other views watch.
- **W4 — JobsView liveness.** Refetch on `lastEvent` (catches `ocr → extract → …`
  stage changes that leave `activeCount` unchanged) and a 10 s poll while
  "Show system tasks" is on (system tasks emit no SSE event).
- **W5 — DocumentListView live status.** Watches `lastEvent` and patches a tile's
  `status` in place — no refetch, so scroll position and infinite-scroll pages
  are preserved.
- **W6 — DocumentDetailView live status + link.** Refetches the open document on
  its own events (suppressed while a re-extraction poll runs); added a **View job
  history** link to `/jobs?document_id=<id>`.

## 3. Key decisions

- **Unified table, not a separate system section** (user's call): one table with
  `[System]` labels keeps the mental model simple.
- **Poll, don't extend SSE, for system tasks.** Periodic chores (email poll,
  series insight) emit no Postgres `NOTIFY`; adding a generic job event stream
  for them was judged over-engineering versus a cheap interval poll that only
  runs while they're shown.
- **Patch-in-place on the list, refetch on the detail.** The list has many tiles
  and an accumulating infinite scroll, so a status patch avoids a jarring
  refetch; the detail page is a single document where a refetch also picks up
  metadata the pipeline fills in.

## 4. Code-review catch (fixed before merge)

The independent review flagged a real bug in the first cut of W1: the `task_name`
filter was applied in the **outer** query, *after* the per-document collapse. So
filtering by `process_document` would drop any document whose *latest* job was a
different task (e.g. `embed_document`) — the dropdown would silently lose
documents. Fixed by moving the task predicate **inside** the CTE (before the
collapse) and gating the hide-system clause off whenever a task filter is active.
Added a regression test
(`test_jobs_endpoint_task_name_filter_matches_documents_by_task`) that fails on
the old placement.

Two lower-confidence notes (out-of-order `getDocument` on rapid detail events; a
double-`load` race in JobsView) were left as-is — both are pre-existing patterns
below the action threshold and self-heal on the next event/reload.

## 5. Tests & docs

- Backend: **522 pass** (4 new jobs tests + 1 regression), 90% coverage, ruff clean.
- Frontend: **351 pass** (+10 across api/store/three views), 90.7% line coverage,
  typecheck + eslint clean, prod build OK.
- Docs: `api.md` §1.8 rewritten for the filters/history + new §1.8.1 task-names
  (subsections renumbered, cross-refs in `architecture.md` / `jobs-and-notifications.md`
  fixed); `frontend.md` gained a JobsView row and live-status notes on the list/detail rows.

## 6. Follow-ups (not done)

- Live **insertion** of brand-new document tiles into the list (explicit non-goal
  — avoids reordering an infinite-scroll list); a document uploaded in another tab
  still needs a navigation to appear.
- The two low-confidence races above, if they ever surface in practice, would
  want an `AbortController`/generation guard.
