# Frontend views UX pass — batch 2 (W8–W11) + reconcile

Date: 2026-06-28. Branch: `feat/views-ux-pass-2` (stacked on `feat/land-views-w1-w7`).

## 1. Context & the reconcile detour

Batch 2 of the views UX pass was meant to build on a `main` that already had
batch 1 (W1–W7). It didn't: W1–W7 had been merged into the
`feat/admin-role-and-views` integration branch (PR #7), never onto `main`, and
`main`'s admin commits were a *rebased* divergence from those branches. So the
shared premise ("W1–W7 are on main") was false — `PageHeader.vue` and the W7 bar
charts were absent from `main`.

Decision (with the user): **reconcile first**. Cherry-picked the seven W1–W7
commits onto a branch off `origin/main` (`feat/land-views-w1-w7`) — they applied
cleanly — plus restored `docs/frontend-view-principles.md` (the W1 deliverable,
which `f8765c6` had dropped from main as a stray-file sweep before the views work
it documents had landed). That branch became **PR #9** and the base for batch 2.
Batch 2's PR (#10) targets it and retargets to `main` once #9 merges.

## 2. What shipped (W8–W11)

### 2.1 W8 — manual series-membership overrides + FX

Series are computed on the fly as `(sender_id, kind_id, currency)` groups with no
membership table. W8 adds durable overrides to that computation:

- **Migration 0015** (latest head was 0014): `series_membership_overrides`
  (`pin`|`exclude`, `NULLS NOT DISTINCT` unique on series+document) and `fx_rates`
  (USD-base reference snapshot, seeded with researched yearly rates 2015–2026 for
  EUR/GBP/CHF/JPY/CAD/AUD/SEK/NOK/DKK).
- **`library.fx`** — date-aware historical conversion: the rate is the row with
  the greatest `as_of` on-or-before the document's date, falling back to the
  earliest; `None` when a currency has no rate.
- **`summarize_series`** applies excludes (drop ids) and pins (force-load by id,
  FX-converting cross-currency amounts into the series bucket; amount-less or
  unconvertible pins drop from stats and are logged).
- **Endpoints** `POST`/`DELETE /api/series/{sender_id}/{kind_id}/members` —
  idempotent toggles between `pinned`/`excluded`/`cleared`.
- **Frontend** — `SeriesChartTile` gains an `editable` mode: per-document remove
  + "+ Add document" search-to-pick; wired into `/charts` and the document-detail
  series card, refetching on change.

Settled with the user before coding: pin semantics = **FX-convert cross-currency
pins**, and FX = **seeded `fx_rates`, date-aware historical**. Backward-compatible
— zero overrides ⇒ byte-identical output.

### 2.2 W9 — overrides feed the LLM series-description prompt

`refresh_series_insight` appends up to `MAX_OVERRIDE_EXAMPLES` pins/excludes per
direction to the prompt as an authoritative "curated membership" block; the system
prompt is told to weight them. Bounded for cost (Haiku tier). Tests are
prompt-construction only — no live LLM call.

### 2.3 W10 — Ask redesign (SRE-Agent layout)

`AskView` reworked: removed the `max-w-6xl` cap (the shell owns width); persistent
`ConversationSidebar` with a thread **search** filter; shared `PageHeader`; wide
rich-markdown answers (GFM tables styled), not chat bubbles; sticky bottom
multi-line composer with a **Send** button. Backend `/ask` text path unchanged;
`#ask-question`/`ask-submit` ids preserved so the e2e contract holds.

### 2.4 W11 — Ask image attachments (multimodal)

`AskRequest` gains `images: [{media_type, data}]` (max 5; png/jpeg/gif/webp;
per-image base64 ceiling). `run_ask` builds image content blocks on the question
turn with a vision-aware system-prompt note; images persist in
`ask_turns.messages` and replay as history. Composer gets an **Attach image**
control (base64 client-side, thumbnail previews + remove).

## 3. Wrap-up fixes (found during /done review/audit)

1. **History-corruption bug in `run_ask`** (code review, conf 85). When the
   tool-turn budget was exhausted without a final answer, `turn_messages` ended on
   a `tool_result` (role "user"); the next question in the thread produced
   back-to-back user turns → Anthropic 400, permanently breaking the thread.
   Pre-existing latent bug in code W11 touched. Fix: close the turn with the
   fallback answer as an assistant message (keeps the tool_use/tool_result pair
   intact and ends on assistant). Added a regression test.
2. **Defensive `max_length` on `AskImage.data`** (~15 MB decoded) — bounds memory
   before bytes reach the model, on top of any proxy body limit.
3. **Stale doc cross-references** (doc audit). Inserting `api.md §1.15` shifted
   Notes →§1.17 and Admin →§1.18; six inbound "§1.16/§1.17" pointers across
   `frontend.md`, `architecture.md`, `roadmap.md`, `ingestion.md` were corrected.
4. **`architecture.md` data-model** now lists the two new tables
   (`series_membership_overrides`, `fx_rates`).

## 4. Verification

- Backend `uv run pytest`: **667 passed**; coverage **89%** (gate 85%), HTML in
  `htmlcov/`. ruff clean.
- Frontend: `vue-tsc` + `eslint` clean, `vitest` **433 passed**.
- Docs updated: `api.md` (§1.11 ask images, §1.15 series membership, endpoint
  table), `ask.md` (§1.2 attachments, §1.7 override hints), `frontend.md`,
  `architecture.md`. New journal entry (this file).

## 5. Open / deferred

- One sub-80 review note left as-is: after overrides, `_insufficient(bucket)` can
  report a pinned doc's identity if a series is excluded below the minimum (UI
  hides `insufficient`, so cosmetic).
- Batch 2 PR (#10) is **draft → ready** for the user to review; merge is the
  user's (they also merge reconcile PR #9, then #10 retargets `main`).
