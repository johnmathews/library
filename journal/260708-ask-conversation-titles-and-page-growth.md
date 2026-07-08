# Ask conversation titles and page growth

Two Ask-feature improvements: give conversations genuinely useful names in the
history sidebar (they were all "tell me about the Document…"), and let the Ask
page grow vertically and scroll instead of being trapped at one viewport height.

Run via the engineering-team skill; artifacts in
`.engineering-team/runs/manual-20260708T125806Z/`.

## 1. Problem

1. Every thread's title was `question.strip()[:120]` — the first 120 chars of
   the opening question (`src/library/api/ask.py:_thread_title`). Since most
   sessions start "tell me about the Document …", the sidebar history was
   useless for telling conversations apart.
2. The Ask page was locked to `lg:h-[calc(100dvh-8rem)]` with an
   internally-scrolling transcript, so on desktop it stayed squished in one
   screen instead of growing with the conversation.

## 2. What changed

### 2.1 LLM-generated titles (backend)

- New `generate_thread_title` in `ask.engine`: one cheap
  `ask_title_model` (default `claude-haiku-4-5`) call summarising the first
  question/answer into a 3–6 word title, with `_clean_title` trimming quotes /
  trailing period / length (≤60 chars).
- `POST /api/ask` calls it for a **new** thread only, after `run_ask`, inside a
  `try/except`. It is **non-fatal**: the answer is already produced, so any
  failure keeps the placeholder title (the truncated question) and never fails
  the request — modelled on the frontend's fire-and-forget `syncThread`. The
  title call's cost is folded into the turn's `cost_usd`.
- `ask_title_model` added to `_PRICED_MODEL_FIELDS`; Haiku is already in the
  pricing map, so no `MODEL_PRICING_USD_PER_MTOK` change was needed.

### 2.2 Rename (backend + frontend)

- `PATCH /api/ask/threads/{id}` (`{"title": 1–120 chars}`), ownership-checked via
  `_owned_thread`, blank-after-trim rejected 422. It builds the returned
  `ThreadSummary` from a **fresh query**, not the just-committed ORM object —
  reading expired attributes post-commit would raise `MissingGreenlet`.
- Sidebar gets an inline **Rename** affordance mirroring the two-step delete:
  the row's title becomes an input seeded with the current title; Enter/Save
  commits via `renameThread`, Esc/Cancel aborts, blank/unchanged is a no-op.

### 2.3 Page grows and scrolls (frontend)

- `AskView.vue`: dropped `lg:h-[calc(100dvh-8rem)]` and the transcript's
  `lg:overflow-y-auto lg:flex-1`. The chat panel now flows at natural height and
  the whole page scrolls. Empty/short conversations keep a `min-h-[18rem]` floor
  (moved onto the empty-state block, which no longer relies on a definite parent
  height). The composer stays a normal `shrink-0` sibling that scrolls with the
  content (per the user's choice over a viewport-pinned composer).
- `scrollToBottom` now brings the newest turn into view via the page scroll
  (`scrollIntoView`), since the transcript no longer scrolls internally.

### 2.4 Backfill

- `scripts/backfill_ask_titles.py` re-titles existing threads still on the
  placeholder title (detected by `is_placeholder_title` == `_thread_title(first
  question)`), generating from each thread's first Q&A. Idempotent, `--dry-run`,
  `--limit N`; skips renamed threads and threads with no turns.

## 3. Decisions

All four scoping questions were confirmed with the user: LLM titles via a cheap
model; **backfill existing + name new**; **add inline rename**; **composer
scrolls with the page** (not pinned).

## 4. Verification

- Backend: full suite **1045 passed**; new tests cover title generation
  (unit + endpoint + failure-keeps-placeholder), the rename endpoint
  (happy/404/422), and the backfill placeholder detection.
- Frontend: **897 passed**, `type-check` clean, `eslint` clean; new tests cover
  `renameThread` (PATCH wiring), the sidebar rename flow, and the
  grows-with-page layout.
- Repo-wide `ruff check` + `ruff format --check` clean.

Not yet done live: a browser walkthrough of the layout with a running stack +
Anthropic key, and running the backfill script against the live DB.

## 5. Gotchas for next time

1. Building a response model from a **just-committed** ORM object triggers a
   lazy reload of expired attributes → `MissingGreenlet`. Re-query the columns
   instead (as `list_threads` does). See [[detail-refresh-expiry]].
2. The `test_api_ask.py` fake Anthropic pops a fixed response list, so any new
   `messages.create` call (title generation) throws off existing tests. Handled
   with an **autouse fixture** stubbing `generate_thread_title` to a no-op;
   titling tests override it. See [[library-model-pricing-map]] for why the new
   `*_model` knob needs a pricing row + `_PRICED_MODEL_FIELDS` entry.
