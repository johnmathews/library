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


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


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


def _install_anthropic(monkeypatch: pytest.MonkeyPatch, responses: list[_Response]) -> None:
    monkeypatch.setattr(ask_module, "AsyncAnthropic", lambda api_key: _FakeAnthropic(responses))


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
            connection.execute(
                text(
                    "INSERT INTO documents"
                    " (sha256, mime_type, status, source, sender_id, kind_id, document_date)"
                    " VALUES (:sha, 'application/pdf', 'indexed', 'upload', :sid, :kid, :d)"
                ),
                {
                    "sha": hashlib.sha256(b"energy-2025").hexdigest(),
                    "sid": sender_id,
                    "kid": kind_id,
                    "d": date(2025, 3, 1),
                },
            )
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
    assert len(body["citations"]) == 1


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
        session: Any, *, query: str, query_embedding: Any, top_k: int
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
    ):
        captured["history"] = history_messages
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
