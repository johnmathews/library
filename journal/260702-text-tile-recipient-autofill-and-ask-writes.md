# Text-tile facsimile, recipient auto-fill from To:, and Ask metadata writes

Three feedback-driven features shipped together, following up on the tile
facsimile / hero-Ask work from
[260701-tile-metadata-facsimile-and-hero-ask](260701-tile-metadata-facsimile-and-hero-ask.md).

## 1. Tile facsimile for all text documents

The metadata "facsimile" tile preview was gated to `text/markdown`, so a
plain-text (`text/plain`) email — e.g. a plain-text-only forward with no HTML
part — still showed the bare "Text" placeholder. Widened the gate to any
`text/*` document with metadata (`DocumentListView.vue`). A text doc with no
metadata still falls through to "Text".

## 2. Recipient auto-fill from the email `To:` header

**Why:** email documents often left `recipient` null because it was set *only*
by the extraction LLM reading the body; the envelope `To:` was never even
captured.

**What:** ingestion now captures the `To:` address(es) onto
`document.extra["email_to"]` (`email_ingest._to_addresses` / `_event_detail`,
threaded through a new `extra_document` param on `ingest_file`). During
extraction, a new fallback (`extraction/apply.resolve_recipient_from_email` +
`match_user_by_email`) resolves those addresses against users'
`email_forward_addresses` (case-insensitive) and, on a match, sets the
recipient to that user's linked recipient.

**Decision — fill-only:** the fallback runs *only* when the LLM returned no
recipient (`document.recipient_id is None` and not user-locked). The LLM's
recipient always wins when present; a user's manual edit always wins. No new
config — reuses `email_forward_addresses`. The resolvers live in
`extraction/apply.py` to avoid an import cycle (`email_ingest → ingest → jobs →
apply`).

## 3. `/ask` can update document metadata (propose-then-confirm)

**Why:** you wanted to correct metadata conversationally ("the recipient is my
wife") and have the agent apply it.

**What:** `/ask` was already an agentic tool-use loop with three read-only
tools; added a fourth, `update_document_metadata`. The mutation logic was
extracted from the `PATCH /api/documents/{id}` route into a shared service
`documents_service.apply_document_update(...)`, so the route and the Ask tool
share identical behavior (upserts, `user_edited_fields`, corrections, audit
event). Ask edits are stamped `edited_by="ask"`.

**Two guardrails, both enforced in code:**
- **Scope** (`editable_ids`): the agent may only edit documents surfaced by a
  read tool in this conversation (ids collected from the server-side thread
  history + this turn's retrievals — not spoofable from the request body).
- **Propose-then-confirm** (`previewed_ids`): a `confirmed=false` call returns a
  current-vs-proposed preview and writes nothing; a `confirmed=true` write is
  refused unless that document was previewed in an **earlier** turn. `previewed_ids`
  is seeded *only* from thread history — never from a preview made in the current
  turn — so the user genuinely sees the proposal and replies before any write.

**Design decisions:** edits scoped to any *cited* document (not just the one the
Ask was opened from); propose-then-confirm rather than apply-immediately.

## 4. Review finding fixed during development

The first cut of the confirm-gate added the document to `previewed_ids` on the
preview call, which an adversarial/eager model could exploit by emitting a
preview **and** a confirm for the same document in a single turn — bypassing the
"user sees it first" property. Fixed by seeding `previewed_ids` from history
only; a same-turn preview no longer authorizes a same-turn confirm. Covered by
`test_engine_same_turn_preview_then_confirm_is_refused` (bypass refused) and
`test_engine_confirm_after_prior_turn_preview_writes` (the real cross-turn path).

A second finding — `apply_document_update` raising `fastapi.HTTPException` from
the shared service — was left as an accepted tradeoff (both current callers
handle it; changing it risks the route's 422 contract). Noted for future
callers.

## 5. Verification

- Full backend suite **794 passed** (coverage 87%); frontend **608 passed**.
- New tests: recipient resolvers + fill-only fallback (`test_extraction_apply.py`,
  `test_email_ingest.py`); the write service, preview/commit, scope guardrail,
  and both preview-gate cases (`test_ask_document_write.py`); the text/plain tile
  facsimile (`DocumentListView.spec.ts`).
- `ruff check`/`format` clean repo-wide; ESLint + vue-tsc clean.
- Independent code review (adversarial pass on the write path) — its one High
  finding (same-turn gate bypass) fixed; features 1 & 2 clean.
- Docs updated: `frontend.md` (tile), `ingestion.md` (recipient auto-fill),
  `ask.md` §1.8 (write tool), `api.md` (shared service + tool list).
