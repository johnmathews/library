# Junk-image ingestion defenses

**Date:** 2026-07-15 · **Trigger:** prod document 159 — a 6 KB company-logo PNG (`image001.png`) from a forwarded email, ingested and flagged for review.

## 1. The incident

Loki showed the full chain: the deterministic noise gate passed the logo (non-inline attachment, longest edge > 64 px, and the 4096-byte `tiny_image` floor applies only to *undecodable* images), the optional LLM label pass **correctly** verdicted it `probably_noise` ("Small PNG image (6KB), likely an embedded signature logo or banner") — but that disposition was ingest-and-flag, never drop. The pipeline then spent ~$0.005 running OCR (3 chars), Haiku extraction (self-reported confidence *high* on a logo), markdown, embedding, and thumbnail jobs. Two sibling junk docs (157: 6 OCR chars, 156: 10 chars) landed the day before via the same gap.

## 2. What shipped (engineering-team run manual-20260715T093250Z, W1–W7)

1. **`decoration_image` noise rule** (`_noise_reason`): for non-inline decodable images, three independent signals — filename (logo/signature/banner/footer/icon stem words, or Outlook `image\d{2,3}` auto-embed names), size (≤ `LIBRARY_EMAIL_FILTER_DECORATION_MAX_BYTES`, 65536), shape (longest edge ≤ `LIBRARY_EMAIL_FILTER_DECORATION_MAX_EDGE_PX`, 384, or a ≥4:1 banner with short edge ≤ 128 px). **Skips only when ≥ 2 signals agree** — byte size alone or a filename alone never drops, preserving the bias-to-ingest invariant. The renegotiated contract test now uses an 800×600 image so "byte size alone never drops a decodable image" still holds.
2. **`llm_noise_corroborated` skip**: a `probably_noise` LLM verdict on an image now skips (quietly, audited) when ≥ 1 decoration signal corroborates it. Zero signals, non-images, or gate off keep the old ingest-and-flag (`email_item_ambiguous`). Two independent judges must agree before anything is dropped; no new model calls.
3. **Override bypass**: the held-email "ingest anyway" path passes `override=True` so the decoration heuristic yields to explicit human intent; hard gates (inline/cid signature, tiny image, non-document parts, oversize, unsupported MIME) still apply, and the override makes no label call.
4. **Durable skip audit** (`email_selection_traces`, migration 0027): a row is written whenever a processed email's selection contains ≥ 1 skip — including zero-document emails, which previously left only a log line. Reason-based trigger (body bookkeeping decisions never write rows); held emails write no row (their trace lives on `held_emails.trace`). Surfaced read-only via `GET /api/settings/email-triage/recent-skips` (last 20, compact) and a "Recently skipped items" card on the Settings email-triage tab.
5. **`decoration_image` validation rule** (post-OCR backstop, all channels): fires on `image/*` docs with < 20 non-whitespace OCR chars **and** nothing grounded (no amount, date, or sender) — the grounded-fields guard came out of the wrap-up code review, which caught that thin OCR alone would have branded every vision-rescued receipt photo a decoration.
6. **`library sweep-junk` CLI**: dry-run by default (image/* docs with < 100 OCR chars or received-size < 20 000 bytes, size read from the `received` event); `--apply --ids` soft-deletes exactly the named candidates with all-or-nothing refusal of non-candidates, mirroring the API delete (restorable until the retention purge).

## 3. Key decisions

1. **Extend `_noise_reason`, don't filter in `ingest_file`** — email-channel-only, so deliberate manual uploads of small images keep working; both the poll path and the override path share the one insertion point.
2. **Corroboration over new model calls** — the labeler already ran and already got doc 159 right; what was missing was permission to act on the verdict, granted only when deterministic signals agree.
3. **Cleanup is soft-delete only** — the 30-day purge window is the safety net; the sweep refuses IDs outside its own candidate query at apply time (no trust in a stale dry-run list).

## 4. Verification

Full backend suite 1214 → **1241 passed** (coverage steady at 89%, HTML in `htmlcov/`); frontend units 962 passed, `vue-tsc` and eslint clean; ruff check/format clean repo-wide including `migrations/`. Wrap-up ran an adversarial docs audit (5 stale spots fixed, e.g. two "never drops" claims and the missing `email_selection_traces` entry in architecture.md) and an independent code review (1 confirmed finding, fixed — see §2.5).

## 5. Follow-ups

1. Prod: deploy, then run `library sweep-junk` dry-run and confirm the deletion list (expected: 159, 157, 156; borderline 135, 143).
2. Watch the recent-skips card for false positives in the first weeks — thresholds are settings, tunable without a deploy.
