# 1. Pushover notifications & email-in user attribution

**Date:** 2026-06-23. **Engineering-team run:** `manual-20260623T182609Z`
(evaluation + plan in `.engineering-team/runs/`).

## 1.1 What shipped

Per-user **Pushover** push notifications as a second sink on the existing
document-event pipeline, plus **email-in sender→user attribution** so forwarded
documents get an owner and notify the right person. Six work units (W1–W6), all
additive — no DB migration (the per-user `preferences` JSONB already existed).

1. **W1 — Pushover client** (`src/library/notifications.py`): async `httpx`
   `send_pushover` + `validate_pushover`; both fold API/transport failures into a
   result object instead of raising. No official Pushover SDK exists, so a plain
   form-encoded POST (matching the embedding/importer client pattern).
2. **W2 — Settings** (`schemas.py`, `api/settings.py`): `NotificationEvent` enum
   (`document_success`, `processing_error`, `needs_review`, `duplicate`),
   secret-safe read model, and `PUT /api/settings/notifications`.
3. **W3 — Dispatch wiring** (`jobs.py`, `ingest.py`, `notifications.py`):
   completion/error from the worker, duplicate from the ingest path.
4. **W4 — Email attribution** (`email_ingest.py`, `config.py`, `schemas.py`):
   `resolve_sender_owner` + per-user `email_forward_addresses` +
   `LIBRARY_EMAIL_DEFAULT_OWNER` fallback.
5. **W5 — Settings UI** (`frontend/`): a third "Notifications" tab.
6. **W6 — Docs**: api.md, jobs-and-notifications.md, ingestion.md, README,
   `.env.example`.

## 1.2 Key decisions

1. **Per-user credentials, both supplied by the user.** Each user stores their
   own Pushover app token *and* user key (validated as independent credentials —
   confirmed against the live Pushover API). No server-level Pushover config.
2. **Owner-targeted.** A notification goes to the document's `uploader_id`.
   Owner-less sources (consume-folder, paperless import) notify no one;
   email is brought into the owned set by W4.
3. **Secrets cleartext in JSONB, never echoed.** The token/key must be re-sent
   to Pushover, so they can't be hashed like API tokens. Stored in `preferences`
   (consistent with the app's existing secret model); the read model returns only
   `pushover_*_set` booleans, and a blank/omitted token on save keeps the stored
   one (so saving only `events` never wipes credentials). Validated against
   Pushover's `users/validate` on enable, so a typo 422s instead of silently
   dropping every future push.
4. **One push per completion.** `dispatch_document_completion` sends the
   `needs_review` message when the doc was flagged *and* the owner subscribed,
   else `document_success` — never both. Errors go out at Pushover high priority.
5. **Two emission sites.** success/error/needs-review fire from the worker
   (`advance_pipeline`); `duplicate` fires at ingest time (`ingest_file`) because
   a duplicate never enters the worker pipeline. Both are best-effort: the
   Pushover call runs after the state is committed and any failure is logged and
   swallowed — it can never fail a job or an upload.
6. **Email attribution via per-user forwarding addresses**, not a global env map,
   to fit the per-user-settings model. Resolution happens in the async layer
   (`poll_mailbox_async`/`_ingest_candidate`), not the threaded sync poll loop.

## 1.3 Review findings addressed

1. **Code review flagged a potential critical bug:** accessing `existing.uploader`
   in the ingest duplicate path *after* `session.commit()` could trigger an
   expired-attribute lazy load → `MissingGreenlet` → silently swallowed → no
   duplicate push. **Verified non-issue:** every session factory uses
   `expire_on_commit=False` (`db.py:25`, both conftest sessions), so the
   selectin-loaded `uploader` stays valid post-commit. Added an end-to-end guard
   test (`test_ingest_api.py::test_duplicate_upload_notifies_owner`) that drives
   the real HTTP→ingest→commit→dispatch path, and a comment documenting the
   invariant.
2. **Doc freshness audit** caught three stale/incomplete claims, all fixed:
   api.md §1.10.4 wrongly said "one push for each subscribed event" (it's one push
   per completion); the §1.10 heading/intro omitted the notifications endpoint;
   and ingestion.md + the `email_ingest` docstring still said email ingests with
   "no uploader". (A pre-existing stale `GET /api/jobs` shape in ingestion.md's
   HTTP-API block was noted as out of scope.)

## 1.4 Tests & config

1. **Backend:** 499 passing (up from the prior baseline; +~40 new across
   `test_notifications`, `test_settings_api`, `test_jobs_pipeline`,
   `test_email_ingest`, `test_ingest_api`), 90% total coverage (gate 85%). New
   modules: schemas 99%, settings 93%, email_ingest 91%, notifications 89%.
2. **Frontend:** 306 passing (43 files), lint + type-check clean.
3. **New config:** `LIBRARY_PUBLIC_BASE_URL` (deep-link pushes to a document),
   `LIBRARY_EMAIL_DEFAULT_OWNER` (owner for unmatched email senders).

## 1.5 Follow-ups / known limitations

1. Consume-folder and paperless-import documents remain owner-less and notify no
   one (explicit non-goal this round).
2. `resolve_sender_owner` scans all users in Python — fine for a family
   deployment; switch to a JSONB `@>` containment query (+ GIN index) if the user
   table ever grows large.
3. Forwarding from your own mail client rewrites `From:` to your address — users
   must list the addresses they forward *from*. Documented in ingestion.md.
