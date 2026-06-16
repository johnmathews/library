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


def _ask_logs_count(database_url: str) -> int:
    engine = create_engine(database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM ask_logs")).scalar_one()
    finally:
        engine.dispose()


# --- Tests ------------------------------------------------------------------


def test_system_prompt_includes_current_date() -> None:
    """The model must resolve "last year" against today, not its training cutoff."""
    prompt = ask_engine._system_prompt(date(2026, 6, 16))
    assert "2026-06-16" in prompt
    assert "The current year is 2026" in prompt
    assert "\"last year\" means 2025" in prompt


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
    assert _ask_logs_count(api_database_url) == 1


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
                text("INSERT INTO senders (name) VALUES ('Vattenfall') RETURNING id")
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
