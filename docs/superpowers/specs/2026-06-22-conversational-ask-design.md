# Conversational Ask — design

**Status:** approved (2026-06-22). Sub-project 4 of the extraction/Ask roadmap.

## Goal

Make Ask multi-turn. A follow-up like *"what about last year?"* should resolve
against the prior turns of the same conversation instead of being answered cold.
Today `POST /api/ask` is single-shot: `run_ask` seeds a Claude tool-use loop with
one user message and returns a cited answer; an `ask_logs` row records the cost.
This sub-project threads persisted conversation history into that loop and turns
the single-shot `AskView` into a chat with a conversation-history sidebar.

## Decisions (locked during brainstorming)

1. **State lives server-side**, in two new tables (`ask_threads`, `ask_turns`).
   Conversations are durable and resumable by id, not just client-held.
2. **Follow-ups re-feed prior Q&A *and* prior tool results** into the loop, so
   the model can reason over earlier evidence without re-querying. The faithful
   path; the cost is bounded (decision 3).
3. **Bounding is a sliding turn window + prompt caching.** Re-feed the last
   `LIBRARY_ASK_HISTORY_TURNS` turns verbatim (default 3); drop older turns
   entirely. Cache the static system+tools and the rehydrated history prefix so
   re-sends hit the Anthropic prompt cache. Once a thread exceeds the window the
   history-prefix cache will miss on dropped turns — accepted; system+tools stay
   cached and most threads are short.
4. **v1 UX is chat + a conversation-history list** (sidebar to resume past
   threads), not just a single active transcript.
5. **`ask_logs` is dropped; `ask_turns` subsumes it.** `ask_logs` is write-only
   (no readers anywhere in the codebase), so the per-turn record `ask_turns`
   becomes the single source of cost/provenance. Existing `ask_logs` rows are
   discarded — acceptable on a young, write-only table.

## 1. Data model

A migration drops `ask_logs` and creates two tables.

### `ask_threads` — one conversation

| Column | Type | Notes |
|--------|------|-------|
| `id` | BigInteger PK | |
| `user_id` | FK `users.id` ON DELETE SET NULL, nullable | owner; history list is scoped to the current user |
| `title` | Text | derived from the first question, truncated to ~120 chars |
| `created_at` | timestamptz, `server_default=now()` | |
| `updated_at` | timestamptz, `server_default=now()`, bumped each turn | drives history-list ordering |

### `ask_turns` — one Q&A turn (subsumes `ask_logs`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | BigInteger PK | |
| `thread_id` | FK `ask_threads.id` ON DELETE **CASCADE**, indexed | |
| `query` | Text | the user's question this turn |
| `answer` | Text | the final prose answer |
| `model` | String(64) | answer model |
| `input_tokens` | Integer, default 0 | summed across the turn's loop |
| `output_tokens` | Integer, default 0 | |
| `cost_usd` | Float, default 0.0 | this turn's estimated cost |
| `used_tools` | JSONB, `'{}'` | `{"tools": [...]}`, as `ask_logs` stored it |
| `citations` | JSONB, `'[]'` | list of `{document_id, title, page_number}` |
| `messages` | JSONB, `'[]'` | the full serialized Anthropic message sequence this turn contributed — the user question plus every assistant `tool_use` / user `tool_result` / final-answer block — for replay (see §2) |
| `created_at` | timestamptz, `server_default=now()` | orders turns within a thread |

A thread's total cost is `sum(turn.cost_usd)`; its turn order is by
`created_at` (monotonic per thread).

**Why store `messages` as a blob.** Replaying prior tool results faithfully
means handing the Anthropic API the exact `tool_use`/`tool_result` block
sequence again. Rather than reconstruct it from `citations`/`used_tools`, we
persist the serialized blocks the turn produced (`block.model_dump()` for
assistant content; the `tool_result` dicts we already build). `query`/`answer`
are denormalized convenience columns for display and the history list; the
question therefore also appears as `messages[0]` — a small, deliberate
duplication that keeps replay robust.

## 2. Engine (`src/library/ask/engine.py`)

`run_ask` gains a parameter and returns more:

```python
async def run_ask(
    session: AsyncSession,
    *,
    question: str,
    settings: Settings,
    client: AsyncAnthropic,
    history_messages: list[dict[str, Any]] | None = None,
) -> AskResult: ...
```

- It seeds `messages = list(history_messages or []) + [{"role": "user", "content": question}]`
  and runs the **existing** bounded loop unchanged (`ask_max_tool_turns`,
  inline-citation parsing, tool dispatch).
- `AskResult` gains `turn_messages: list[dict[str, Any]]` — the message blocks
  this turn appended to the conversation (the leading user question, every
  `{"role": "assistant", "content": [...]}` with serialized blocks, and every
  `{"role": "user", "content": [tool_result...]}`). The API persists this into
  `ask_turns.messages`.
- Assistant content is serialized with `block.model_dump()` before storage and
  before re-send (the loop already appends raw SDK objects to its working
  `messages`; persistence/replay use plain dicts, which are valid API input).
- **Prompt caching.** When `history_messages` is non-empty, set
  `cache_control: {"type": "ephemeral"}` on the last content block of the
  rehydrated history prefix, and on the static system/tools, so re-sent history
  hits the cache. Caching is best-effort; correctness never depends on a hit.

Citations and `used_tools` are computed exactly as today. `citations` is also
serialized onto the turn for display without a re-query.

## 3. API (`src/library/api/ask.py`)

All routes require the current user (enforced at include level, as today) and
return 503 when no Anthropic key is configured (unchanged for the answer path).

- **`POST /api/ask`** — request body gains optional `thread_id: int | None`;
  response gains `thread_id: int`.
  - No `thread_id` → create an `ask_thread` (title = truncated question).
  - With `thread_id` → load the thread (404 if missing or not owned by the
    user), take its **last `ASK_HISTORY_TURNS`** turns ordered by `created_at`,
    concatenate their `messages` into `history_messages`, and pass to `run_ask`.
  - Persist a new `ask_turn` (query, answer, model, tokens, cost, used_tools,
    citations, `messages=result.turn_messages`); bump `thread.updated_at`;
    commit. Return the answer, citations, used_tools, cost_usd, and `thread_id`.
- **`GET /api/ask/threads`** — list the user's threads, newest `updated_at`
  first: `id, title, created_at, updated_at, turn_count, total_cost_usd`.
- **`GET /api/ask/threads/{id}`** — rehydrate one thread for display (404 if not
  owned): `id, title`, and its turns in order (`id, query, answer, citations,
  used_tools, cost_usd, created_at`). Replay `messages` are not returned to the
  client.
- **`DELETE /api/ask/threads/{id}`** — delete a conversation (CASCADE deletes
  its turns); 404 if not owned; 204 on success.

## 4. Frontend (`frontend/src/views/AskView.vue`, `frontend/src/api/ask.ts`)

- **`api/ask.ts`**: `askQuestion(question, threadId?, signal?)` (returns the
  response incl. `thread_id`), `listThreads()`, `getThread(id)`,
  `deleteThread(id)`, with types mirroring the Pydantic models.
- **`AskView`** becomes a chat:
  - A scrollable **transcript** of Q&A pairs. Each answer reuses the existing
    sanitized-markdown rendering and citation-card list (with page deep-links);
    the per-turn tools/cost meta line is preserved.
  - A follow-up question input pinned below the transcript; submitting posts
    with the current `thread_id` and appends the new turn.
  - A **conversation sidebar** listing past threads (title + relative time),
    click to resume; a **"New conversation"** action clears to an empty thread;
    per-thread delete.
  - Routes: `/ask` (new/empty) and `/ask/:threadId` (resume). Loading a
    `:threadId` calls `getThread` and renders its turns.
  - The 503 "no API key" and network error handling carries over unchanged.

## 5. Config (`src/library/config.py`, `.env.example`)

- **`LIBRARY_ASK_HISTORY_TURNS`** — `int`, default `3`. Sliding window of prior
  turns re-fed into the loop. `0` disables history (each turn answered cold but
  still recorded under its thread). `ASK_MAX_TOOL_TURNS` still bounds each
  turn's own loop.

## 6. Testing

**Backend (pytest):**
- Migration round-trips (upgrade creates the tables + drops `ask_logs`;
  downgrade restores `ask_logs`).
- `run_ask` with seeded `history_messages` (mocked `AsyncAnthropic`): prior
  `tool_result` blocks are present in the replayed `messages`; the window caps
  at `ASK_HISTORY_TURNS`; `turn_messages` captures the new turn's blocks.
- `POST /api/ask`: new-thread path creates a thread and returns its id;
  continue path loads history and writes a second turn; 404 for a foreign/absent
  `thread_id`; 503 with no API key.
- `GET /api/ask/threads` and `/{id}`: ownership scoping, `turn_count` /
  `total_cost_usd`, 404s.
- `DELETE`: cascade removes turns; 404 for non-owned.

**Frontend (vitest):**
- Transcript renders multiple turns with citations.
- Follow-up submit posts the active `thread_id` and appends the answer.
- Sidebar lists threads, resume loads a thread, "New conversation" clears,
  delete removes a thread.

**Docs & journal:** update `docs/ask.md` (conversational section, new config,
remove the "single-question" limitation), `docs/api.md` (new endpoints + the
`thread_id` field), `docs/architecture.md` (replace the `ask_logs` mention with
`ask_threads`/`ask_turns`); add a `journal/260622-conversational-ask.md` entry.

## Out of scope (this sub-project)

- Rolling summarization of long threads (windowing only for v1).
- Cross-encoder re-ranking, MCP exposure of Ask (unchanged from prior release).
- Comparative/"is this more than usual?" queries — that is sub-project 5
  (document series), the next session.
