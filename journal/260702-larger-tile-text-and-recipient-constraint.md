# Larger tile facsimile text & inference-constrained recipients

Two feedback-driven refinements to the text-tile facsimile / recipient work.

## 1. Larger, more readable facsimile text

The metadata facsimile on text tiles was hard to read (`text-[11px]` rows,
`text-xs` title). Bumped the title to `text-base` and the metadata rows to
`text-sm`, with slightly darker text, in `DocumentListView.vue`. Confirmed
visually against a rendered mock; still fits the tile's preview box.

## 2. Inference no longer invents recipients

**Problem:** the extraction LLM could create junk recipients — e.g. a Saxo Bank
letter yielded recipient "Mathews" (a family surname, not a person), because the
recipient path called `upsert_recipient`, which creates a row when no match
exists.

**Fix:** added `match_existing_recipient` (`extraction/apply.py`) which resolves
a name to an **existing** recipient — a known user (username/display-name →
their linked recipient) or an existing recipient by case-insensitive name — and
returns `None` when nothing matches, **never creating** a row. The extraction
path now uses it and simply drops an unmatched name (recipient left unset). The
existing email-`To:` fallback then fills the recipient for emails.

`upsert_recipient` was refactored to `match_existing_recipient(...) or create`,
so its behavior is unchanged for the **manual** edit path (`PATCH
/api/documents/{id}` via `apply_document_update`) — a user can still create a new
recipient by hand. Only *inference* is constrained.

**Net effect** (per user guidance): for emails the recipient comes from the LLM
only when it names a real known recipient; otherwise from the email `To:`
address. "Mathews"-style invented names are dropped, and the To: fallback (which
resolves to John/Ritsya) fills in.

Also verified (and locked with a test) that email **attachment** documents carry
`extra["email_to"]` too — both attachment and body candidates share
`_ingest_candidate`, so the To: fallback works for attachment-sourced docs, not
just email bodies.

## 3. Follow-up (separate, pending confirmation)

Existing documents already assigned a non-person recipient (e.g. the "Mathews"
row) still need a one-time cleanup: delete the orphaned recipient and blank the
documents pointing at it. That is prod data surgery, so it will be done as a
separate, confirmed step after probing exactly which recipients/documents are
affected — not baked into this change.

## 4. Verification

- Full backend suite **803 passed** (coverage 87%); frontend **608 passed**.
- New tests: `match_existing_recipient` (user match / existing match / unknown →
  None with no row created); apply-path (matched name assigned; unmatched name
  dropped + no row + To: fallback fills; LLM-wins-when-real preserved); manual
  PATCH still creates a brand-new recipient; email attachment carries
  `extra["email_to"]`.
- `ruff check`/`format` clean repo-wide; ESLint + vue-tsc clean.
- Docs updated (`ingestion.md` recipient behavior, `frontend.md` tile sizing).
