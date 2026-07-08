# Email attachment ingest selection

When a forwarded email arrives with `[body, attachment 1 … attachment N]`, only
some items are worth filing. Previously the poller ingested **every** supported
attachment as its own document and used the body only as a zero-attachment
fallback — so signature logos, tracking pixels, footer banners, and calendar
invites all became first-class documents, and contentless cover notes ("FYI see
attached") could be filed too. This adds a layered selection pipeline that
removes the noise while never dropping a real document, plus a decision trace so
the whole process is debuggable from the logs.

Run: `.engineering-team/runs/manual-20260708T072428Z/` (evaluation + plan).

## Design

Layered, mirroring the codebase's existing "cheap deterministic rules that flag,
expensive LLM only when budget-justified" philosophy. Overriding invariant:
**never lose a real document** — nothing is deleted; an item is ingested,
ingested-and-flagged (`needs_review`), or recorded as a recoverable/quiet drop,
and the original mail always survives in the IMAP `Processed` folder.

1. **Deterministic noise gate** (`_noise_reason`, always on, `LIBRARY_EMAIL_FILTER_NOISE_ENABLED`).
   In `_classify_attachments`, before the allowed-type check: `signature_image`
   (inline / `cid:`-referenced images), `tiny_image` (dimensions authoritative,
   byte-size only a fallback for undecodable images), `non_document_type`
   (declared `text/calendar` / vCard / PKCS7 / TNEF — matched on the *declared*
   type because calendar/vCard bytes sniff as the allowed `text/plain`). Drops
   are **quiet**: recorded in the trace and counted (`attachments_filtered`) but
   not surfaced, so a footer logo never flags its real sibling.
2. **Body-substance gate** (`_body_substance` / `_body_candidate`). Body stays
   fallback-only, but quoted replies / signatures / mobile footers are stripped
   and the remainder must clear 40 words **or** 240 chars. The cleaned body is
   what's filed.
3. **Ambiguous-item flag** (`email_item_ambiguous` finding in `validation.py`).
   `extra["email_selection"].verdict ∈ {ambiguous, probably_noise}` → a finding →
   `needs_review`. This is the safety net: the "maybe" pile is ingested-and-flagged.
4. **Optional per-email LLM label pass** (`email_label.py`, **default OFF**). One
   Haiku call per email labels each *surviving* attachment `keep`/`probably_noise`
   (body handled by layer 2, passed only as context). `probably_noise` → the
   layer-3 flag, **never a drop**. Fail-open, budget-gated like extraction.
5. **Decision trace** (observability, a user-requested first-class concern). One
   always-on greppable `email-selection` log line per email (covers the
   zero-document case), plus a persisted `email_selection` `IngestionEvent` on
   each produced document (visible in the history "Show all events").

## Key decisions / discoveries

1. **Record vs surface, split.** The engineers proposed making noise drops fully
   silent (not even recorded). To honour "never lose a real document" I split the
   two concerns: the decision trace is the recoverable record; the `needs_review`
   + push path stays for genuine user-facing drops only. No change to the existing
   `email_attachments_dropped` rule.
2. **`IngestionEvent.document_id` is NOT nullable**, but the LLM label pass runs
   *before* any document exists. So the label budget event (`email_label_completed`,
   summed by a generalised `todays_spend_usd(session, event=...)`) is written on
   the first produced document, not at call time. Consequence: an email that files
   no *new* document (nothing, or only duplicates) under-counts its label spend by
   at most one cheap call. Deliberate; documented. No migration needed — everything
   rides existing JSONB `extra` + `IngestionEvent`.
3. **Tiny-image rule uses dimensions first, bytes as fallback.** An earlier
   byte-size-first version would have wrongly filtered a legitimate small-but-
   normal-dimension image (e.g. a simple 200×200 diagram). Dimensions are the real
   "is this an icon" signal; byte size only applies when the image won't decode.
4. **Fail-open widened after code review.** The reviewer caught that
   `todays_spend_usd` (budget read) and `estimate_cost_usd` sat *outside* the
   `try` in `label_email_items` — a DB hiccup or unpriced-model `KeyError` would
   have propagated up through the pre-ingest label call and aborted the whole
   message, leaving real attachments un-ingested. The entire body is now guarded
   so the labeller can only ever return a `LabelOutcome`, never raise. Regression
   test added (`test_budget_read_failure_fails_open`).

## Tests & docs

- Full backend suite green: **1035 passed**. Coverage 88% (85% gate). New tests
  across `test_email_ingest.py` (noise gate, trace log + persisted event incl. the
  zero-document case, body substance + `_body_substance` units, LLM-flag wiring),
  `test_email_label.py` (schema, budget gate, fail-open incl. the budget-read
  failure, index-mismatch), `test_extraction_validation.py` (the new finding).
- Behaviour-change fixtures updated deliberately: a ~56-byte test PNG (now
  enlarged past the tiny threshold) and several short-body fixtures (now use a
  substantive body) — flagged in the plan, not surprises.
- `docs/ingestion.md`: rewrote the "Email-in" flow, added an "Email item
  selection" reference + a debug/triage runbook (trace log format, grep/Loki
  query, action-per-reason table), and added the `email_item_ambiguous` row to the
  Validation rules table. Doc-freshness audit run adversarially; 4 findings fixed
  (stale module docstring, missing rule row, a never-emitted `label_skipped`
  verdict in the docs, table verdict gaps).

## Config added (all `LIBRARY_EMAIL_*`)

`FILTER_NOISE_ENABLED` (true), `FILTER_TINY_IMAGE_MAX_BYTES` (4096),
`FILTER_TINY_IMAGE_MAX_EDGE_PX` (64), `LABEL_ENABLED` (false), `LABEL_MODEL`
(`claude-haiku-4-5`, added to `_PRICED_MODEL_FIELDS`), `LABEL_DAILY_BUDGET_USD`
(2.0), `LABEL_BODY_SNIPPET_CHARS` (1000).
