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

## Ship: CI proof, deploy, and a post-deploy fix

**CI proof.** Merged to `main` as `3920d76`; pushed. CI went green on that
commit — backend (453 passed, 89%), frontend (300 passed), e2e
(`jobs-view.spec.ts` included), and compose-smoke all passed, so `promote`
retagged `ghcr.io/johnmathews/library:latest` from the `:3920d76` image.

**No new migration.** The live-events path is pure runtime `pg_notify` /
`LISTEN` — no schema change. The migration head is still `0008_ask_threads`
(from the Ask release), so `library-migrate` was a no-op and no pre-deploy
`pg_dump` was required (code-only change, per deployment.md §1.7).

**Deployed** to the `paperless` LXC (`/srv/apps`, compose project `apps`):
`docker compose up -d --pull always library-migrate library-webserver
library-worker`. Confirmed live — `/jobs` renders the Active/Recent tables
against real production data (document jobs link through to their documents).

**Post-deploy finding → fix: the email-poll heartbeat buried document work.**
On the live page the Recent list (limit 200, newest-first) was dominated by
`poll_email_inbox` rows — the `@job_app.periodic` email poll fires on a cron and
succeeds constantly with no `document_id`, pushing the actual
`extract_document` / `markdown_document` rows below the fold. It's the only
periodic task; every document task carries a `document_id`, so that's the clean
discriminator.

Fix (this follow-up): `GET /api/jobs` now hides document-less **succeeded**
system jobs by default — `WHERE (j.args ->> 'document_id') IS NOT NULL OR
j.status <> 'succeeded'` — so a *failed or running* poll still surfaces (you'd
see a broken poller), only the routine successes are dropped. `JobsView` gains a
**"Show system tasks"** checkbox that re-fetches with `?include_system=true` to
list everything. The SSE/`jobs` store is unaffected (it only tracks
document-bearing in-flight jobs, which always have a `document_id`). Tests:
backend `test_jobs_endpoint_hides_system_tasks_by_default` (succeeded poll
hidden, failed poll kept, opt-in shows both); frontend toggle spec; ruff +
type-check + eslint clean.

**Second pass: one row per document.** Hiding the heartbeats wasn't enough —
the list was still repetitive because it was *job*-centric, and one document
spawns several jobs (`process_document` + `generate_thumbnail`, plus a
`markdown_document` / `embed_document` / `extract_document` each from the
backfills), so the same document appeared up to ~5 times. `GET /api/jobs` now
collapses to **one row per document** via `DISTINCT ON
(COALESCE((args->>'document_id')::bigint, -id))` keeping each document's latest
job (document-less system rows key on `-id` so each stays its own row). The view
drops the per-job Task/Status columns in favour of a single document-stage badge
(Document · Status · Cost · Error). Backend
`test_jobs_endpoint_collapses_to_one_row_per_document`; ruff + type-check +
eslint + specs clean. Both passes need a redeploy to go live (new `:latest`).

**Still to verify on the live box** (not yet separately checked): the SSE
keep-alive through the reverse proxy — confirm `GET /api/events` streams and
isn't buffered/closed by the proxy in front of `library-webserver` (the one new
failure mode SSE introduces; the store degrades to a snapshot + backoff
reconnect, so a buffering proxy shows stale-until-refresh rather than a hard
break).
