# 1. Jobs view & live notifications

How Library surfaces background work to the user: a **Jobs view**, **toasts**,
and a **navbar running-jobs indicator**, all fed by a live Server-Sent Events
stream. For the API contract see [api.md](api.md) §1.8 / §1.8.5; for the
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
   (queued/running) and **Recent** (finished). Active rows show the document
   (linked), task name, status, and a started timestamp; Recent rows add a
   finished timestamp, run **duration** (from the job's `started_at`/`finished_at`
   events), extraction cost, and any error. The task name (humanised from the
   Procrastinate `task_name`, e.g. *Poll email inbox*) makes document-less
   **system tasks** legible — previously their row was an empty `—`. It refreshes
   automatically as documents finish. The **Recent** table has a **Columns**
   visibility menu (`[data-testid="jobs-columns-button"]`) — toggling a column
   persists to `localStorage['library:jobs-columns']` (merged over defaults, so
   new columns keep their default visibility); column widths are fixed via
   `table-fixed` + a `<colgroup>` of `clamp()` widths so the Document column no
   longer dominates the row. Both sections are **responsive**: a table from the
   `sm` breakpoint up (`hidden sm:block`) and a card/tile list below it
   (`sm:hidden`) — the cards lead with Document + Status and render the remaining
   *visible* columns as a meta grid (mirroring the journal-insights webapp
   convention).

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

### 1.2.1 Series-insight refresh job

When a document reaches `indexed` with both a sender and a kind, the pipeline
also defers `library.jobs.generate_series_insight(sender_id, kind_id)`
(best-effort, like the thumbnail defer). The task regenerates the cached
natural-language description for that `(sender, kind)` series and upserts it into
`series_insights` (see [ask.md §1.7](ask.md)). It is idempotent and skips quietly
for series too small to summarise; it does not toast.

## 1.3 Scope & non-goals

1. Toasts fire for document processing only — manual re-extract/embed/markdown,
   email polling, importer, and series-insight jobs appear in the Jobs view but
   do not toast.
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

## 1.5 Pushover push notifications (per-user)

Beyond the in-app toasts (which are session-bound and only visible while the web
app is open), each user can opt into **Pushover** push notifications that reach
their phone/desktop even when Library isn't open. This is a second sink on the
same document events, configured per-user in **Settings → Notifications**
(`PUT /api/settings/notifications`, see [api.md](api.md) §1.10.4).

### 1.5.1 Credentials

Each user supplies their **own** Pushover application token **and** user key
(plus an optional device). There is no server-level Pushover config — register a
free application at pushover.net and paste both values into the settings form.
Credentials are validated against Pushover's `users/validate` endpoint at save
time, so a typo is rejected (`422`) rather than silently dropped. They are stored
in the user's `preferences` JSONB and are **write-only** over the API (the read
model returns only `*_set` booleans). **Threat-model note:** the token/key are
stored in cleartext (they must be re-sent to Pushover, so they cannot be hashed
like API tokens) — consistent with how the app already holds config secrets;
a database compromise exposes them.

### 1.5.2 Events and recipient

Four opt-in event kinds: `document_success`, `processing_error`, `needs_review`
(processed but extraction flagged it low-confidence), and `duplicate`. A
notification is sent to the **document's owner** (`uploader_id`) only — so for a
family deployment, each person hears about their own documents. Documents with
no owner (consume-folder, paperless import) notify no one; email-in documents are
attributed to a user via their forwarding addresses (see
[ingestion.md](ingestion.md), "Email-in").

On a successful completion the dispatcher sends **one** push: the `needs_review`
message when the document was flagged *and* the owner opted into `needs_review`,
otherwise the `document_success` message (if subscribed). Errors go out at
Pushover **high priority** (bypassing the recipient's quiet hours); everything
else at normal priority.

### 1.5.3 Where it fires (`library.notifications`)

`document_success` / `processing_error` / `needs_review` are dispatched from the
**worker** at the pipeline's terminal transition (`library.jobs.advance_pipeline`).
`duplicate` is dispatched at **ingest time** (`library.ingest.ingest_file`),
because a duplicate never enters the worker pipeline. Both are **best-effort**:
the Pushover HTTP call (async `httpx`) runs after the document state is committed,
and any failure is logged and swallowed — it can never fail a job or an upload.
Set `LIBRARY_PUBLIC_BASE_URL` to deep-link each push back to its document.
