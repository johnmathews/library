# SSE: single shared LISTEN connection with in-process fan-out

**Date:** 2026-06-28

## 1. Symptom

The post-merge `main` CI run for PR #14 (run id 28334713269) **hard-failed** the
`e2e` job: `e2e/projects.spec.ts` ("create a project, assign a document, filter
the dashboard by it") failed **both attempts** on the `tablet-webkit` project,
turning `main` red and blocking the `promote` (deploy) job. The surface symptom
was the test's cleanup `DELETE /api/documents/{id}` returning **HTTP 500**.

The backend logs showed the real cause **43×**:

```
asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
```

The traceback ran through `src/library/auth/deps.py` `current_user` →
`auth/service.py` `validate_session` → SQLAlchemy `pool.connect()`, which could
not acquire a connection. Because `current_user` is an `/api` include-level
dependency, it runs on **every** authenticated request — so once Postgres was
out of connections, *any* request that needed one 500'd: the cleanup `DELETE`,
and the many `GET /api/events` SSE streams also seen 500ing in the log.

## 2. Root cause

`src/library/api/events.py` opened **one dedicated asyncpg `LISTEN` connection
per SSE client** (`asyncpg.connect(dsn)` inside `document_event_stream`) and held
it for the stream's lifetime. `sse-starlette` only notices a client disconnect on
its next keep-alive ping (`_PING_SECONDS = 15`), so when a page navigated away
its stale asyncpg connection lingered up to ~15 s while the new page's
`EventSource` opened a fresh one immediately.

The e2e suite navigates rapidly against **one shared backend** (Playwright
`workers: 1`, serial), so stale SSE connections piled up faster than they were
released. Combined with the SQLAlchemy `QueuePool` (~15) they crossed Postgres's
default `max_connections` (100). It was intermittent because it depends on stream
overlap — it passed on #12/#13/#14-PR and tripped on #14-main. Unrelated to the
two fixes merged earlier that day (#13 Ask, #14 pdf-preview); a latent backend
resource leak that this run happened to expose.

## 3. The fix — process-wide events broker + in-process fan-out

Replaced per-client `LISTEN` with a single shared listener:
`src/library/events_broker.py` adds `EventsBroker`, which holds **one** asyncpg
connection LISTENing on `EVENTS_CHANNEL` for the whole process lifetime and fans
each NOTIFY payload out to a bounded per-client `asyncio.Queue`. Each SSE request
now `register()`s a queue on connect and `unregister()`s in a `finally` on
disconnect — **no per-client Postgres connection**. SSE Postgres usage is capped
at exactly one connection per process regardless of client count (also a real
production win: previously every open browser tab held its own connection).

- **Lifecycle.** The broker is owned by the FastAPI lifespan in
  `src/library/app.py`: started on startup (stored on `app.state.events_broker`),
  stopped on shutdown.
- **Public contract unchanged.** `GET /api/events` still streams `document`
  events whose data is the raw JSON `{document_id, event, status, title}`,
  `ping=15s`, `X-Accel-Buffering: no`; auth/CSRF unchanged (unauth still 401s
  before the stream opens).
- **Edge cases.** Resilient startup (a connect failure is logged and a background
  reconnect scheduled rather than raising — so a DB not-ready-yet never blocks
  app startup); `add_termination_listener` reconnect with capped backoff on a
  dropped connection; drop-oldest on a slow client's full queue so one slow
  consumer can't block the relay or the other clients; clean unregister on
  disconnect that never touches the shared connection.

## 4. Tests (TDD)

Rewrote `tests/test_events_sse.py` to the broker API and added proofs of the
fix, written failing first:

1. `test_single_connection_serves_many_clients` — spies on `asyncpg.connect` to
   assert **one** connection serves three concurrent clients, and a single
   NOTIFY fans out to all three.
2. `test_disconnect_unregisters_without_closing_shared_connection` — a client
   disconnect drops only its queue; `broker.running` stays true and a surviving
   client still receives events.
3. `test_document_event_stream_ignores_other_channels`, the relay happy path,
   `test_lifespan_starts_and_stops_shared_broker` (lifespan wiring), and
   `test_events_requires_authentication` (auth gate) round out the coverage.

Full backend suite: **670 passed**; `ruff check`/`format` clean repo-wide.

## 5. Why not just bump `max_connections`?

Considered as defense-in-depth but rejected as the primary fix: bumping the e2e
Postgres `max_connections` or the SQLAlchemy pool only raises the ceiling the
leak climbs toward. The fan-out removes the leak itself — the SSE connection sink
is gone — so no knob change was needed.
