# W14 — Email-in ingestion

**Date:** 2026-06-11
**Unit:** W14 (improvement plan §1.3.14)

## What landed

- `src/library/email_ingest.py`: synchronous IMAP poller
  (`poll_mailbox`) over imap-tools, plus `poll_mailbox_async`, which
  runs it in a worker thread and marshals each ingest back onto the
  event loop. Attachments go through the existing `ingest_file`
  service with `source=email`.
- `library.jobs.poll_email_inbox`: Procrastinate periodic task
  (`@job_app.periodic`), cron built from `LIBRARY_EMAIL_POLL_MINUTES`
  via `email_poll_cron()` (step clamped to cron's 1–59 minute field).
  Instant no-op while `LIBRARY_EMAIL_HOST` is unset.
- Settings block (`LIBRARY_EMAIL_*`): host/port/username/password
  (SecretStr), folder, processed folder, poll minutes, comma-separated
  sender allowlist (`NoDecode` + before-validator, lowercased).
- `ingest_file` gained an optional `extra_event_detail` parameter —
  the email channel records `email_from` / `email_subject` /
  `email_message_id` in the `received` / `duplicate_upload` event.
  All existing callers unchanged.
- Tests (`tests/test_email_ingest.py`, 13 cases): fake MailBox at the
  imap-tools boundary holding real `MailMessage.from_bytes` messages
  built from multipart RFC822 bytes; real testcontainers Postgres.
- Docs: ingestion.md "Email-in" section (flow, decisions, provider
  table incl. Gmail app-password note, env table rows, event-detail
  note); architecture.md W14 row flipped to done.

## Decisions

- **Idempotency = folder move, not seen flags.** Processed messages
  move to `LIBRARY_EMAIL_PROCESSED_FOLDER` (created on demand); the
  poller fetches `ALL` with `mark_seen=False` so a human reading the
  mailbox cannot break it. sha256 dedup in `ingest_file` is the
  second line of defence (duplicate attachment in a new mail →
  `duplicate_upload` event, mail still filed).
- **Allowlist rejections stay in the inbox** (logged each poll) rather
  than being moved — a sender typo should be visible, not silently
  archived.
- **Per-message isolation:** any exception leaves that mail in place
  for the next poll and the run continues; per-attachment
  `IngestError` (e.g. soft-deleted duplicate) skips the attachment but
  still files the mail.
- **Body-only mails** are moved without creating a document
  (attachments only in v1, per plan; HTML→PDF deferred).
- **Threading model:** imap-tools is sync, so the periodic task does
  `asyncio.to_thread(poll_mailbox, ...)`; ingest calls hop back to the
  worker loop via `run_coroutine_threadsafe` so the DB session and
  Procrastinate connector stay loop-affine.
- **Lazy import in jobs.py** for `email_ingest` (it imports
  `library.ingest`, which imports `library.jobs` — top-level would
  cycle).

## Verification

- `uv run pytest` green (full suite), ruff check + format clean,
  `uv lock --check` ok. imap-tools 1.13.0 added via `uv add`.
