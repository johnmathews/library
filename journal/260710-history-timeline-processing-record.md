# History timeline as the canonical processing record

**Date:** 2026-07-10

## 1. What & why

On `/documents/:id` the **History** tile (`DocumentHistoryTimeline.vue`) is meant to be the
first and ideally only place someone looks to understand how a document was processed. It was
under-reporting: `extraction_completed` rendered as a single bland line ("Description &
metadata added") with no model/confidence/escalation detail; `extraction_skipped` had no label
and was hidden as noise; and failures/skips never showed their reason or error. Crucially, the
new **vision fallback** (yesterday's thin-OCR image-PDF fix — a low-confidence retry that
re-reads the *original file* instead of the OCR text) was invisible.

This session made each processing step tell a small, readable breakdown of what happened.
**Frontend-only** — the backend already emits everything in each `IngestionEvent.detail`; the
API returns the full dict verbatim (`schemas.py IngestionEventOut`). Confirmed against
`extraction/apply.py` and `extraction/extractor.py` before touching UI.

## 2. What shipped

All in `frontend/src/components/DocumentHistoryTimeline.vue` + its vitest spec:

1. **`extraction_completed` breakdown** — a **method sentence** narrating how the input was
   sent, plus a wrapped row of small labelled **chips** (model, confidence, cost). The method
   sentence distinguishes the four `input_mode`/`escalated` cases and gives the **vision
   fallback** a violet-accented, unmissable line (`[data-testid="history-extraction-method"]`;
   chips `[data-testid="history-extraction-chip"]`).
2. **`extraction_skipped`** graduated from noise → milestone, labelled "Extraction skipped"
   with its reason (budget skips show spent-of-budget; input/file skips show the detail string).
3. **Failures** (`extraction_failed` / `ocr_failed` / `markdown_failed` / `embedding_failed`)
   surface their carried `error`/`detail` message (`[data-testid="history-secondary"]`).

## 3. Key semantics

`input_mode` = what was sent on the **final** attempt; `escalated` = whether the low-confidence
retry ran. The four cases (verified in `extractor.py:345-396`):

| escalated | input_mode | method sentence |
|---|---|---|
| false | text | "Read the OCR text" |
| false | document/image | "Read the original file directly (OCR text was unusable)" |
| **true** | **document/image** | **"Low confidence — re-read the original file (vision fallback)"** ← violet |
| true | text | "Low confidence — retried with a stronger model" |

## 4. Decisions

1. **Chips + method sentence**, not one terse line — fits the mosaic UI language (uppercase-xs
   labels, violet accent, small pills) and keeps each timeline item compact.
2. **Cost in, raw tokens out.** Cost is a single at-a-glance number that's part of "how it was
   processed", so it's a chip; `input_tokens`/`output_tokens` are operational noise and stay in
   the existing "Show all events" JSON dump only.
3. **Only `extraction_skipped` surfaces.** Extraction is the headline step, so its skip is worth
   a milestone. `embedding_skipped` and other low-signal skips stay hidden by default (still in
   "Show all"), preserving the existing contract — a regression test locks this in.
4. All reads off the untyped `detail` dict are `typeof`/`Array.isArray`-guarded, so a
   missing/wrong-typed field degrades to a safe default (e.g. the "normal OCR-text read" case)
   rather than throwing or rendering `undefined`.

## 5. Verification

- `npm run test:unit` — 920 passing (11 new History cases: 4 input-mode narratives, chip
  model/confidence/cost + token-exclusion, cost-omission, budget/detail skips, extraction &
  stage failures, embedding-skipped-stays-hidden regression).
- `npm run lint` (eslint) + `npm run type-check` (vue-tsc) — clean.
- Adversarial code-review subagent cross-checked the four-case logic and untyped-detail guards
  against `docs/ingestion.md` — no issues.
- e2e: display-only change, **no** e2e spec references the History tile and no fixtures added;
  CI runs the full Playwright suite against the real stack as the promote gate.

## 6. Gotcha (for next time)

Early edits to the spec and `docs/frontend.md` accidentally landed in the **main checkout**
instead of the worktree — the initial `Read` happened before `EnterWorktree`, so absolute paths
still pointed at the main repo. Caught it when vitest ran only the 5 original tests. Fixed by
copying the two files into the worktree and `git checkout`-ing main clean. Lesson: after
`EnterWorktree`, use the worktree-prefixed absolute paths for every edit.
