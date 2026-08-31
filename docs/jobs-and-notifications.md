# Jobs view & live notifications

**Status:** active. **Last updated:** 2026-08-31 (the legacy series stack was deleted: dropped the series-insight refresh job section and renumbered §1.2.2/§1.2.3 up to §1.2.1/§1.2.2 — nothing cites those numbers; corrected §1.6.1's best-effort deferral count, five → two; dropped series links from the purge cascade and series-insight from the no-toast list). Earlier: 2026-08-12 (documentation verification sweep: the Jobs view is one ordered table, not an Active/Recent split — corrected the column set and refresh trigger and documented the three filters; fixed the ownerless-document examples, the `duplicate` dispatch site and the document-less push link titles).
**Last verified:** 2026-08-31 — method: partial, scoped to §1.2, §1.3 and §1.6.1. Re-derived the task list from `src/library/jobs.py` (`grep -n '@job_app'`): no series task remains and the four `@job_app.periodic` tasks §1.6 names are still exactly `sweep_stalled_jobs`, `backfill_budget_skipped`, `purge_deleted_documents`, `poll_email_inbox`; counted the `_defer_best_effort` call sites — two, `generate_thumbnail` and `classify_document_matters`; and confirmed by `grep -rn '§1\.2\.[123]' docs/ src/ frontend/src/` that no document or source file cites this section's subsection numbers, which is why the renumber is safe here and was not done in `api.md`. Nothing else was re-checked this pass; the rest carries forward the 2026-08-12 verification, whose method was: checked every event name, channel, endpoint, payload field, task name, cron, retry-exception class, `data-testid` and storage key against `jobs.py`, `events_broker.py`, `api/events.py`, `api/jobs.py`, `notifications.py` and the Jobs view, jobs/notifications stores, toast container and header indicator in `frontend/src`.

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
3. **Jobs view (`/jobs`).** A dashboard of background jobs as a **single ordered
   table** — active (queued/running) rows sort to the top and carry a spinner,
   finished rows follow in the server's order. There is no Active/Recent split
   and no Started column: the columns are `document, task, status, finished,
   duration, cost, error`, with `started_at` used only to compute **duration**.
   The task name (humanised from the Procrastinate `task_name`, e.g. *Poll email
   inbox*) makes document-less **system tasks** legible — previously their row
   was an empty `—`. It refetches on **every** document event (not only terminal
   ones) and on every filter change. Three controls sit above the table: a
   **Show system tasks** checkbox (`include_system`), a **Task type** filter
   (options from `GET /api/jobs/task-names`) and a **Document** filter, which
   switches the server into that document's uncollapsed history. Both filters
   are URL-query-backed, so a filtered view is deep-linkable. The table has a
   **Columns** visibility menu (`[data-testid="jobs-columns-button"]`) —
   toggling a column persists to `localStorage['library:jobs-columns']` (merged
   over defaults, so new columns keep their default visibility); widths are
   fixed via `table-fixed` + a `<colgroup>` (`clamp()` for the document and
   error columns, fixed `rem` for the rest) so the Document column no longer
   dominates the row. It is **responsive**: the table from the `sm` breakpoint up
   (`hidden sm:block`) and a card/tile list below it (`sm:hidden`) — the cards
   lead with Document + Status and render the remaining *visible* columns as a
   meta grid (mirroring the journal-insights webapp convention).

## 1.2 How it works

1. **Emit (worker).** Each pipeline transition and failure emits a Postgres
   `NOTIFY` on `library_doc_events` (`library.jobs.notify_document_event`),
   best-effort so a notify failure never fails the job.
2. **Stream (api).** A single process-wide `EventsBroker`
   (`library.events_broker`) holds *one* asyncpg `LISTEN` connection for the
   whole process lifetime and fans each notification out to an in-process queue
   per connected client. `GET /api/events` (`library.api.events`) registers a
   queue on connect, relays each fanned-out payload as an SSE `document` event,
   and unregisters on disconnect — no per-client Postgres connection.
   Authenticated by the session cookie (a GET is CSRF-safe), with ~15 s
   keep-alive pings and `X-Accel-Buffering: no` to defeat proxy buffering.
3. **Consume (frontend).** The `jobs` Pinia store
   (`frontend/src/stores/jobs.ts`) opens one `EventSource`, seeds an initial
   snapshot from `GET /api/jobs`, tracks in-flight documents, reconnects with
   capped exponential backoff, and routes terminal events to the generic
   `notifications` toast store. The store is connected once in `DefaultLayout`
   (so it runs only for authenticated routes) and torn down on sign-out.

### 1.2.1 Crash recovery (stalled-job sweeper)

A hard-killed worker (OOM/`SIGKILL`/redeploy mid-stage) leaves its in-flight
`process_document` job in `doing` with the document stranded in a non-terminal
status — no exception fires, so nothing re-queues it. The periodic
`library.jobs.sweep_stalled_jobs` task recovers these: it re-enqueues
`process_document` jobs whose worker heartbeat has gone stale (see
[ingestion.md](ingestion.md), "process_document — pipeline"). The pipeline
resumes idempotently, so a recovered document simply continues from where it
died and eventually reaches `indexed`. This is why the Jobs view needs no manual
requeue button for the common crash case.

The resume **re-runs the stage the document is sitting in**. A document's
`status` records the stage that was *entered*, not one that completed — the
transition is committed and `NOTIFY`d before the stage's work runs, which is
exactly what makes the live progress signal above possible ("now doing X"). The
flip side is that a hard kill mid-stage leaves the status advanced and the work
undone, so the resume must redo the entered stage rather than skip past it;
otherwise a document killed inside OCR would sail to `indexed` with no text and
report success. Every stage hook is written to tolerate that re-run, and the
stages that call Anthropic (extract, markdown, the repair pass, matter
classification) each carry a completion guard so a recovered document re-runs
without being billed twice — the recovery shows up as an `already_extracted` /
`already_generated` skip in the document's events rather than a second charge
(see [ingestion.md](ingestion.md), "process_document — pipeline").

### 1.2.2 Recently-Deleted purge job

`library.jobs.purge_deleted_documents` is a daily periodic task (small hours)
that completes the soft-delete lifecycle: it hard-deletes documents whose
`deleted_at` is older than `LIBRARY_DELETED_RETENTION_DAYS` (default 30),
removing the row (chunks, comments, pages, events, note versions, and
tag/project links cascade at the DB level) and unlinking the on-disk
original and derived artifacts. File unlink is safe and unconditional because
`documents.sha256` is unique — exactly one row references each stored file. The
task is gated by `LIBRARY_DELETED_PURGE_ENABLED` (default on): with it off,
soft-deleted documents stay in the Recently-Deleted area indefinitely and remain
restorable. See [api.md §1.6](api.md) for the delete/restore/list endpoints.

## 1.3 Scope & non-goals

1. Toasts fire for document processing only — manual re-extract/embed/markdown,
   email polling and importer jobs appear in the Jobs view but do not toast.
2. The Jobs view is read-only: no cancel/retry/requeue actions. Retries are
   automatic where configured — see §1.6, which also says which tasks
   deliberately do not retry.
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
(`PUT /api/settings/notifications`, see [api.md](api.md) §1.10.5).

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

Five opt-in event kinds: `document_success`, `processing_error`, `needs_review`
(processed but extraction flagged it low-confidence), `duplicate`, and
`email_held` (an inbound email was held for review instead of filed). A
notification is sent to the **document's owner** (`uploader_id`) only — so for a
family deployment, each person hears about their own documents. A document with
no owner notifies no one — but the consume folder and the paperless importer are
**not** examples of that: both attribute an owner from
`LIBRARY_IMPORT_DEFAULT_OWNER` when it is set (and only leave documents ownerless
when it is not). Email-in documents are
attributed to a user via their forwarding addresses (see
[ingestion.md](ingestion.md), "Email-in").

On a successful completion the dispatcher sends **one** push: the `needs_review`
message when the document was flagged *and* the owner opted into `needs_review`,
otherwise the `document_success` message (if subscribed). Errors go out at
Pushover **high priority** (bypassing the recipient's quiet hours); everything
else at normal priority.

**Dropped email attachments** reuse the `processing_error` opt-in: when an
inbound email has attachments that could not be added, the poller sends an
"Attachments not added" push (high priority) listing the dropped filenames. This
one is **document-less** — the content never became a document — so it resolves
the owner from the email sender directly rather than from a document row (see
[ingestion.md](ingestion.md), "Email-in"). No new opt-in key: subscribing to
`processing_error` covers it.

**Held emails** get their own dedicated opt-in key, `email_held`: when the
poller holds an inbound email for review instead of filing it (see
[ingestion.md](ingestion.md), "Held for review"), a normal-priority push
("Email held for review", deep-linked to the `/held-emails` queue) goes to the
owner resolved from the sender — also document-less, at-most-once per hold
(the skip-if-exists retry path never re-notifies). A held email deliberately
does **not** fire the attachments-dropped push: the held queue and this event
are its surface. Normal priority because a hold is review work, not an error.

### 1.5.3 Where it fires (`library.notifications`)

`document_success` / `processing_error` / `needs_review` are dispatched from the
**worker** at the pipeline's terminal transition (`library.jobs.advance_pipeline`).
`duplicate` is dispatched at **ingest time** from
`library.ingest._duplicate_result` — the shared exit both duplicate paths in
`ingest_file` return through — because a duplicate never enters the worker
pipeline. The document-less
**attachments-dropped** push fires from the **email poller**
(`dispatch_attachments_dropped_notification`, once per message with drops, only
after the message's successful Processed move), and the document-less
**email-held** push (`dispatch_email_held_notification`) fires from the same
poller after a fresh `held_emails` row commits. All
are **best-effort**: the Pushover HTTP call (async `httpx`) runs after the
relevant state is committed, and any failure is logged and swallowed — it can
never fail a job, an upload, or a poll.

### 1.5.4 Deep-linking pushes to the document

Set `LIBRARY_PUBLIC_BASE_URL` to the web app's public URL (no trailing slash,
e.g. `https://library.example.com`) to make every push carry a link straight to
what it refers to. The dispatcher attaches it as Pushover's supplementary URL:
for the **document-scoped** pushes that is `url_title` "Open in Library"
pointing at `{LIBRARY_PUBLIC_BASE_URL}/documents/{id}` — so tapping the
notification on your phone opens that document in the app. The two
document-less pushes link elsewhere: attachments-dropped uses "Open Library"
(the app root), and the held-email push uses "Open held emails", pointing at
`{LIBRARY_PUBLIC_BASE_URL}/held-emails`.

When the variable is **unset** the feature silently no-ops: notifications still
go out, but without a link. Because that looks like a bug ("my notifications
have no link"), the API logs a one-line `WARNING` at startup whenever it is
unset. Set it on the live host (the deployment's env/compose file) to turn the
links on — no other configuration is required, the linking code is always on.

## 1.6 Retry policy

Procrastinate does **not** retry by default: an unhandled exception marks a job
`failed` permanently. Every task therefore used to lose its work to a single
network blip — an Anthropic rate limit, a restarting embedder, a Postgres
failover — with no second attempt.

Tasks that depend on a network service now carry `retry=TRANSIENT_RETRY`
(`library.jobs`): five attempts with exponential backoff (~4s, 8s, 16s, 32s, so
roughly a minute in total), which covers a container restart or a failover
window without keeping a genuinely broken job alive for hours.

`retry_exceptions` is an **allowlist**, and that direction is the design. A
denylist would retry anything not yet thought of, so a deterministic bug — a
`ValueError`, an unsupported MIME type, a parse failure, a pydantic
`ValidationError` — would burn all five attempts and land in `failed` a minute
later with the identical error. With an allowlist those fail on the first
attempt, which is faster *and* more truthful. The allowlist is: Anthropic
`APIConnectionError`/`APITimeoutError`/`RateLimitError`/`InternalServerError`
(deliberately not `APIStatusError`, which would include 4xx), `httpx.TransportError`
(not `HTTPStatusError`, which is not a subclass of it), SQLAlchemy
`OperationalError` and `InterfaceError`, and `EmbeddingError`.

`DBAPIError` is deliberately **excluded** despite being the obvious "database
problem" class: it is the parent of `IntegrityError`, `DataError`,
`ProgrammingError` and `NotSupportedError`, so allowlisting it would retry
constraint violations and SQL bugs — exactly what an allowlist exists to
prevent.

Two groups do not retry:

- **`generate_thumbnail`** — a deterministic render of one file. A failure means
  a bad file, not bad luck, so retrying repeats the same outcome. It is
  best-effort by design.
- **The four `@job_app.periodic` tasks** (`sweep_stalled_jobs`,
  `backfill_budget_skipped`, `purge_deleted_documents`, `poll_email_inbox`) —
  the next scheduled tick *is* the retry. `poll_email_inbox` additionally carries
  both `queueing_lock` and `lock`, so a retry would be a second recovery
  mechanism racing the schedule for no gain.

Every task in the module is pinned to an explicit decision by
`tests/test_jobs_pipeline.py::test_retry_policy_per_task`, and a new task with no
entry fails `test_retry_policy_per_task_is_complete`. That matters because
Procrastinate's silent default makes an *omitted* decision indistinguishable
from a considered "no".

### 1.6.1 Deferrals that fail before the job exists

A retry policy cannot help when the `defer_async` call itself fails — there is
no job to retry. **Two** follow-up jobs are deferred best-effort after their
document's own work has committed (thumbnail and matter classification), and a
queue error there must never strand an already-processed document in `failed`.
There were five until the legacy series stack was deleted; the other three were
the series-insight refresh, series autocontinue and the semantic-group
membership eval, and they went with it. The guard below is unchanged — it is a
property of `_defer_best_effort`, not of any particular caller.

Previously each of those logged a warning and moved on, which meant the
observable result was a document that quietly never got its thumbnail or its
matter classification, with nothing on the document to say why. They now also
record a **`job_defer_failed`** ingestion event carrying the task name and the
error, so the loss appears on the document's own timeline and is queryable.
Recording the event is itself guarded — noting a loss must not become a larger
one by failing the document.
