# Conversational Ask Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ask multi-turn — persist conversations server-side and re-feed prior turns (Q&A + tool results) into the Claude loop so follow-ups like *"what about last year?"* resolve against context.

**Architecture:** Two new tables (`ask_threads`, `ask_turns`) replace the write-only `ask_logs`. `ask_turns` stores each turn's serialized Anthropic message blocks for replay. `run_ask` accepts a rehydrated history prefix; `POST /api/ask` resolves/creates a thread, windows the last N turns into that prefix, and persists the new turn. Thread CRUD endpoints back a chat UI with a conversation sidebar.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Anthropic SDK (prompt caching), Pydantic v2; Vue 3 + TS + Vite + Tailwind, vue-router, vitest.

## Global Constraints

- Python 3.13, full type annotations on every signature and non-obvious variable.
- `uv` for all deps/tests: run backend tests with `uv run pytest`, type-check with `uv run mypy src`, lint/format with `uv run ruff check` / `uv run ruff format`.
- Frontend: `npm run test:unit` (vitest), `npm run lint`, `npm run type-check` from `frontend/`.
- Tests are required for every task; never skip. Commit per task; end commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Migration revision ids are zero-padded sequential strings; current head is `0007`. The new migration is `0008`, `down_revision = "0007"`.
- All `/api/ask*` routes require the current user (enforced at include level in `app.py`) and return 503 when no Anthropic key is configured (answer path only).
- `ask_logs` has NO readers anywhere — dropping it is safe.

---

## File Structure

- `migrations/versions/0008_ask_threads.py` — **create**: drop `ask_logs`, add `ask_threads` + `ask_turns`.
- `src/library/models.py` — **modify**: remove `AskLog`; add `AskThread`, `AskTurn`.
- `src/library/ask/engine.py` — **modify**: `run_ask(history_messages=...)`, block serialization, `AskResult.turn_messages`, prompt caching.
- `src/library/config.py` — **modify**: add `ask_history_turns`.
- `src/library/api/ask.py` — **modify**: thread resolution + persistence on `POST /api/ask`; new thread CRUD endpoints + Pydantic models.
- `frontend/src/api/ask.ts` — **modify**: `thread_id` plumbing + thread CRUD client functions/types.
- `frontend/src/views/AskView.vue` — **modify**: chat transcript + follow-up + routing/resume.
- `frontend/src/components/ask/ConversationSidebar.vue` — **create**: thread list / new / delete.
- `frontend/src/router/index.ts` — **modify**: add `/ask/:threadId` route.
- Tests: `tests/test_migrations.py`, `tests/test_models.py`, `tests/test_api_ask.py`, `tests/test_config.py`, `frontend/src/api/__tests__/ask.spec.ts`, `frontend/src/views/__tests__/AskView.spec.ts`, `frontend/src/components/ask/__tests__/ConversationSidebar.spec.ts`.
- Docs: `docs/ask.md`, `docs/api.md`, `docs/architecture.md`; `journal/260622-conversational-ask.md`.

---

## Task 1: Data model — drop `ask_logs`, add `ask_threads` + `ask_turns`

**Files:**
- Modify: `src/library/models.py` (remove `AskLog` at lines 400-418; add two models)
- Create: `migrations/versions/0008_ask_threads.py`
- Modify: `src/library/api/ask.py` (minimal switch off the dropped `AskLog`)
- Test: `tests/test_migrations.py`, `tests/test_models.py`, `tests/test_api_ask.py`

**Why the API is touched here.** `api/ask.py` imports and writes `AskLog`. Dropping the model without updating the writer leaves the app un-importable and tests red between tasks. So Task 1 also makes the minimal API change: stop writing `AskLog`, instead create a thread + a single turn (no `thread_id` in/out, `messages=[]`). Full threading lands in Task 3.

**Interfaces:**
- Produces: `AskThread(id, user_id, title, created_at, updated_at, turns)` and `AskTurn(id, thread_id, query, answer, model, input_tokens, output_tokens, cost_usd, used_tools, citations, messages, created_at, thread)` ORM models. `AskLog` no longer exists. `POST /api/ask` persists one `AskThread` + one `AskTurn` per request (response shape unchanged — no `thread_id` yet).

- [ ] **Step 1: Update the migration round-trip table set (failing test)**

In `tests/test_migrations.py`, change `EXPECTED_TABLES`: remove `"ask_logs"`, add `"ask_threads"` and `"ask_turns"`. Add a new column-shape test:

```python
def test_ask_turns_has_messages_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'ask_turns' AND column_name = 'messages'
        """,
    )
    assert rows == [("messages", "jsonb", "NO")]


def test_ask_logs_table_is_gone(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        "SELECT tablename FROM pg_tables WHERE tablename = 'ask_logs'",
    )
    assert rows == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_migrations.py::test_ask_turns_has_messages_column tests/test_migrations.py::test_ask_logs_table_is_gone -v`
Expected: FAIL (migration `0008` and tables don't exist yet).

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0008_ask_threads.py` (model on `0005_ask_logs.py`):

```python
"""ask threads

Conversational Ask: ask_threads + ask_turns replace the write-only ask_logs.
ask_turns stores each turn's serialized Anthropic message blocks for replay.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("ask_logs")
    op.create_table(
        "ask_threads",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_ask_threads_user_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ask_threads")),
    )
    op.create_index(
        op.f("ix_ask_threads_user_id"), "ask_threads", ["user_id"], unique=False
    )
    op.create_table(
        "ask_turns",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column(
            "used_tools", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "citations", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "messages", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["ask_threads.id"],
            name=op.f("fk_ask_turns_thread_id_ask_threads"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ask_turns")),
    )
    op.create_index(
        op.f("ix_ask_turns_thread_id"), "ask_turns", ["thread_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ask_turns_thread_id"), table_name="ask_turns")
    op.drop_table("ask_turns")
    op.drop_index(op.f("ix_ask_threads_user_id"), table_name="ask_threads")
    op.drop_table("ask_threads")
    op.create_table(
        "ask_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column(
            "used_tools", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_ask_logs_user_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ask_logs")),
    )
```

- [ ] **Step 4: Replace the `AskLog` model**

In `src/library/models.py`, delete `class AskLog` (lines 400-418) and put in its place:

```python
class AskThread(Base):
    """One Ask conversation: an ordered series of question/answer turns."""

    __tablename__ = "ask_threads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    turns: Mapped[list["AskTurn"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AskTurn.created_at",
    )


class AskTurn(Base):
    """One question/answer turn within a thread (cost + provenance + replay).

    Subsumes the former ``ask_logs`` audit row. ``messages`` holds the
    serialized Anthropic message blocks this turn produced (the user question
    plus assistant ``tool_use`` / ``tool_result`` / final-answer blocks) so a
    follow-up can replay prior tool results without re-querying.
    """

    __tablename__ = "ask_turns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("ask_threads.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    used_tools: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    citations: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    messages: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    thread: Mapped[AskThread] = relationship(back_populates="turns")
```

(`BigInteger, ForeignKey, Mapped, mapped_column, DateTime, Text, String, Integer, Float, JSONB, func, text, datetime, Any` are already imported in this file — confirm and only add what is missing.)

- [ ] **Step 5: Add a model smoke test**

In `tests/test_models.py`, add (follow the file's existing async-session style):

```python
async def test_ask_thread_cascades_to_turns(async_session: AsyncSession) -> None:
    from library.models import AskThread, AskTurn

    thread = AskThread(title="Energy bills")
    thread.turns.append(
        AskTurn(query="who?", answer="Vattenfall", model="claude-sonnet-4-6", messages=[])
    )
    async_session.add(thread)
    await async_session.commit()

    await async_session.delete(thread)
    await async_session.commit()

    remaining = (await async_session.execute(select(AskTurn))).scalars().all()
    assert remaining == []
```

(Match the actual session fixture name used in `tests/test_models.py`; adapt imports to the file's conventions.)

- [ ] **Step 6: Minimal API switch off `AskLog` (keep the tree green)**

In `src/library/api/ask.py`, change the import `from library.models import AskLog, User` to `from library.models import AskThread, AskTurn, User`, and replace the `session.add(AskLog(...))` block with:

```python
    thread = AskThread(user_id=user.id, title=request.question.strip()[:120])
    session.add(thread)
    await session.flush()
    session.add(
        AskTurn(
            thread_id=thread.id,
            query=request.question,
            answer=result.answer,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            used_tools={"tools": result.used_tools},
            citations=[
                {"document_id": c.document_id, "title": c.title, "page_number": c.page_number}
                for c in result.citations
            ],
            messages=[],
        )
    )
    await session.commit()
```

(`AsyncSession.flush` is already awaited elsewhere; `session` is in scope. Response construction is unchanged.)

In `tests/test_api_ask.py`, replace the `_ask_logs_count` helper and its single use in `test_ask_semantic_answers_with_citation`:

```python
def _ask_turns_count(database_url: str) -> int:
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM ask_turns")).scalar_one()
    finally:
        engine.dispose()
```

and change the assertion `assert _ask_logs_count(api_database_url) == 1` to `assert _ask_turns_count(api_database_url) == 1`.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_migrations.py tests/test_models.py tests/test_api_ask.py -v`
Expected: PASS. Then `uv run mypy src && uv run ruff check && uv run ruff format`.

- [ ] **Step 8: Commit**

```bash
git add src/library/models.py migrations/versions/0008_ask_threads.py src/library/api/ask.py tests/test_migrations.py tests/test_models.py tests/test_api_ask.py
git commit -m "$(printf 'feat(ask): ask_threads + ask_turns replace ask_logs\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: Engine — history replay, turn-message capture, prompt caching

**Files:**
- Modify: `src/library/ask/engine.py`
- Test: `tests/test_api_ask.py` (engine-level unit tests use the existing fakes)

**Interfaces:**
- Consumes: `AskThread`/`AskTurn` (not directly — engine stays storage-agnostic).
- Produces:
  - `run_ask(session, *, question, settings, client, history_messages: list[dict[str, Any]] | None = None) -> AskResult`
  - `AskResult` gains `turn_messages: list[dict[str, Any]]` (the block sequence THIS turn produced: leading user question + assistant/tool_result/final-answer messages, all as plain JSON-safe dicts).
  - `_serialize_block(block: Any) -> dict[str, Any]` helper.

- [ ] **Step 1: Write failing engine tests**

Add to `tests/test_api_ask.py` (reuses `_Response`, `_ToolUseBlock`, `_TextBlock`, `_FakeAnthropic`, `_unit_vector`):

```python
@pytest.mark.asyncio
async def test_run_ask_captures_turn_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """turn_messages records the user question, the tool dance, and the answer
    as plain dicts suitable for replay/persistence."""
    from typing import cast

    from library.config import get_settings
    from library.ask.engine import run_ask

    async def fake_embed_query(text_value: str, *, settings: Any, client: Any = None) -> list[float]:
        return _unit_vector(0)

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)

    async def fake_search(session: Any, *, query: str, query_embedding: Any, top_k: int) -> list[Any]:
        return []

    monkeypatch.setattr(ask_engine, "semantic_search", fake_search)

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[_ToolUseBlock(name="semantic_search", input={"query": "x"}, id="t1")],
                usage=_Usage(10, 5),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="No matches.")],
                usage=_Usage(8, 4),
            ),
        ]
    )
    settings = get_settings()
    result = await run_ask(
        cast(Any, None), question="anything?", settings=settings, client=cast(Any, client)
    )

    roles = [m["role"] for m in result.turn_messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    # every block is a plain dict (JSON-serialisable), not an SDK/dataclass object
    for message in result.turn_messages:
        for block in (message["content"] if isinstance(message["content"], list) else []):
            assert isinstance(block, dict)


@pytest.mark.asyncio
async def test_run_ask_replays_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """history_messages are prepended to the API call's messages, so prior
    tool results are visible to the follow-up turn."""
    from typing import cast

    from library.config import get_settings
    from library.ask.engine import run_ask

    client = _FakeAnthropic(
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="2025 was Vattenfall.")], usage=_Usage(5, 3))]
    )
    history = [
        {"role": "user", "content": [{"type": "text", "text": "who in 2024?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Eneco [#1]."}]},
    ]
    settings = get_settings()
    await run_ask(
        cast(Any, None),
        question="and 2025?",
        settings=settings,
        client=cast(Any, client),
        history_messages=history,
    )

    sent = client.messages.calls[0]["messages"]
    assert sent[0]["content"][0]["text"] == "who in 2024?"
    assert sent[-1]["content"][-1]["text"] == "and 2025?"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_ask.py::test_run_ask_captures_turn_messages tests/test_api_ask.py::test_run_ask_replays_history -v`
Expected: FAIL (`run_ask` lacks `history_messages`; `AskResult` lacks `turn_messages`).

- [ ] **Step 3: Add serialization + the `turn_messages` field**

In `src/library/ask/engine.py`, change the dataclass import and `AskResult`:

```python
from dataclasses import dataclass, field
```

```python
@dataclass(slots=True)
class AskResult:
    """The answer plus citations, tools used, cost, and replay blocks."""

    answer: str
    citations: list[AskCitation]
    used_tools: list[str]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    turn_messages: list[dict[str, Any]] = field(default_factory=list)
```

Add the serializer near `_text_of`:

```python
def _serialize_block(block: Any) -> dict[str, Any]:
    """Convert an Anthropic content block (SDK model or test fake) to a plain,
    JSON-serialisable dict suitable for re-sending and for JSONB storage."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": block.text}
    if block_type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    return {"type": block_type}
```

- [ ] **Step 4: Rewrite `run_ask` to seed history, capture blocks, and cache**

Replace the body of `run_ask` (from the signature through the loop) with:

```python
async def run_ask(
    session: AsyncSession,
    *,
    question: str,
    settings: Settings,
    client: AsyncAnthropic,
    history_messages: list[dict[str, Any]] | None = None,
) -> AskResult:
    """Answer ``question`` from the archive via a bounded Claude tool-use loop.

    ``history_messages`` is a rehydrated prefix of prior turns (already in block
    form); it is prepended so follow-ups can reason over earlier tool results.
    """
    model = settings.ask_model
    result = AskResult(answer="", citations=[], used_tools=[], model=model)
    cited: set[int] = set()
    pages: dict[int, int] = {}
    used: list[str] = []

    history = list(history_messages or [])
    question_msg: dict[str, Any] = {"role": "user", "content": [{"type": "text", "text": question}]}
    messages: list[dict[str, Any]] = [*history, question_msg]
    new_messages: list[dict[str, Any]] = [question_msg]
    _apply_cache_control(messages, len(history))

    system_prompt: list[dict[str, Any]] = [
        {"type": "text", "text": _system_prompt(date.today()), "cache_control": {"type": "ephemeral"}}
    ]

    answer = ""
    for _ in range(max(1, settings.ask_max_tool_turns)):
        response = await client.messages.create(
            model=model,
            max_tokens=settings.ask_max_answer_tokens,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += estimate_cost_usd(
            model, response.usage.input_tokens, response.usage.output_tokens
        )

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": [_serialize_block(block) for block in response.content],
        }

        if response.stop_reason != "tool_use":
            answer = _text_of(response.content)
            new_messages.append(assistant_msg)
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            used.append(block.name)
            output = await _dispatch_tool(
                session, settings, block.name, dict(block.input), cited, pages
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                }
            )
        if not tool_results:
            answer = _text_of(response.content)
            new_messages.append(assistant_msg)
            break
        tool_msg: dict[str, Any] = {"role": "user", "content": tool_results}
        messages.append(assistant_msg)
        messages.append(tool_msg)
        new_messages.append(assistant_msg)
        new_messages.append(tool_msg)
    else:
        logger.info("ask hit the tool-turn limit without a final answer")

    result.answer = answer or "I couldn't find an answer to that in the archive."
    mentioned = {int(match) for match in re.findall(r"#(\d+)", answer)} & cited
    result.citations = await _citations_for(session, mentioned or cited, pages)
    result.used_tools = list(dict.fromkeys(used))
    result.turn_messages = new_messages
    return result
```

Add the cache helper above `run_ask`:

```python
def _apply_cache_control(messages: list[dict[str, Any]], history_len: int) -> None:
    """Mark the end of the rehydrated history prefix with an ephemeral cache
    breakpoint so re-sent prior turns hit the Anthropic prompt cache. Best
    effort: a no-op when there is no history or the boundary isn't block-form."""
    if history_len == 0:
        return
    boundary = messages[history_len - 1]
    content = boundary.get("content")
    if isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
```

Note: assistant content is now stored/sent as serialized dicts (not raw SDK objects); `_text_of` still runs on the raw `response.content`, so it is unchanged.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_api_ask.py -v`
Expected: PASS. The API already writes `ask_turns` (Task 1) and still ignores `turn_messages` (it persists `messages=[]` until Task 3); `run_ask`'s output for the existing tests is unchanged apart from the new `turn_messages` field. Then `uv run mypy src && uv run ruff check`.

- [ ] **Step 6: Commit**

```bash
git add src/library/ask/engine.py tests/test_api_ask.py
git commit -m "$(printf 'feat(ask): run_ask replays history and captures turn messages\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: Config + `POST /api/ask` threading & persistence

**Files:**
- Modify: `src/library/config.py` (add `ask_history_turns`), `.env.example`
- Modify: `src/library/api/ask.py`
- Test: `tests/test_config.py`, `tests/test_api_ask.py`

**Interfaces:**
- Consumes: `run_ask(..., history_messages=...)`, `AskResult.turn_messages`; `AskThread`, `AskTurn`.
- Produces: `AskRequest{question, thread_id: int | None = None}`; `AskResponse{answer, citations, used_tools, cost_usd, thread_id: int}`; module helpers `_thread_title(question) -> str`, `_history_messages(session, thread_id, turns) -> list[dict[str, Any]]`.

- [ ] **Step 1: Add the config setting (with a test)**

In `src/library/config.py`, after `ask_max_answer_tokens` (line 64) add:

```python
    ask_history_turns: int = 3  # prior turns re-fed into the loop; 0 disables.
```

In `tests/test_config.py` add:

```python
def test_ask_history_turns_default() -> None:
    from library.config import Settings

    assert Settings().ask_history_turns == 3
```

In `.env.example`, add near the other `LIBRARY_ASK_*` entries: `LIBRARY_ASK_HISTORY_TURNS=3`.

- [ ] **Step 2: Write failing API tests**

Add to `tests/test_api_ask.py`. Use a helper to read turn rows. The first test asserts a new thread is created and its id returned; the second asserts a follow-up replays history.

```python
def _thread_turn_counts(database_url: str) -> list[tuple[int, int]]:
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            return [
                (int(tid), int(n))
                for tid, n in connection.execute(
                    text(
                        "SELECT thread_id, count(*) FROM ask_turns GROUP BY thread_id"
                    )
                ).all()
            ]
    finally:
        engine.dispose()


def test_ask_creates_thread_and_returns_id(
    api_client: TestClient, api_database_url: str, with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_anthropic(
        monkeypatch,
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="No data.")], usage=_Usage(8, 3))],
    )
    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})
    assert response.status_code == 200, response.text
    thread_id = response.json()["thread_id"]
    assert isinstance(thread_id, int)
    assert _thread_turn_counts(api_database_url) == [(thread_id, 1)]


def test_ask_follow_up_replays_prior_turn(
    api_client: TestClient, api_database_url: str, with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_ask(session: Any, *, question: str, settings: Any, client: Any, history_messages=None):  # type: ignore[no-untyped-def]
        captured["history"] = history_messages
        from library.ask.engine import AskResult

        return AskResult(
            answer="ok", citations=[], used_tools=[], model=settings.ask_model,
            turn_messages=[{"role": "user", "content": [{"type": "text", "text": question}]},
                           {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}],
        )

    monkeypatch.setattr(ask_module, "run_ask", fake_run_ask)

    first = api_client.post("/api/ask", json={"question": "who in 2024?"})
    thread_id = first.json()["thread_id"]
    api_client.post("/api/ask", json={"question": "and 2025?", "thread_id": thread_id})

    assert captured["history"]  # second call received the first turn's messages
    assert captured["history"][0]["content"][0]["text"] == "who in 2024?"


def test_ask_foreign_thread_is_404(
    api_client: TestClient, api_database_url: str, with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_anthropic(
        monkeypatch,
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="x")], usage=_Usage(1, 1))],
    )
    response = api_client.post("/api/ask", json={"question": "hi", "thread_id": 999999})
    assert response.status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_config.py::test_ask_history_turns_default tests/test_api_ask.py::test_ask_creates_thread_and_returns_id tests/test_api_ask.py::test_ask_follow_up_replays_prior_turn tests/test_api_ask.py::test_ask_foreign_thread_is_404 -v`
Expected: FAIL.

- [ ] **Step 4: Update `POST /api/ask`**

In `src/library/api/ask.py`, update imports and the handler. Replace the `AskLog` import:

```python
from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.ask import run_ask
from library.models import AskThread, AskTurn, User
```

Add `thread_id` to the request/response models:

```python
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000, description="The question to answer.")
    thread_id: int | None = Field(default=None, description="Continue an existing conversation.")
```

```python
class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    used_tools: list[str]
    cost_usd: float
    thread_id: int
```

Add helpers above the route:

```python
def _thread_title(question: str) -> str:
    return question.strip()[:120]


async def _history_messages(
    session: AsyncSession, thread_id: int, turns: int
) -> list[dict[str, Any]]:
    """The last ``turns`` turns' message blocks, chronological, flattened."""
    if turns <= 0:
        return []
    rows = (
        await session.execute(
            select(AskTurn.messages)
            .where(AskTurn.thread_id == thread_id)
            .order_by(AskTurn.created_at.desc(), AskTurn.id.desc())
            .limit(turns)
        )
    ).scalars().all()
    history: list[dict[str, Any]] = []
    for turn_messages in reversed(rows):
        history.extend(turn_messages)
    return history
```

Replace the handler body (after the 503 guard) with:

```python
    if request.thread_id is None:
        thread = AskThread(user_id=user.id, title=_thread_title(request.question))
        session.add(thread)
        await session.flush()
    else:
        thread = await session.get(AskThread, request.thread_id)
        if thread is None or thread.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found.")

    history = await _history_messages(session, thread.id, settings.ask_history_turns)

    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
        result = await run_ask(
            session,
            question=request.question,
            settings=settings,
            client=client,
            history_messages=history,
        )

    session.add(
        AskTurn(
            thread_id=thread.id,
            query=request.question,
            answer=result.answer,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            used_tools={"tools": result.used_tools},
            citations=[
                {"document_id": c.document_id, "title": c.title, "page_number": c.page_number}
                for c in result.citations
            ],
            messages=result.turn_messages,
        )
    )
    thread.updated_at = func.now()
    await session.commit()

    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(document_id=c.document_id, title=c.title, page_number=c.page_number)
            for c in result.citations
        ],
        used_tools=result.used_tools,
        cost_usd=result.cost_usd,
        thread_id=thread.id,
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_api_ask.py tests/test_config.py -v`
Expected: PASS. Then `uv run mypy src && uv run ruff check`.

- [ ] **Step 6: Commit**

```bash
git add src/library/config.py src/library/api/ask.py tests/test_api_ask.py tests/test_config.py .env.example
git commit -m "$(printf 'feat(ask): thread POST /api/ask and persist turns\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: Thread management endpoints (list / get / delete)

**Files:**
- Modify: `src/library/api/ask.py`
- Test: `tests/test_api_ask.py`

**Interfaces:**
- Produces: `GET /api/ask/threads -> list[ThreadSummary]`, `GET /api/ask/threads/{thread_id} -> ThreadDetail`, `DELETE /api/ask/threads/{thread_id} -> 204`. Models `ThreadSummary`, `TurnView`, `ThreadDetail`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api_ask.py` (uses `create_user`/`login` from conftest for the ownership case):

```python
def test_thread_lifecycle_list_get_delete(
    api_client: TestClient, api_database_url: str, with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_anthropic(
        monkeypatch,
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="Answer one.")], usage=_Usage(10, 5))],
    )
    created = api_client.post("/api/ask", json={"question": "first question?"})
    thread_id = created.json()["thread_id"]

    listing = api_client.get("/api/ask/threads")
    assert listing.status_code == 200
    summary = next(t for t in listing.json() if t["id"] == thread_id)
    assert summary["title"] == "first question?"
    assert summary["turn_count"] == 1
    assert summary["total_cost_usd"] > 0

    detail = api_client.get(f"/api/ask/threads/{thread_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["turns"][0]["query"] == "first question?"
    assert body["turns"][0]["answer"] == "Answer one."

    deleted = api_client.delete(f"/api/ask/threads/{thread_id}")
    assert deleted.status_code == 204
    assert api_client.get(f"/api/ask/threads/{thread_id}").status_code == 404


def test_thread_get_foreign_user_is_404(
    api_client: TestClient, api_database_url: str, api_app, with_api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient as TC

    from tests.conftest import create_user, login
    from library.jobs import job_app  # if needed; otherwise reuse api_client's app

    _install_anthropic(
        monkeypatch,
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="x")], usage=_Usage(1, 1))],
    )
    thread_id = api_client.post("/api/ask", json={"question": "mine"}).json()["thread_id"]

    other = create_user(api_database_url)
    with TC(api_app) as other_client:
        login(other_client, other)
        assert other_client.get(f"/api/ask/threads/{thread_id}").status_code == 404
        assert other_client.delete(f"/api/ask/threads/{thread_id}").status_code == 404
```

(Adjust the second test to the conftest's available fixtures/imports — the key assertions are the two 404s for a non-owner. If wiring a second client is awkward, instead insert a thread with a different `user_id` via raw SQL and assert `api_client` gets 404.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_ask.py::test_thread_lifecycle_list_get_delete -v`
Expected: FAIL (routes 404 — not defined).

- [ ] **Step 3: Add the models and routes**

In `src/library/api/ask.py` add models after `AskResponse`:

```python
class ThreadSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    total_cost_usd: float


class TurnView(BaseModel):
    id: int
    query: str
    answer: str
    citations: list[Citation]
    used_tools: list[str]
    cost_usd: float
    created_at: datetime


class ThreadDetail(BaseModel):
    id: int
    title: str
    turns: list[TurnView]
```

Add routes after `ask`:

```python
@router.get("/ask/threads", response_model=list[ThreadSummary], summary="List Ask conversations")
async def list_threads(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ThreadSummary]:
    rows = (
        await session.execute(
            select(
                AskThread.id,
                AskThread.title,
                AskThread.created_at,
                AskThread.updated_at,
                func.count(AskTurn.id),
                func.coalesce(func.sum(AskTurn.cost_usd), 0.0),
            )
            .outerjoin(AskTurn, AskTurn.thread_id == AskThread.id)
            .where(AskThread.user_id == user.id)
            .group_by(AskThread.id)
            .order_by(AskThread.updated_at.desc())
        )
    ).all()
    return [
        ThreadSummary(
            id=tid, title=title, created_at=created, updated_at=updated,
            turn_count=count, total_cost_usd=float(cost),
        )
        for tid, title, created, updated, count, cost in rows
    ]


async def _owned_thread(session: AsyncSession, thread_id: int, user: User) -> AskThread:
    thread = await session.get(AskThread, thread_id)
    if thread is None or thread.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return thread


@router.get("/ask/threads/{thread_id}", response_model=ThreadDetail, summary="Get one conversation")
async def get_thread(
    thread_id: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThreadDetail:
    thread = await _owned_thread(session, thread_id, user)
    turns = (
        await session.execute(
            select(AskTurn).where(AskTurn.thread_id == thread_id).order_by(AskTurn.created_at, AskTurn.id)
        )
    ).scalars().all()
    return ThreadDetail(
        id=thread.id,
        title=thread.title,
        turns=[
            TurnView(
                id=t.id,
                query=t.query,
                answer=t.answer,
                citations=[Citation(**c) for c in t.citations],
                used_tools=list(t.used_tools.get("tools", [])),
                cost_usd=t.cost_usd,
                created_at=t.created_at,
            )
            for t in turns
        ],
    )


@router.delete("/ask/threads/{thread_id}", status_code=204, summary="Delete a conversation")
async def delete_thread(
    thread_id: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    thread = await _owned_thread(session, thread_id, user)
    await session.delete(thread)
    await session.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_api_ask.py -v`
Expected: PASS. Then `uv run mypy src && uv run ruff check && uv run ruff format`.

- [ ] **Step 5: Commit**

```bash
git add src/library/api/ask.py tests/test_api_ask.py
git commit -m "$(printf 'feat(ask): thread list/get/delete endpoints\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: Frontend API client — `thread_id` + thread CRUD

**Files:**
- Modify: `frontend/src/api/ask.ts`
- Test: `frontend/src/api/__tests__/ask.spec.ts`

**Interfaces:**
- Produces: `askQuestion(question, threadId?, signal?) -> Promise<AskResponse>` (now includes `thread_id`); `listThreads()`, `getThread(id)`, `deleteThread(id)`; interfaces `AskResponse` (+`thread_id`), `ThreadSummary`, `TurnView`, `ThreadDetail`.

- [ ] **Step 1: Write failing tests**

In `frontend/src/api/__tests__/ask.spec.ts`, update the first test's `body` to include `thread_id: 5` and expect `result.thread_id === 5`. Add:

```typescript
it('includes thread_id in the POST body when continuing a thread', async () => {
  fetchMock.mockResolvedValue(
    jsonResponse({ answer: 'a', citations: [], used_tools: [], cost_usd: 0, thread_id: 5 }),
  )
  await askQuestion('follow up?', 5)
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  expect(JSON.parse(init.body as string)).toEqual({ question: 'follow up?', thread_id: 5 })
})

it('lists, gets, and deletes threads', async () => {
  fetchMock.mockResolvedValueOnce(jsonResponse([{ id: 1, title: 'T', created_at: '', updated_at: '', turn_count: 2, total_cost_usd: 0.01 }]))
  const threads = await listThreads()
  expect(threads[0].turn_count).toBe(2)

  fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, title: 'T', turns: [] }))
  const detail = await getThread(1)
  expect(detail.id).toBe(1)

  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await deleteThread(1)
  const [url, init] = fetchMock.mock.calls[2] as [string, RequestInit]
  expect(url).toBe('/api/ask/threads/1')
  expect(init.method).toBe('DELETE')
})
```

Add the new imports at the top of the spec: `import { askQuestion, listThreads, getThread, deleteThread } from '../ask'`.

- [ ] **Step 2: Run to verify failure**

Run (from `frontend/`): `npm run test:unit -- src/api/__tests__/ask.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement the client**

Replace `frontend/src/api/ask.ts` body, keeping the file's docstring style:

```typescript
import { apiFetch } from './client'

export interface AskCitation {
  document_id: number
  title: string | null
  page_number: number | null
}

export interface AskResponse {
  answer: string
  citations: AskCitation[]
  used_tools: string[]
  cost_usd: number
  thread_id: number
}

export interface ThreadSummary {
  id: number
  title: string
  created_at: string
  updated_at: string
  turn_count: number
  total_cost_usd: number
}

export interface TurnView {
  id: number
  query: string
  answer: string
  citations: AskCitation[]
  used_tools: string[]
  cost_usd: number
  created_at: string
}

export interface ThreadDetail {
  id: number
  title: string
  turns: TurnView[]
}

export function askQuestion(
  question: string,
  threadId?: number,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const body: { question: string; thread_id?: number } = { question }
  if (threadId !== undefined) body.thread_id = threadId
  return apiFetch<AskResponse>('/api/ask', { method: 'POST', body, signal })
}

export function listThreads(): Promise<ThreadSummary[]> {
  return apiFetch<ThreadSummary[]>('/api/ask/threads')
}

export function getThread(id: number): Promise<ThreadDetail> {
  return apiFetch<ThreadDetail>(`/api/ask/threads/${id}`)
}

export function deleteThread(id: number): Promise<void> {
  return apiFetch<void>(`/api/ask/threads/${id}`, { method: 'DELETE' })
}
```

- [ ] **Step 4: Run tests**

Run (from `frontend/`): `npm run test:unit -- src/api/__tests__/ask.spec.ts && npm run type-check && npm run lint`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/ask.ts frontend/src/api/__tests__/ask.spec.ts
git commit -m "$(printf 'feat(ask): frontend client for threads + thread_id\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: AskView chat transcript + follow-up + routing/resume

**Files:**
- Modify: `frontend/src/views/AskView.vue`, `frontend/src/router/index.ts`
- Test: `frontend/src/views/__tests__/AskView.spec.ts`

**Interfaces:**
- Consumes: `askQuestion`, `getThread`, types from `@/api/ask`.
- Produces: a chat `AskView` driven by route param `threadId`; renders an ordered transcript of `{ query, answerHtml, citations, used_tools, cost_usd }` turns; posts follow-ups with the active `thread_id`. The conversation sidebar (Task 7) mounts inside it.

- [ ] **Step 1: Add the resume route**

In `frontend/src/router/index.ts`, after the `/ask` record add:

```typescript
  {
    path: '/ask/:threadId',
    name: 'ask-thread',
    component: () => import('../views/AskView.vue'),
  },
```

- [ ] **Step 2: Write failing component tests**

Update `frontend/src/views/__tests__/AskView.spec.ts`. The mock now needs `getThread`/`listThreads`/`deleteThread`. Adjust `vi.mock('@/api/ask', ...)` to export all four. Add a router with the `ask-thread` route. Replace the single-answer assertions with transcript-based ones:

```typescript
it('appends a turn to the transcript and posts with thread_id on follow-up', async () => {
  askQuestionMock.mockResolvedValueOnce({
    answer: 'First answer [#1].', citations: [{ document_id: 1, title: 'Doc', page_number: null }],
    used_tools: ['semantic_search'], cost_usd: 0.01, thread_id: 42,
  })
  const w = mountView()
  await typeAndSubmit(w, 'first?')
  expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)

  askQuestionMock.mockResolvedValueOnce({
    answer: 'Second answer.', citations: [], used_tools: [], cost_usd: 0.01, thread_id: 42,
  })
  await typeAndSubmit(w, 'and then?')
  expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(2)
  expect(askQuestionMock).toHaveBeenLastCalledWith('and then?', 42, expect.anything())
})

it('loads a thread when mounted on /ask/:threadId', async () => {
  getThreadMock.mockResolvedValue({
    id: 7, title: 'Energy', turns: [
      { id: 1, query: 'who?', answer: 'Vattenfall [#3].', citations: [{ document_id: 3, title: 'Bill', page_number: 2 }], used_tools: ['query_documents'], cost_usd: 0.02, created_at: '' },
    ],
  })
  await router.push('/ask/7')
  const w = mountView()
  await flushPromises()
  expect(getThreadMock).toHaveBeenCalledWith(7)
  expect(w.text()).toContain('Vattenfall')
})
```

(Define `typeAndSubmit`, `getThreadMock` helpers in the spec following the existing mount/await style; keep the existing 503-error and empty-question tests, adapting them to the transcript layout.)

- [ ] **Step 3: Run to verify failure**

Run (from `frontend/`): `npm run test:unit -- src/views/__tests__/AskView.spec.ts`
Expected: FAIL.

- [ ] **Step 4: Rewrite AskView as a chat**

Rework `frontend/src/views/AskView.vue`:
- State: `const turns = ref<TurnVM[]>([])`, `const threadId = ref<number | null>(null)`, `question`, `loading`, `errorMessage`.
- `TurnVM = { query: string; answerHtml: string; citations: AskCitation[]; usedTools: string[]; costUsd: number }`. Factor the existing markdown→sanitize logic into `renderAnswer(answer: string): string` (reuse `marked` + `DOMPurify`), used both for live answers and rehydrated turns.
- On submit: call `askQuestion(trimmed, threadId.value ?? undefined)`; push a new `TurnVM`; set `threadId.value = res.thread_id`; if the route lacks the id, `router.replace({ name: 'ask-thread', params: { threadId: res.thread_id } })`.
- On mount / route param change: if `route.params.threadId`, call `getThread(Number(...))`, map its `turns` into `TurnVM[]` (via `renderAnswer`), set `threadId`. Watch `() => route.params.threadId` to support sidebar navigation without remount.
- Template: render `turns` as a list of `[data-testid="ask-turn"]` blocks, each reusing the existing answer/citation/meta markup (the `.ask-answer` styles and citation `RouterLink` carry over unchanged). Keep the follow-up `<form>` pinned below the transcript; relabel the button "Ask" / "Send". Preserve the 503/network `friendlyError` handling and the `AppErrorSummary`.
- Provide a `resetConversation()` (clears `turns`, `threadId`, navigates to `/ask`) for the sidebar's "New conversation" (wired in Task 7).

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npm run test:unit -- src/views/__tests__/AskView.spec.ts && npm run type-check && npm run lint`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/AskView.vue frontend/src/router/index.ts frontend/src/views/__tests__/AskView.spec.ts
git commit -m "$(printf 'feat(ask): chat transcript with thread resume\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: Conversation sidebar (list / resume / new / delete)

**Files:**
- Create: `frontend/src/components/ask/ConversationSidebar.vue`
- Modify: `frontend/src/views/AskView.vue` (mount the sidebar)
- Test: `frontend/src/components/ask/__tests__/ConversationSidebar.spec.ts`

**Interfaces:**
- Consumes: `listThreads`, `deleteThread`, `ThreadSummary`.
- Produces: `ConversationSidebar` emitting `select(threadId: number)`, `new`, and refreshing its own list. Props: `activeThreadId: number | null`.

- [ ] **Step 1: Write failing component test**

Create `frontend/src/components/ask/__tests__/ConversationSidebar.spec.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ConversationSidebar from '../ConversationSidebar.vue'
import { listThreads, deleteThread } from '@/api/ask'

vi.mock('@/api/ask', () => ({ listThreads: vi.fn(), deleteThread: vi.fn() }))

describe('ConversationSidebar', () => {
  beforeEach(() => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 1, title: 'Energy bills', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0.05 },
    ])
  })
  afterEach(() => vi.clearAllMocks())

  it('lists threads and emits select when one is clicked', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    expect(w.text()).toContain('Energy bills')
    await w.find('[data-testid="thread-item"]').trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([1])
  })

  it('emits new when the new-conversation button is clicked', async () => {
    const w = mount(ConversationSidebar, { props: { activeThreadId: null } })
    await flushPromises()
    await w.find('[data-testid="new-conversation"]').trigger('click')
    expect(w.emitted('new')).toBeTruthy()
  })

  it('deletes a thread and refreshes', async () => {
    vi.mocked(deleteThread).mockResolvedValue()
    const w = mount(ConversationSidebar, { props: { activeThreadId: 1 } })
    await flushPromises()
    await w.find('[data-testid="thread-delete"]').trigger('click')
    await flushPromises()
    expect(deleteThread).toHaveBeenCalledWith(1)
    expect(listThreads).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run (from `frontend/`): `npm run test:unit -- src/components/ask/__tests__/ConversationSidebar.spec.ts`
Expected: FAIL (component missing).

- [ ] **Step 3: Implement the sidebar**

Create `frontend/src/components/ask/ConversationSidebar.vue`:
- `defineProps<{ activeThreadId: number | null }>()`, `defineEmits<{ select: [number]; new: [] }>()`.
- `const threads = ref<ThreadSummary[]>([])`; `async function refresh() { threads.value = await listThreads() }`; call on mount.
- Expose `refresh` via `defineExpose({ refresh })` so AskView can refresh after a new turn creates a thread.
- Template: a "New conversation" button (`data-testid="new-conversation"`) emitting `new`; a list of threads (`data-testid="thread-item"`) showing `title` + relative/short timestamp, highlighting `activeThreadId`, click emits `select`; a per-row delete button (`data-testid="thread-delete"`) calling `deleteThread(id)` then `refresh()` (and emit `new` if the deleted thread was active). Match the app's Tailwind card/list styling used elsewhere (e.g. the citation list classes in AskView).

- [ ] **Step 4: Mount it in AskView**

In `AskView.vue`, lay out a two-column flex: the sidebar on the left, the transcript+form on the right. Wire `@select="resumeThread"` (navigate to `/ask/:id`), `@new="resetConversation"`, and call the sidebar's exposed `refresh()` after a successful ask that created a new thread (i.e., when the previous `threadId` was null). Use a `ref` to the sidebar component for `refresh()`.

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npm run test:unit && npm run type-check && npm run lint`
Expected: PASS / clean (full frontend suite green).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ask/ConversationSidebar.vue frontend/src/components/ask/__tests__/ConversationSidebar.spec.ts frontend/src/views/AskView.vue
git commit -m "$(printf 'feat(ask): conversation sidebar with resume/new/delete\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: Docs + journal

**Files:**
- Modify: `docs/ask.md`, `docs/api.md`, `docs/architecture.md`
- Create: `journal/260622-conversational-ask.md`

- [ ] **Step 1: Update `docs/ask.md`**
- Add a "Conversational Ask" subsection: threads persist server-side; follow-ups re-feed the last `ASK_HISTORY_TURNS` turns (Q&A + tool results) with prompt caching; the sliding window drops older turns.
- Add `LIBRARY_ASK_HISTORY_TURNS` (default 3) to the config table.
- Remove limitation #2 ("Single-question, single-answer …"); note the cache-miss-on-slide tradeoff instead.
- Replace the `ask_logs` cost note (§1.4) with `ask_turns` (per-turn cost lives on the turn; thread total = sum of turns).

- [ ] **Step 2: Update `docs/api.md`**
- Document `thread_id` on the `POST /api/ask` request and response.
- Add `GET /api/ask/threads`, `GET /api/ask/threads/{id}`, `DELETE /api/ask/threads/{id}` with shapes and the 404 ownership rule.
- Fix the `ask_logs` reference (line ~446) to `ask_turns`.

- [ ] **Step 3: Update `docs/architecture.md`**
- Replace the `ask_logs` mention (line ~120) with `ask_threads` / `ask_turns` and a one-line note on conversation persistence + replay blocks.

- [ ] **Step 4: Write the journal entry**

Create `journal/260622-conversational-ask.md` capturing: the five locked decisions (server-side threads; re-feed Q&A + tool results; sliding window + caching; chat + sidebar; drop `ask_logs`), the `messages` replay-blob approach, and the accepted cache-miss-on-slide tradeoff.

- [ ] **Step 5: Verify docs build/links and commit**

Run: `uv run ruff check` (no code changed, sanity only). Confirm no dangling links to `ask_logs` remain: `grep -rn "ask_logs" docs/ src/ || true` should only show archived/spec history.

```bash
git add docs/ask.md docs/api.md docs/architecture.md journal/260622-conversational-ask.md
git commit -m "$(printf 'docs(ask): document conversational Ask + threads\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Final verification (whole branch)

- [ ] `uv run pytest` — full backend suite green.
- [ ] `uv run mypy src && uv run ruff check && uv run ruff format --check`.
- [ ] From `frontend/`: `npm run test:unit && npm run type-check && npm run lint`.
- [ ] `grep -rn "ask_logs" src/ frontend/src/` returns nothing (only docs archive/spec history may mention it).
- [ ] Whole-branch code review on the most capable model (superpowers:requesting-code-review) before finishing-a-development-branch.

---

## Self-Review notes (spec coverage)

- Spec §1 (data model) → Task 1. §2 (engine replay + caching + turn_messages) → Task 2. §3 (API: POST threading + thread CRUD) → Tasks 3-4. §4 (frontend chat + sidebar) → Tasks 5-7. §5 (config) → Task 3. §6 (testing) → tests in every task + final verification. Docs/journal → Task 8.
- `ask_logs` drop is covered in Task 1 (migration + model) and verified in Tasks 3/8.
- Type consistency: `turn_messages`, `history_messages`, `thread_id`, `_history_messages`, `_thread_title`, `ThreadSummary`/`TurnView`/`ThreadDetail` names are used identically across backend tasks and mirrored in the frontend client (Task 5) and components (Tasks 6-7).
