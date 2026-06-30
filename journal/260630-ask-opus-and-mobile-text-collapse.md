# Ask → Opus 4.8, and mobile document-text collapse

Date: 2026-06-30

## 1. Ask answer model → Opus 4.8

Switched the `/ask` answer model from `claude-sonnet-4-6` to `claude-opus-4-8`
for higher answer accuracy (user request).

- `src/library/config.py`: `ask_model` default → `claude-opus-4-8`.
- `src/library/extraction/extractor.py`: added the Opus 4.8 pricing row to
  `MODEL_PRICING_USD_PER_MTOK` — `(5.0, 25.0)` $/MTok (input/output). Without it
  `estimate_cost_usd` would log "no pricing" and record cost 0, so the per-turn
  cost line in the UI would read $0.
- Docs/env: `.env.example` (`LIBRARY_ASK_MODEL`) and `docs/ask.md` (three
  references) updated to the new default.

**Migration check (Opus 4.8 rejects `temperature`/`top_p`/`top_k`/`budget_tokens`
with a 400):** the ask engine's `client.messages.create` call passes only
`model`, `max_tokens` (1024), `system`, `tools`, `messages` — none of the
removed params, and no `thinking` field. So this is a clean model-ID swap with
no code-behavior change and no 400 risk. `max_tokens=1024` needs no streaming.

Cost impact: Opus 4.8 is ~1.67× Sonnet's input price and ~1.67× output
($5/$25 vs $3/$15 per MTok) — per-question cost rises accordingly; user accepted.

## 2. Mobile: collapse document text by default

On `/documents/:id`, the document-text reader lives in the preview column
(`lg:order-2`, **first on mobile**) while the metadata column (summary, amount)
is `lg:order-1` (**below it on mobile**). A long document forced the user to
scroll past the entire text to reach the metadata.

`DocumentDetailView.vue`:
- Added `textExpanded` ref, initialised at setup from
  `matchMedia('(min-width: 1024px)')` — expanded on desktop, **collapsed on
  small screens** (no expand→collapse flash; jsdom has no matchMedia →
  defaults expanded under test).
- The "Document text" card header now has a mobile-only **Show/Hide** toggle
  (`lg:hidden`, `data-testid="markdown-toggle"`, `aria-expanded`/`aria-controls`).
- The body (`#document-markdown-body`) is gated by `v-show="textExpanded"` with
  `lg:!block` so it is always visible at lg+ (where text and metadata are side
  by side) — the `!important` block class overrides v-show's inline
  `display:none`. v-show keeps the body in the DOM so anchors/deep-links resolve.

## 3. Tests

- New unit test: toggle collapses/expands the body (asserts inline
  `display: none` + Show/Hide label; content stays in the DOM).
- e2e `markdown-reader.spec.ts` and `notes.spec.ts` assert `markdown-content`
  visibility and run on the mobile/tablet projects (below lg), where the text is
  now collapsed by default — added an expand-if-visible step (mirrors the
  existing hamburger-if-visible pattern) before those assertions.

## 4. Verification

- Backend: **743 passed**; `ruff check` + `ruff format --check` clean.
- Frontend: **508 passed** (+1 new); eslint, vue-tsc, and vite build clean.
- e2e self-skips without `E2E_BASE_URL` (live stack); the expand-on-mobile steps
  are the only changes to that path.
