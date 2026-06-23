# 1. Jobs view & live notifications

How Library surfaces background work to the user: a **Jobs view**, **toasts**,
and a **navbar running-jobs indicator**, all fed by a live Server-Sent Events
stream. For the API contract see [api.md](api.md) §1.8 / §1.8.4; for the
worker→api bridge see [architecture.md](architecture.md) §1.4.1.

## 1.1 What the user sees

1. **Navbar indicator.** While any document is processing, a spinning indicator
   with a count badge appears in the header. Its dropdown lists the in-flight
   documents (title + current stage) and links to each document and to the Jobs
   page. It disappears when nothing is running.
2. **Toasts.** When a document finishes, a toast is raised — a green “Document
   processed” on success, a red “Processing failed” on failure (errors stay until
   dismissed; successes auto-dismiss). Each links to the document. Toasts cover
   the **document-processing lifecycle only**; other job types stay quiet.
3. **Jobs view (`/jobs`).** A dashboard of background jobs split into **Active**
   (queued/running) and **Recent** (finished). Rows show the document (linked),
   task, Procrastinate status, pipeline stage, extraction cost, and any error.
   It refreshes automatically as documents finish.

## 1.2 How it works

1. **Emit (worker).** Each pipeline transition and failure emits a Postgres
   `NOTIFY` on `library_doc_events` (`library.jobs.notify_document_event`),
   best-effort so a notify failure never fails the job.
2. **Stream (api).** `GET /api/events` (`library.api.events`) holds a dedicated
   asyncpg `LISTEN` connection and relays each notification as an SSE `document`
   event. Authenticated by the session cookie (a GET is CSRF-safe), with ~15 s
   keep-alive pings and `X-Accel-Buffering: no` to defeat proxy buffering.
3. **Consume (frontend).** The `jobs` Pinia store
   (`frontend/src/stores/jobs.ts`) opens one `EventSource`, seeds an initial
   snapshot from `GET /api/jobs`, tracks in-flight documents, reconnects with
   capped exponential backoff, and routes terminal events to the generic
   `notifications` toast store. The store is connected once in `DefaultLayout`
   (so it runs only for authenticated routes) and torn down on sign-out.

## 1.3 Scope & non-goals

1. Toasts fire for document processing only — manual re-extract/embed/markdown,
   email polling, and importer jobs appear in the Jobs view but do not toast.
2. The Jobs view is read-only: no cancel/retry/requeue actions (jobs are
   retried by Procrastinate per its own policy).
3. Transport is one-way SSE, not a WebSocket; events are not replayed on
   reconnect — the snapshot fetch covers the gap.

## 1.4 Deployment note

The worker and api must share one Postgres database for `NOTIFY` to cross the
process boundary — the standard compose deployment already wires both to the
same `LIBRARY_DATABASE_URL`. If a reverse proxy is placed in front of the api,
ensure it does not buffer `text/event-stream` responses (the endpoint already
sends `X-Accel-Buffering: no` for nginx).
