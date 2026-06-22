# 1. Jobs view + live job notifications (2026-06-23)

Built via the engineering-team workflow (evaluate → plan → develop → wrap-up).
Run dir: `.engineering-team/runs/manual-20260622T221914Z/`.

## 1.1 What shipped

A user-facing surface for background work, in eight work units (W1–W8):

1. A **Jobs view** (`/jobs`) — Procrastinate jobs split into Active / Recent,
   each enriched with the document's pipeline stage, status, extraction cost,
   and any error; rows link to the document.
2. A **navbar running-jobs indicator** (spinner + count badge + dropdown) that
   appears in `AppHeader` only while documents are processing.
3. **Toasts** on the document-processing lifecycle (success on `indexed`, error
   on `failed`), via a new generic `notifications` store + `ToastContainer`.
4. A **live transport**: a new SSE endpoint `GET /api/events` fed by Postgres
   `LISTEN/NOTIFY`, consumed by a Pinia `jobs` store over `EventSource`.
5. An **enriched `GET /api/jobs`** (joined to `documents` + latest failed event).

## 1.2 Key decisions

1. **SSE over Postgres `LISTEN/NOTIFY`, not polling or WebSocket.** Updates are
   strictly server→client, so SSE is the right fit. The worker emits
   `pg_notify('library_doc_events', …)` on each pipeline transition / failure;
   the api process holds a dedicated asyncpg `LISTEN` connection and relays each
   notification. The worker→api hop crosses processes via Postgres itself — both
   already share `LIBRARY_DATABASE_URL`.
2. **`sse-starlette`** for the endpoint — it handles ping keep-alives and
   client-disconnect teardown correctly (both easy to get wrong hand-rolled).
3. **Toasts fire for document processing only.** Other job types (manual
   re-extract/embed/markdown, email poll, importer) appear in the Jobs view but
   stay quiet. The toast store is generic, so other call sites can use it later.
4. **Jobs view is read-only** — no cancel/retry/requeue this round.

## 1.3 Implementation notes & gotchas

1. **Testing SSE through httpx's `ASGITransport` hangs** on an open-ended
   stream (it buffers infinite responses). Resolved by factoring the
   NOTIFY→SSE relay into a standalone async generator
   (`document_event_stream(dsn)`) and testing that directly against the test
   Postgres; the route's auth gate (401) is still checked through the app. This
   is the durable pattern for SSE unit tests here.
2. **Code-review fix — notify session isolation.** The first cut had
   `notify_document_event` run `commit()`/`rollback()` on the *shared* pipeline
   `AsyncSession`, so its "decoupled" docstring wasn't true. Changed it to take
   the `session_factory` and open its own short-lived session, so a NOTIFY
   failure is fully isolated and can never strand a document.
3. **`ruff` F402** — the module already used `text` as a loop variable in
   `run_embed`; importing SQLAlchemy's `text` shadowed it. Aliased the import to
   `sql_text` rather than touch unrelated code.
4. **Frontend SSR/jsdom guard** — `DefaultLayout` connects the jobs store on
   mount, but `EventSource` is undefined in jsdom; the store no-ops when it's
   absent so existing component tests (which mount `App`/`DefaultLayout`) don't
   break.
5. **Toast dedup** — terminal toasts are deduped per document id; the dedup set
   is cleared on `disconnect()` to bound it to one connected session
   (code-review follow-up).

## 1.4 Tests & verification

1. **Backend:** new `tests/test_notify.py` (NOTIFY emission via a real asyncpg
   listener), `tests/test_events_sse.py` (relay generator + auth gate), and an
   enrichment test in `tests/test_ingest_api.py`. Full suite: **453 passed**
   (excluding `slow_ocr`), **89%** coverage.
2. **Frontend:** new specs for the notifications store, `ToastContainer`, the
   `jobs` SSE store, the `AppHeader` indicator, and `JobsView`. Full suite:
   **300 passed**, type-check + eslint clean.
3. **e2e:** `frontend/e2e/jobs-view.spec.ts` (upload → navbar indicator → toast →
   `/jobs` row), self-skipping without the compose stack like the rest of the
   e2e suite; runs in CI.

## 1.5 Dependency change

Added `sse-starlette` (backend) for the SSE endpoint.

## 1.6 Docs

`docs/jobs-and-notifications.md` (new feature doc), `docs/api.md` §1.8 (enriched
jobs) + §1.8.4 (SSE), `docs/architecture.md` §1.4.1 (live events), plus CHANGELOG
and README entries.
