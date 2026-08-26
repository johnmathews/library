"""Tests for the /api/ask endpoint (Anthropic + embedder mocked)."""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from library.api import ask as ask_module
from library.ask import engine as ask_engine
from library.config import get_settings
from library.models import EMBEDDING_DIM

pytestmark = pytest.mark.integration


# --- Fake Anthropic SDK -----------------------------------------------------


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int
    # Anthropic reports cached tokens separately from `input_tokens`; default 0
    # so existing fixtures keep meaning "nothing was cached".
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ThinkingBlock:
    """A thinking block as the SDK returns one under adaptive thinking.

    `display` defaults to omitted, so `thinking` comes back empty while
    `signature` carries the payload that must be replayed unmodified.
    """

    signature: str
    thinking: str = ""
    type: str = "thinking"


@dataclass
class _ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str
    type: str = "tool_use"


@dataclass
class _Response:
    stop_reason: str
    content: list[Any]
    usage: _Usage


class _FakeMessages:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeAnthropic:
    def __init__(self, responses: list[_Response]) -> None:
        self.messages = _FakeMessages(responses)

    async def __aenter__(self) -> "_FakeAnthropic":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _install_anthropic(
    monkeypatch: pytest.MonkeyPatch, responses: list[_Response]
) -> _FakeAnthropic:
    """Install the fake SDK and return it, so a test can assert on the kwargs
    each `messages.create` actually received."""
    fake = _FakeAnthropic(responses)
    monkeypatch.setattr(ask_module, "AsyncAnthropic", lambda api_key: fake)
    return fake


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stub_thread_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't make a real title-generation model call by default.

    A new thread now fires ``generate_thread_title`` (an extra ``messages.create``
    the per-test fake response lists don't account for). Stub it to return an
    empty title so the thread keeps its truncated-question placeholder — the
    behavior these tests already assert. Titling-specific tests override this.
    """

    async def _no_title(
        client: Any,
        *,
        model: str,
        question: str,
        answer: str,
        settings: Any = None,
        backend: str = "api",
    ) -> Any:
        # Keep this signature in step with `generate_thread_title`. Titling is
        # deliberately non-fatal, so a stale stub does not fail a test — it
        # raises a TypeError that the caller swallows, and every test quietly
        # exercises the error path instead of the stub. It drifted once already.
        return ask_engine.TitleResult(title="", cost_usd=0.0)

    monkeypatch.setattr(ask_module, "generate_thread_title", _no_title)


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


def _seed_document_with_chunk(
    database_url: str, *, marker: str, ocr_text: str, chunk_text: str
) -> int:
    """Insert one indexed document and a single chunk via raw SQL; return id."""
    sha = hashlib.sha256(marker.encode()).hexdigest()
    vector_literal = "[" + ",".join("1" if i == 0 else "0" for i in range(EMBEDDING_DIM)) + "]"
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            document_id = connection.execute(
                text(
                    "INSERT INTO documents (sha256, mime_type, status, source, ocr_text, title)"
                    " VALUES (:sha, 'application/pdf', 'indexed', 'upload', :ocr, :title)"
                    " RETURNING id"
                ),
                {"sha": sha, "ocr": ocr_text, "title": "Employment contract"},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO document_chunks (document_id, chunk_index, text, embedding)"
                    " VALUES (:doc, 1, :txt, CAST(:emb AS vector))"
                ),
                {"doc": document_id, "txt": chunk_text, "emb": vector_literal},
            )
        return document_id
    finally:
        engine.dispose()


def _thread_turn_counts(database_url: str) -> list[tuple[int, int]]:
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            return [
                (int(tid), int(n))
                for tid, n in connection.execute(
                    text("SELECT thread_id, count(*) FROM ask_turns GROUP BY thread_id")
                ).all()
            ]
    finally:
        engine.dispose()


# --- Tests ------------------------------------------------------------------


def test_system_prompt_includes_current_date() -> None:
    """The model must resolve "last year" against today, not its training cutoff."""
    prompt = ask_engine._system_prompt(date(2026, 6, 16))
    assert "2026-06-16" in prompt
    assert "The current year is 2026" in prompt
    assert '"last year" means 2025' in prompt


# --- prompt-cache accounting -----------------------------------------------
#
# The tool loop re-sends the whole conversation each iteration, so a tool result
# fetched on pass 2 is paid for again on passes 3 and 4. Top-level
# `cache_control` makes those re-reads bill at ~0.1x. The accounting has to move
# with it: Anthropic reports cached tokens in fields SEPARATE from
# `input_tokens`, so counting only `input_tokens` makes spend appear to collapse
# the moment caching starts working — partly because tokens stopped being
# counted, not because they stopped being sent.


def test_cached_usage_counts_nothing_extra_when_cache_is_cold() -> None:
    total, billable = ask_engine._cached_usage(_Usage(input_tokens=1000, output_tokens=50))
    assert (total, billable) == (1000, 1000)


def test_cached_usage_totals_include_cached_tokens() -> None:
    """`total` answers "how much context went in", cached or not."""
    usage = _Usage(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=9000,
        cache_creation_input_tokens=900,
    )
    total, _ = ask_engine._cached_usage(usage)
    assert total == 10_000


def test_cached_usage_prices_reads_at_a_tenth_and_writes_at_1_25x() -> None:
    """A cache read must not cost the same as a fresh token, or the whole point
    of caching is invisible in the recorded cost."""
    usage = _Usage(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=9000,
        cache_creation_input_tokens=900,
    )
    _, billable = ask_engine._cached_usage(usage)
    # 100 fresh + 900 read-equivalent (9000 * 0.1) + 1125 write-equivalent
    assert billable == 100 + 900 + 1125


def test_cached_usage_billable_is_far_below_total_when_the_cache_hits() -> None:
    """The property that matters: a warm cache is cheaper than a cold one for
    identical context. Asserting the relationship, not just the arithmetic."""
    context = 20_000
    cold = ask_engine._cached_usage(_Usage(input_tokens=context, output_tokens=10))
    warm = ask_engine._cached_usage(
        _Usage(input_tokens=0, output_tokens=10, cache_read_input_tokens=context)
    )
    assert cold[0] == warm[0] == context  # same context either way
    assert warm[1] < cold[1] / 5  # but far cheaper when served from cache


def test_cached_usage_tolerates_a_usage_object_without_cache_fields() -> None:
    """Older SDKs, and any provider shim, may not carry the cache fields at all.
    Missing must read as "nothing cached", never raise."""

    @dataclass
    class _Bare:
        input_tokens: int
        output_tokens: int

    assert ask_engine._cached_usage(_Bare(input_tokens=7, output_tokens=1)) == (7, 7)


def test_ask_requests_prompt_caching_on_every_tool_loop_call(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this the growing tool-result tail is re-read at full price on
    every iteration of the loop — the dominant cost of a turn."""
    fake = _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[_ToolUseBlock(name="query_documents", input={}, id="t1")],
                usage=_Usage(100, 20),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="An answer.")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    response = api_client.post("/api/ask", json={"question": "anything?"})
    assert response.status_code == 200

    # Both loop iterations, not just the first: the tail grows every pass, so a
    # breakpoint on only the opening call would cache the cheapest request.
    assert len(fake.messages.calls) == 2
    for call in fake.messages.calls:
        assert call["cache_control"] == {"type": "ephemeral"}


# --- reasoning configuration ------------------------------------------------
#
# On this model family, OMITTING `thinking` means no extended reasoning at all —
# the absence of the parameter is not a neutral default. These pin the three
# coupled settings so a future edit cannot silently turn reasoning back off or
# re-starve its token budget.


def test_ask_enables_adaptive_thinking_on_every_call(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[_ToolUseBlock(name="query_documents", input={}, id="t1")],
                usage=_Usage(100, 20),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="An answer.")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    assert api_client.post("/api/ask", json={"question": "anything?"}).status_code == 200

    assert len(fake.messages.calls) == 2
    for call in fake.messages.calls:
        assert call["thinking"] == {"type": "adaptive"}


def test_ask_answer_budget_leaves_room_for_thinking(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking tokens are billed against `max_tokens`. At the old 1024 the
    reasoning could consume the budget and truncate the answer, so the cap and
    the thinking flag are one decision, not two."""
    fake = _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="An answer.")],
                usage=_Usage(100, 20),
            )
        ],
    )

    assert api_client.post("/api/ask", json={"question": "anything?"}).status_code == 200
    assert fake.messages.calls[0]["max_tokens"] >= 4096


def test_ask_replays_thinking_blocks_unmodified_to_later_calls(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API requires thinking blocks to come back byte-identical on the next
    call of the same turn. Dropping or rewriting the signature is rejected, and
    the tool loop re-sends the whole assistant turn every pass — so this is the
    path most likely to break when reasoning is switched on."""
    fake = _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ThinkingBlock(signature="sig-abc123"),
                    _ToolUseBlock(name="query_documents", input={}, id="t1"),
                ],
                usage=_Usage(100, 20),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="An answer.")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    response = api_client.post("/api/ask", json={"question": "anything?"})
    assert response.status_code == 200
    # Reasoning must not leak into the user-visible answer.
    assert response.json()["answer"] == "An answer."

    # The second call must carry the first call's thinking block, signature intact.
    second_call_messages = fake.messages.calls[1]["messages"]
    replayed = [
        block
        for message in second_call_messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "thinking"
    ]
    assert len(replayed) == 1
    assert replayed[0]["signature"] == "sig-abc123"


def test_ask_tool_loop_has_headroom_beyond_four_calls(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 of 51 production turns used all four calls. This asserts the loop can
    now go further — a question needing search -> read -> compare -> verify."""
    tool_calls = [
        _Response(
            stop_reason="tool_use",
            content=[_ToolUseBlock(name="query_documents", input={}, id=f"t{i}")],
            usage=_Usage(100, 20),
        )
        for i in range(5)
    ]
    fake = _install_anthropic(
        monkeypatch,
        [
            *tool_calls,
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="Reached after five tool calls.")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    response = api_client.post("/api/ask", json={"question": "anything?"})
    assert response.status_code == 200
    # Six calls total: the old cap of 4 would have bailed out with the
    # no-answer fallback before ever reaching the real answer.
    assert len(fake.messages.calls) == 6
    assert response.json()["answer"] == "Reached after five tool calls."


def test_ask_without_api_key_returns_503(api_client: TestClient) -> None:
    response = api_client.post("/api/ask", json={"question": "anything?"})
    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


def test_ask_semantic_answers_with_citation(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = _seed_document_with_chunk(
        api_database_url,
        marker="ask-contract",
        ocr_text="Your travel allowance is 0.21 per km.",
        chunk_text="Article 7: the employee receives a travel allowance of 0.21 per km.",
    )

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="semantic_search", input={"query": "travel allowance"}, id="t1"
                    )
                ],
                usage=_Usage(100, 20),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Yes, a travel allowance [#{document_id}].")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    response = api_client.post("/api/ask", json={"question": "Do I have a travel allowance?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "travel allowance" in body["answer"]
    assert body["used_tools"] == ["semantic_search"]
    assert document_id in [citation["document_id"] for citation in body["citations"]]
    assert body["cost_usd"] > 0
    assert (body["thread_id"], 1) in _thread_turn_counts(api_database_url)


def test_ask_structured_answers_provider_question(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            sender_id = connection.execute(
                text(
                    "INSERT INTO senders (name) VALUES ('Vattenfall')"
                    " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                )
            ).scalar_one()
            kind_id = connection.execute(
                text("SELECT id FROM kinds WHERE slug = 'utility-bill'")
            ).scalar_one()
            doc_id = connection.execute(
                text(
                    "INSERT INTO documents"
                    " (sha256, mime_type, status, source, sender_id, kind_id, document_date)"
                    " VALUES (:sha, 'application/pdf', 'indexed', 'upload', :sid, :kid, :d)"
                    " RETURNING id"
                ),
                {
                    "sha": hashlib.sha256(b"energy-2025").hexdigest(),
                    "sid": sender_id,
                    "kid": kind_id,
                    "d": date(2025, 3, 1),
                },
            ).scalar_one()
    finally:
        engine.dispose()

    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="query_documents",
                        input={
                            "aggregate": "distinct_senders",
                            "kind": "utility-bill",
                            "date_from": "2025-01-01",
                            "date_to": "2025-12-31",
                        },
                        id="t1",
                    )
                ],
                usage=_Usage(120, 25),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="Your energy provider was Vattenfall.")],
                usage=_Usage(140, 18),
            ),
        ],
    )

    response = api_client.post(
        "/api/ask", json={"question": "Who was my energy provider last year?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Vattenfall" in body["answer"]
    assert body["used_tools"] == ["query_documents"]
    citation_ids = [c["document_id"] for c in body["citations"]]
    assert doc_id in citation_ids


def test_ask_citation_schema_has_page_number() -> None:
    """The Citation response model must expose page_number for clients."""
    from library.api.ask import Citation

    assert "page_number" in Citation.model_fields


def test_ask_semantic_citation_carries_page_number(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A semantic hit with page_number surfaces it in the API citation; an
    aggregation-only citation gets None."""
    from sqlalchemy import text as sa_text

    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            sha = hashlib.sha256(b"page-number-ask").hexdigest()
            vector_literal = (
                "[" + ",".join("1" if i == 0 else "0" for i in range(EMBEDDING_DIM)) + "]"
            )
            document_id = connection.execute(
                sa_text(
                    "INSERT INTO documents (sha256, mime_type, status, source, ocr_text, title)"
                    " VALUES (:sha, 'application/pdf', 'indexed', 'upload', :ocr, :title)"
                    " RETURNING id"
                ),
                {"sha": sha, "ocr": "travel allowance clause", "title": "Contract 2025"},
            ).scalar_one()
            connection.execute(
                sa_text(
                    "INSERT INTO document_chunks"
                    " (document_id, chunk_index, page_number, text, embedding)"
                    " VALUES (:doc, 1, :page, :txt, CAST(:emb AS vector))"
                ),
                {
                    "doc": document_id,
                    "page": 7,
                    "txt": "Article 7: travel allowance 0.21/km",
                    "emb": vector_literal,
                },
            )
    finally:
        engine.dispose()

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="semantic_search",
                        input={"query": "travel allowance"},
                        id="t1",
                    )
                ],
                usage=_Usage(100, 20),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Yes, travel allowance [#{document_id}].")],
                usage=_Usage(150, 30),
            ),
        ],
    )

    response = api_client.post("/api/ask", json={"question": "travel allowance?"})

    assert response.status_code == 200, response.text
    body = response.json()
    citations = body["citations"]
    matched = [c for c in citations if c["document_id"] == document_id]
    assert matched, f"document {document_id} not in citations {citations}"
    assert matched[0]["page_number"] == 7


def test_ask_empty_corpus_is_honest(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="The archive does not appear to contain that.")],
                usage=_Usage(80, 12),
            )
        ],
    )

    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["citations"] == []
    assert body["used_tools"] == []
    assert "does not appear" in body["answer"]


# --- Engine unit tests (no DB, no HTTP) -------------------------------------


@pytest.mark.asyncio
async def test_run_ask_captures_turn_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """turn_messages records the user question, the tool dance, and the answer
    as plain dicts suitable for replay/persistence."""
    from typing import cast

    from library.ask.engine import run_ask
    from library.config import get_settings

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)

    async def fake_search(
        session: Any, *, query: str, query_embedding: Any, top_k: int, chunks_per_doc: int = 1
    ) -> list[Any]:
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
        for block in message["content"] if isinstance(message["content"], list) else []:
            assert isinstance(block, dict)


@pytest.mark.asyncio
async def test_run_ask_replays_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """history_messages are prepended to the API call's messages, so prior
    tool results are visible to the follow-up turn."""
    from typing import cast

    from library.ask.engine import run_ask
    from library.config import get_settings

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="2025 was Vattenfall.")],
                usage=_Usage(5, 3),
            )
        ]
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


@pytest.mark.asyncio
async def test_run_ask_turn_messages_replayable_when_tool_limit_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting the tool-turn budget must still leave replayable history.

    Otherwise the stored turn ends on a tool_result (role "user") and the next
    question in the thread produces back-to-back user turns → Anthropic 400.
    """
    from typing import cast

    from library.ask.engine import run_ask
    from library.config import get_settings

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    async def fake_search(
        session: Any, *, query: str, query_embedding: Any, top_k: int, chunks_per_doc: int = 1
    ) -> list[Any]:
        return []

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    monkeypatch.setattr(ask_engine, "semantic_search", fake_search)

    settings = get_settings()
    # Every round returns a tool_use, so the loop never reaches a final answer.
    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[_ToolUseBlock(name="semantic_search", input={"query": "x"}, id=f"t{i}")],
                usage=_Usage(10, 5),
            )
            for i in range(settings.ask_max_tool_turns)
        ]
    )
    result = await run_ask(
        cast(Any, None), question="loop forever?", settings=settings, client=cast(Any, client)
    )

    import itertools

    roles = [m["role"] for m in result.turn_messages]
    assert roles[-1] == "assistant"  # history ends on an assistant turn
    # No two consecutive user turns anywhere (the replay-breaking condition).
    assert not any(a == b == "user" for a, b in itertools.pairwise(roles))


@pytest.mark.asyncio
async def test_run_ask_includes_image_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attached images become base64 image content blocks on the user turn (W11)."""
    from typing import cast

    from library.ask.engine import run_ask
    from library.config import get_settings

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="It's a receipt.")],
                usage=_Usage(5, 3),
            )
        ]
    )
    settings = get_settings()
    await run_ask(
        cast(Any, None),
        question="what is this?",
        settings=settings,
        client=cast(Any, client),
        images=[{"media_type": "image/png", "data": "aGVsbG8="}],
    )

    user_content = client.messages.calls[0]["messages"][-1]["content"]
    text_blocks = [b for b in user_content if b["type"] == "text"]
    image_blocks = [b for b in user_content if b["type"] == "image"]
    assert text_blocks[0]["text"] == "what is this?"
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aGVsbG8=",
    }


def test_ask_passes_images_to_engine(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint forwards uploaded images to run_ask (W11)."""
    captured: dict[str, Any] = {}

    async def fake_run_ask(
        session: Any, *, question: str, settings: Any, client: Any, **kwargs: Any
    ):
        captured["images"] = kwargs.get("images")
        from library.ask.engine import AskResult

        return AskResult(
            answer="A receipt.",
            citations=[],
            used_tools=[],
            model=settings.ask_model,
            turn_messages=[{"role": "user", "content": [{"type": "text", "text": question}]}],
        )

    monkeypatch.setattr(ask_module, "run_ask", fake_run_ask)
    response = api_client.post(
        "/api/ask",
        json={
            "question": "what is this?",
            "images": [{"media_type": "image/png", "data": "aGVsbG8="}],
        },
    )
    assert response.status_code == 200, response.text
    assert captured["images"] == [{"media_type": "image/png", "data": "aGVsbG8="}]


def test_ask_rejects_unsupported_image_media_type(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/ask",
        json={"question": "hi", "images": [{"media_type": "image/tiff", "data": "x"}]},
    )
    assert response.status_code == 422


def test_ask_rejects_too_many_images(api_client: TestClient) -> None:
    images = [{"media_type": "image/png", "data": "x"} for _ in range(6)]
    response = api_client.post("/api/ask", json={"question": "hi", "images": images})
    assert response.status_code == 422


# --- Thread persistence tests -----------------------------------------------


def test_ask_creates_thread_and_returns_id(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn", content=[_TextBlock(text="No data.")], usage=_Usage(8, 3)
            )
        ],
    )
    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})
    assert response.status_code == 200, response.text
    thread_id = response.json()["thread_id"]
    assert isinstance(thread_id, int)
    counts = _thread_turn_counts(api_database_url)
    assert (thread_id, 1) in counts


def test_ask_follow_up_replays_prior_turn(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_ask(
        session: Any,
        *,
        question: str,
        settings: Any,
        client: Any,
        history_messages: list[dict[str, Any]] | None = None,
        images: list[dict[str, str]] | None = None,
        backend: str = "api",
        archive_context: str | None = None,
    ):
        captured["history"] = history_messages
        captured["backend"] = backend
        from library.ask.engine import AskResult

        return AskResult(
            answer="ok",
            citations=[],
            used_tools=[],
            model=settings.ask_model,
            turn_messages=[
                {"role": "user", "content": [{"type": "text", "text": question}]},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            ],
        )

    monkeypatch.setattr(ask_module, "run_ask", fake_run_ask)

    first = api_client.post("/api/ask", json={"question": "who in 2024?"})
    thread_id = first.json()["thread_id"]
    api_client.post("/api/ask", json={"question": "and 2025?", "thread_id": thread_id})

    assert captured["history"]  # second call received the first turn's messages
    assert captured["history"][0]["content"][0]["text"] == "who in 2024?"


def test_ask_foreign_thread_is_404(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
) -> None:
    response = api_client.post("/api/ask", json={"question": "hi", "thread_id": 999999})
    assert response.status_code == 404


def test_thread_lifecycle_list_get_delete(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="Answer one.")],
                usage=_Usage(10, 5),
            )
        ],
    )
    created = api_client.post("/api/ask", json={"question": "first question?"})
    assert created.status_code == 200, created.text
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


def _seed_utility_series(database_url: str) -> list[int]:
    """Insert three utility-bill docs for sender Vattenfall, ascending dates and
    amounts 100/100/130.  Returns ids oldest→newest."""
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            sender_id = connection.execute(
                text(
                    "INSERT INTO senders (name) VALUES ('Vattenfall')"
                    " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                )
            ).scalar_one()
            kind_id = connection.execute(
                text("SELECT id FROM kinds WHERE slug = 'utility-bill'")
            ).scalar_one()
            ids: list[int] = []
            bills = [
                (date(2025, 1, 1), 100),
                (date(2025, 2, 1), 100),
                (date(2025, 3, 1), 130),
            ]
            for d, amount in bills:
                sha = hashlib.sha256(f"vattenfall-{d}".encode()).hexdigest()
                doc_id = connection.execute(
                    text(
                        "INSERT INTO documents"
                        " (sha256, mime_type, status, source, sender_id, kind_id,"
                        "  document_date, amount_total, currency)"
                        " VALUES (:sha, 'application/pdf', 'indexed', 'upload',"
                        "         :sid, :kid, :d, :amt, 'EUR')"
                        " RETURNING id"
                    ),
                    {"sha": sha, "sid": sender_id, "kid": kind_id, "d": d, "amt": amount},
                ).scalar_one()
                ids.append(doc_id)
        return ids
    finally:
        engine.dispose()


def test_ask_uses_compare_to_series(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc_ids = _seed_utility_series(api_database_url)
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="compare_to_series",
                        input={
                            "kind": "utility-bill",
                            "sender_contains": "vattenfall",
                            "reference": "latest",
                        },
                        id="c1",
                    )
                ],
                usage=_Usage(120, 25),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Yes, higher than usual [#{doc_ids[-1]}].")],
                usage=_Usage(140, 18),
            ),
        ],
    )
    response = api_client.post(
        "/api/ask", json={"question": "is my latest bill higher than usual?"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_tools"] == ["compare_to_series"]
    assert any(c["document_id"] == doc_ids[-1] for c in body["citations"])


def test_thread_get_foreign_user_is_404(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread owned by another user returns 404 for GET and DELETE."""
    # Insert a thread row owned by a synthetic foreign user_id via raw SQL.
    # This avoids the asyncio event-loop conflict that arises when create_user
    # (which calls asyncio.run) is invoked inside an active TestClient context.
    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as conn:
            foreign_user_id: int = conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, display_name, is_active)"
                    " VALUES ('foreign-thread-owner', 'x', '', true) RETURNING id"
                )
            ).scalar_one()
            foreign_thread_id: int = conn.execute(
                text(
                    "INSERT INTO ask_threads (user_id, title)"
                    " VALUES (:uid, 'foreign thread') RETURNING id"
                ),
                {"uid": foreign_user_id},
            ).scalar_one()
    finally:
        engine.dispose()

    # api_client is logged in as its own user — it must NOT see the foreign thread.
    assert api_client.get(f"/api/ask/threads/{foreign_thread_id}").status_code == 404
    assert api_client.delete(f"/api/ask/threads/{foreign_thread_id}").status_code == 404


@pytest.mark.asyncio
async def test_run_semantic_search_excerpt_concatenates_passages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a hit carries multiple chunk_texts, the tool result excerpt joins
    them; a single-passage hit keeps a plain excerpt."""
    from types import SimpleNamespace
    from typing import cast

    from library.ask.engine import _run_semantic_search
    from library.search import SemanticHit

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    multi_doc = SimpleNamespace(
        id=11, title="Long dossier", sender=None, recipient=None, document_date=None
    )
    single_doc = SimpleNamespace(
        id=22, title="Invoice", sender=None, recipient=None, document_date=None
    )

    async def fake_search(
        session: Any, *, query: str, query_embedding: Any, top_k: int, chunks_per_doc: int = 1
    ) -> list[Any]:
        return [
            SemanticHit(
                document=cast(Any, multi_doc),
                score=0.9,
                chunk_index=1,
                chunk_text="first passage",
                page_number=None,
                chunk_texts=("first passage", "second passage", "third passage"),
            ),
            SemanticHit(
                document=cast(Any, single_doc),
                score=0.5,
                chunk_index=1,
                chunk_text="only passage",
                page_number=None,
                chunk_texts=("only passage",),
            ),
        ]

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    monkeypatch.setattr(ask_engine, "semantic_search", fake_search)

    cited: set[int] = set()
    pages: dict[int, int] = {}
    result = await _run_semantic_search(
        cast(Any, None), get_settings(), {"query": "energie"}, cited, pages
    )

    rows = {row["document_id"]: row for row in result["results"]}
    assert rows[11]["excerpt"] == "first passage\n\n[…]\n\nsecond passage\n\n[…]\n\nthird passage"
    assert rows[22]["excerpt"] == "only passage"
    assert cited == {11, 22}


# --- Conversation titles ----------------------------------------------------


async def test_generate_thread_title_cleans_and_costs() -> None:
    """A model title is stripped of quotes/trailing period and priced."""
    from typing import cast

    fake = _FakeAnthropic(
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text='  "Travel allowance policy."  ')],
                usage=_Usage(200, 8),
            )
        ]
    )
    result = await ask_engine.generate_thread_title(
        cast(Any, fake),
        model="claude-haiku-4-5",
        question="Do I have a travel allowance?",
        answer="Yes, 0.21 per km.",
    )
    assert result.title == "Travel allowance policy"
    assert result.cost_usd > 0


def test_new_thread_gets_generated_title(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new conversation is named by the title model, not the raw question."""

    async def _titler(
        client: Any,
        *,
        model: str,
        question: str,
        answer: str,
        settings: Any = None,
        backend: str = "api",
    ) -> Any:
        return ask_engine.TitleResult(title="Tax return locations", cost_usd=0.002)

    monkeypatch.setattr(ask_module, "generate_thread_title", _titler)
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="In the top drawer.")],
                usage=_Usage(8, 3),
            )
        ],
    )
    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})
    assert response.status_code == 200, response.text
    thread_id = response.json()["thread_id"]

    summary = next(t for t in api_client.get("/api/ask/threads").json() if t["id"] == thread_id)
    assert summary["title"] == "Tax return locations"
    # The title call's cost is folded into the turn cost the response reports.
    assert response.json()["cost_usd"] > 0


def test_title_failure_keeps_placeholder(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title-generation failure never breaks the answer or the thread."""

    async def _boom(client: Any, *, model: str, question: str, answer: str) -> Any:
        raise RuntimeError("title model unavailable")

    monkeypatch.setattr(ask_module, "generate_thread_title", _boom)
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="An answer.")],
                usage=_Usage(8, 3),
            )
        ],
    )
    response = api_client.post("/api/ask", json={"question": "A question about invoices?"})
    assert response.status_code == 200, response.text
    thread_id = response.json()["thread_id"]

    summary = next(t for t in api_client.get("/api/ask/threads").json() if t["id"] == thread_id)
    assert summary["title"] == "A question about invoices?"


def _create_thread(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, question: str) -> int:
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="Answer.")],
                usage=_Usage(8, 3),
            )
        ],
    )
    created = api_client.post("/api/ask", json={"question": question})
    assert created.status_code == 200, created.text
    return int(created.json()["thread_id"])


def test_rename_thread_updates_title(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _create_thread(api_client, monkeypatch, "original question?")

    response = api_client.patch(
        f"/api/ask/threads/{thread_id}", json={"title": "  My renamed chat  "}
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "My renamed chat"  # trimmed

    summary = next(t for t in api_client.get("/api/ask/threads").json() if t["id"] == thread_id)
    assert summary["title"] == "My renamed chat"


def test_rename_foreign_thread_is_404(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
) -> None:
    assert api_client.patch("/api/ask/threads/999999", json={"title": "nope"}).status_code == 404


def test_rename_rejects_blank_or_oversized_title(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _create_thread(api_client, monkeypatch, "q?")

    assert api_client.patch(f"/api/ask/threads/{thread_id}", json={"title": ""}).status_code == 422
    assert (
        api_client.patch(f"/api/ask/threads/{thread_id}", json={"title": "   "}).status_code == 422
    )
    assert (
        api_client.patch(f"/api/ask/threads/{thread_id}", json={"title": "x" * 121}).status_code
        == 422
    )


def test_ask_returns_503_when_the_subscription_cannot_authenticate(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credential problem is an operator problem, not a 500.

    Before this, an unauthenticated subscription backend raised out of run_ask
    unhandled and FastAPI answered "Internal Server Error", with the reason —
    which names the command to run — visible only in the container log.
    """
    from library.llm.subscription import SubscriptionBackendError

    async def fake_run_ask(*args: Any, **kwargs: Any) -> Any:
        raise SubscriptionBackendError(
            "the Claude subscription backend could not authenticate: no credentials. "
            "Run `CLAUDE_CONFIG_DIR=/app/.claude claude auth login --claudeai` on the host"
        )

    monkeypatch.setattr(ask_module, "run_ask", fake_run_ask)

    response = api_client.post("/api/ask", json={"question": "where is my gas bill"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "claude auth login --claudeai" in detail
    assert "could not authenticate" in detail


# --- archive context ---------------------------------------------------------
#
# The system prompt carries a block naming the user and the archive's vocabulary
# (kinds, matters, projects, tags, frequent senders). Without it the model has to
# guess slugs and cannot tell "my" bills from a housemate's.


def test_system_prompt_appends_archive_context() -> None:
    plain = ask_engine._system_prompt(date(2026, 6, 16))
    with_context = ask_engine._system_prompt(
        date(2026, 6, 16), archive_context='Archive context:\n- The user is "Ada Example".'
    )
    assert with_context.startswith(plain)
    assert with_context.endswith('- The user is "Ada Example".')
    assert ask_engine._system_prompt(date(2026, 6, 16), archive_context=None) == plain


@pytest.mark.asyncio
async def test_run_ask_sends_archive_context_in_the_cached_system_block() -> None:
    from typing import cast

    from library.ask.engine import run_ask

    client = _FakeAnthropic(
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="ok")], usage=_Usage(1, 1))]
    )
    await run_ask(
        cast(Any, None),
        question="anything?",
        settings=get_settings(),
        client=cast(Any, client),
        archive_context='- The user is "Ada Example".',
    )
    system = client.messages.calls[0]["system"]
    assert 'The user is "Ada Example"' in system[0]["text"]
    # Same block as the static prompt, so one breakpoint caches both.
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_ask_route_tells_the_model_who_the_user_is(
    api_client: TestClient,
    api_database_url: str,
    auth_user: Any,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET display_name = 'Ada Example' WHERE id = :id"),
                {"id": auth_user.id},
            )
    finally:
        engine.dispose()
    fake = _install_anthropic(
        monkeypatch,
        [_Response(stop_reason="end_turn", content=[_TextBlock(text="ok")], usage=_Usage(1, 1))],
    )

    response = api_client.post("/api/ask", json={"question": "what do I have?"})

    assert response.status_code == 200, response.text
    system_text = fake.messages.calls[0]["system"][0]["text"]
    assert 'The user is "Ada Example"' in system_text
    # The seeded kind vocabulary rides along so tool calls use real slugs.
    assert "utility-bill" in system_text


def test_ask_cites_nothing_when_the_loop_produces_no_answer(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cited` holds every candidate a read tool surfaced, including ones the
    model looked at and rejected. Attaching them to "I couldn't find an answer"
    presents rejected candidates as sources for a non-answer."""

    # Real rows, so a lingering fallback-to-`cited` bug would actually surface
    # them as citations rather than being masked by an empty `IN (...)` match.
    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            doc_ids = [
                connection.execute(
                    text(
                        "INSERT INTO documents (sha256, mime_type, status, source, ocr_text, title)"
                        " VALUES (:sha, 'application/pdf', 'indexed', 'upload', :ocr, :title)"
                        " RETURNING id"
                    ),
                    {
                        "sha": hashlib.sha256(f"no-answer-candidate-{index}".encode()).hexdigest(),
                        "ocr": "noise",
                        "title": title,
                    },
                ).scalar_one()
                for index, title in enumerate(["Unrelated", "Also unrelated"])
            ]
    finally:
        engine.dispose()

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    async def fake_search(session: Any, **kwargs: Any) -> list[Any]:
        from dataclasses import dataclass as _dataclass

        @_dataclass
        class _Doc:
            id: int
            title: str | None
            sender: Any = None
            recipient: Any = None
            document_date: Any = None

        @_dataclass
        class _Hit:
            document: Any
            score: float
            chunk_index: int | None
            chunk_text: str | None
            page_number: int | None
            chunk_texts: tuple[str, ...]

        return [
            _Hit(_Doc(id=doc_ids[0], title="Unrelated"), 0.1, 0, "noise", None, ("noise",)),
            _Hit(_Doc(id=doc_ids[1], title="Also unrelated"), 0.1, 0, "noise", None, ("noise",)),
        ]

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    monkeypatch.setattr(ask_engine, "semantic_search", fake_search)

    # Every response is a tool_use, so the loop exhausts ask_max_tool_turns
    # without ever producing text and falls back to the _NO_ANSWER sentinel.
    settings = get_settings()
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(name="semantic_search", input={"query": "tax"}, id=f"t{index}")
                ],
                usage=_Usage(100, 10),
            )
            for index in range(settings.ask_max_tool_turns)
        ],
    )

    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "I couldn't find an answer to that in the archive."
    assert body["citations"] == []


def test_ask_system_prompt_requires_disclosing_partial_coverage() -> None:
    """The coverage block is only worth computing if the model is obliged to
    act on it."""
    from library.ask.engine import ASK_SYSTEM_PROMPT_TEMPLATE

    assert "coverage" in ASK_SYSTEM_PROMPT_TEMPLATE
    assert "needs_review" in ASK_SYSTEM_PROMPT_TEMPLATE
    assert "excluded" in ASK_SYSTEM_PROMPT_TEMPLATE


def test_query_documents_tool_description_explains_coverage() -> None:
    from library.ask.engine import TOOLS

    tool = next(tool for tool in TOOLS if tool["name"] == "query_documents")
    assert "coverage" in tool["description"]
    assert "needs_review" in tool["description"]
    assert "excluded" in tool["description"]


def test_ask_system_prompt_pins_the_disclosure_obligation_as_a_MUST() -> None:
    """The other coverage/needs_review/excluded assertions are pure vocabulary
    containment checks: 'you MUST say so' could be reworded to 'you may
    mention it' and every one of them would still pass. The modal is the
    actual mechanism forcing disclosure, so pin its literal strength here too
    — a reword that softens it is a silent regression on this branch's whole
    point, not a wording tweak."""
    from library.ask.engine import ASK_SYSTEM_PROMPT_TEMPLATE

    assert "MUST say so" in ASK_SYSTEM_PROMPT_TEMPLATE
    assert "MUST also say so" in ASK_SYSTEM_PROMPT_TEMPLATE
