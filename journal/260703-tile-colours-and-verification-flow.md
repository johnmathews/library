# Tile-border colours fix + document verification flow

Engineering-team cycle (run `manual-20260703T114943Z`). Two threads: fixing the
per-kind tile borders that shipped invisibly, and reworking the "needs
verification" experience (why-it's-flagged, clear-on-save, batch queue).

## 1. The tile-border bug — a CSS `@layer` defeat, not a logic bug

The per-kind border-colour feature (shipped the same day) was correct in every
respect the unit tests checked — the `.app-doc-card--accented` class and the
`--card-accent` custom property were both applied — yet no coloured border ever
painted.

Root cause: this is Tailwind v4. `main.css` does
`@import './utility-patterns.css' layer(components)`, so the accent rule
`.app-doc-card.app-doc-card--accented { border-color: var(--card-accent) }` lives
in the **`components`** layer. The tile's markup carried a Tailwind
`border-gray-200` utility, which lives in the **`utilities`** layer. Under CSS
cascade-layers semantics, a later-declared layer wins *regardless of
specificity* — and `@import 'tailwindcss'` declares the order
`theme, base, components, utilities`. So `border-gray-200` (utilities) always
beat the accent (components). The old code comment reasoned about
selector specificity, which is irrelevant across layers — that wrong mental model
is what let the bug ship. (The pre-existing violet hover border was silently
defeated the same way.)

**Fix:** make `.app-doc-card` **own its border** (`border: 1px solid …` in the
component rule) and drop the `border border-gray-200 …` utilities from the tile
markup. Now the neutral border, hover, and accent all resolve inside the one
`components` layer, where specificity works again.

**Why the test missed it, and the guard added:** jsdom has no layered-cascade
resolution, so the unit test could only assert the class hook. Added
`e2e/tile-border-colour.spec.ts`, which sets a per-kind override and asserts the
tile's **computed** `border-top-color` equals it in a real browser — the "ensure
it's implemented next time" guard.

## 2. Verification flow

Three linked changes, driven by user pain (doc 107: fixed an implausible date,
saved, warning stayed).

### 2.1 Clear-on-save (the doc-107 bug)

`PATCH /api/documents/{id}` never recomputed validation — `review_status` and
`extra["validation"]` were only ever written by the extraction pipeline, the
`revalidate` CLI, or explicit verify. So a corrected field left its stale finding
in place. Fix: `update_document` now calls a new
`documents_service.revalidate_after_edit` before committing, which re-runs the
deterministic rules and rewrites the findings + status in the same transaction.

Key subtlety: the extraction path sets status via `derive_review_status`, which
only ever returns `needs_review`/`unreviewed` — reusing it verbatim would demote
a **user-verified** document to unreviewed on any edit. So the edit path has its
own status policy: findings → `needs_review`; no findings + already `verified` →
**keep verified**; else `unreviewed`. Shared the pure validate-and-persist core
as `extraction/apply.revalidate_document` (returns findings, does not touch
status); `_apply_validation` now delegates to it.

The **Ask agent's write tool** (`ask/engine._run_update_document`) shares
`apply_document_update`, so it had the same stickiness bug via a different entry
point — a code-review catch during wrap-up. It now calls `revalidate_after_edit`
before its commit too, so agent-applied fixes clear (and bad agent edits flag)
identically to the PATCH route.

### 2.2 "Why this needs review" + dashboard row reasons

The detail page already had a top banner, but it deliberately **excluded**
field-mapped findings — an implausible date showed only as a tiny per-field ⚠
tooltip, never at the top. Reworked it into a prominent "Why this needs review"
panel listing **every** finding in plain language (gated on `needs_review`).
Added `utils/validationReason.ts` as the single source of human wording (rule
code → short title + detail), reused by the detail panel, the dashboard rows, and
the queue. Exposed a compact `review_findings` (`{rule, field, message}`) on the
list API — populated only for `needs_review` rows so clean rows stay lean — and
show a short reason next to each dashboard "Needs review" badge.

### 2.3 Step-through review queue

New `stores/reviewQueue.ts` (ordered `needs_review` ids + cursor). The dashboard
"Review these one by one →" button loads the set and opens the first doc with
`?queue=1`; `DocumentDetailView` in queue mode shows a position bar +
Prev / Verify & next / Next / Exit. Because the detail page **autosaves per
field** (now revalidated server-side), "Save & next" collapses to "Next": a fixed
doc drops off `needs_review`, and Next removes resolved docs from the queue while
keeping unfixed ones for a later pass. No new route or editor — queue mode reuses
the whole existing detail page.

## 3. Decisions

1. **Humanise finding text in the frontend**, not the backend `message` strings —
   one place, and no churn to backend validation tests.
2. **Reuse the detail page for the queue** via a query flag + a small store,
   rather than building a second editor (the detail page is 2000+ lines).
3. **Smart recompute on save** (fixed findings clear, unfixable ones remain) over
   "any save clears the flag" — keeps genuine warnings.

## 4. Tests / checks

Backend 871 passed (ruff clean whole-repo, coverage 85%). Frontend 689 unit
passed (+20: `validationReason`, `reviewQueue`, detail-view queue mode, list-row
reason, detail why-panel), type-check + eslint clean, production build OK. Two new
e2e specs (`tile-border-colour`, `review-queue`) run in CI; the review-queue spec
also exercises §2.1 by manufacturing a `needs_review` doc via a future-date edit —
something the e2e stack previously couldn't do without Claude extraction.

## 5. Follow-ups (not done)

- `reviewQueue.start()` caps at 200 `needs_review` docs (silently). Fine for now;
  paginate the queue if anyone accumulates more.
