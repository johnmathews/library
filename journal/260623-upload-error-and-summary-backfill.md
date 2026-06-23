# Upload validation-error fix + summary backfill command

**Date:** 2026-06-23
**Branch:** `main`

---

## What shipped

Two small, independent changes driven by a user report ("I can't upload — it
says *select at least one file* even after I pick a document") and a follow-up
request (auto-summary works for new docs; backfill the old ones).

1. **`UploadView` clears the validation error on selection.**
   `frontend/src/views/UploadView.vue` now `watch`es the selected files and
   clears `fileError` as soon as a non-empty selection arrives.
2. **`library backfill-summaries` CLI command.** `src/library/cli.py` enqueues
   `extract_document` for every indexed, non-deleted document with
   `summary IS NULL`. Mirrors `backfill-markdown`/`backfill-embeddings`.

---

## The upload bug — systematic debugging, and what it was *not*

The report was intermittent and I could not reproduce the hard failure. Rather
than guess, I tested each hypothesis and recorded the result:

- **Deployed ≠ `main`?** Ruled out. A fresh `npm run build` produced
  `AppErrorSummary-RH2PfOh5.js` — byte-identical to the hash the user's browser
  loaded (seen in Loki logs on the `paperless` LXC). The deployment *is* `main`.
- **`<label for>` + nested `<input>` double-fires the file dialog?** Ruled out.
  A Playwright click-counter repro fired the input's click exactly **once** in
  both Chromium *and* Firefox (the user's browser). The "uploaded twice" turned
  out to be a normal multi-file upload of two different documents.
- **Stale service-worker cache?** Ruled out. No SW is registered; the SPA serves
  `index.html` with `Cache-Control: no-cache` and immutable hashed assets.
- **Unit-level model wiring?** Ruled out. All 16 upload unit tests pass.

What the investigation *did* find is a real latent UX bug: `fileError` was set on
a premature submit and only cleared by the **next successful submit** — never
when the user actually selected a file. So a stale "select a file" error could
sit on screen over a valid selection, looking exactly like the reported symptom.
That is the bug that got fixed (TDD: failing test first, then the `watch`).

The hard failure may have simply been clicking *Upload* before the file picker
completed. If it recurs, the next step is the Firefox console at the moment of
failure — but I didn't want to invent a root cause without evidence.

---

## Backfill design

Auto-summary already works: the metadata-extraction stage produces `summary`
for every new upload. So old blank docs only need the **same** extraction re-run,
which is exactly what the existing `extract_document` Procrastinate task does
(honours `extra["user_edited_fields"]`, respects the daily budget). The command
is therefore a thin query-and-enqueue, consistent with the sibling backfills.
A summary-only generation path was considered and rejected — it would diverge
from how new docs get summaries, for no benefit.

Query: `deleted_at IS NULL AND status = INDEXED AND summary IS NULL`. The
`INDEXED` filter (which the sibling backfills omit) avoids racing documents that
are still mid-pipeline and will get a summary naturally.

---

## Verification

- `frontend`: 275 vitest tests pass; `vue-tsc` clean; `eslint` clean.
- `backend`: full suite **449 pass** (2 new in `test_cli.py`:
  enqueues-without-summary, respects-`--limit`); coverage 90% total / `cli.py`
  95%; `make lint` (ruff) clean.

Docs updated: `docs/ingestion.md` (§"Backfill summaries"), `docs/deployment.md`
(§1.7 step 8), `CHANGELOG.md` (Added + new Fixed section). Doc freshness audit
(during `/done`) cross-checked the new command + UploadView behavior against
ingestion/deployment/frontend docs, README and env-var table — all current, no
stale claims.
