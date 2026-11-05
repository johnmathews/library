"""MCP ``semantic_search`` and ``get_library_context`` (W9).

Kept out of tests/test_mcp.py, which covers the keyword read tools, auth and
connection handling; these tests share its helpers. ``embed_query`` is
patched on ``library.mcp_server`` (the tool embeds the query itself, like
Ask), so no embedding service is needed. Every query embeds to ``e0`` and
every seeded chunk is a ``graded_vector`` at a known distance from it.

Scoping: the test database is shared across suites, and the vector leg
would otherwise rank every embedded document in it, so every call carries a
unique tag. That also exercises the filtered retrieval path, which is the
one MCP callers hit whenever they name anything.

As in tests/test_mcp.py, the app lifespan and the MCP session are entered
inside each test body (anyio cancel scopes must exit in the task that
entered them).
"""

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from procrastinate import PsycopgConnector
from sqlalchemy import update

from library import mcp_server
from library.embedding import EmbeddingError
from library.jobs import job_app, procrastinate_conninfo
from library.models import EMBEDDING_DIM, DocumentChunk, Recipient, User
from tests.conftest import AuthUser, _insert_user
from tests.test_documents_api import _seed_document
from tests.test_mcp import MCP_URL, _asgi_client, _db, _insert_token, _label, error_text, payload
from tests.test_semantic_search import graded_vector

pytestmark = pytest.mark.integration

type Connector = Callable[[str], AbstractAsyncContextManager[ClientSession]]


def _e0() -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    return vector


@pytest.fixture
def connect(api_app: FastAPI, api_database_url: str) -> Connector:
    """Factory: the app's lifespan plus an MCP session authenticated with ``token``."""

    @asynccontextmanager
    async def run(token: str) -> AsyncIterator[ClientSession]:
        connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
        with job_app.replace_connector(connector):
            async with (
                api_app.router.lifespan_context(api_app),
                _asgi_client(api_app, token) as http_client,
                streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session

    return run


@pytest.fixture
async def token(api_database_url: str, auth_user: AuthUser) -> str:
    raw, _ = await _insert_token(api_database_url, auth_user.id, "mcp-semantic")
    return raw


@pytest.fixture
def embeds_to_e0(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every query embeds to ``e0``; returns the queries the tool embedded."""
    queries: list[str] = []

    async def fake_embed_query(text: str, *, settings: Any, client: Any = None) -> list[float]:
        queries.append(text)
        return _e0()

    monkeypatch.setattr(mcp_server, "embed_query", fake_embed_query)
    return queries


async def _add_chunks(
    database_url: str, document_id: int, chunks: list[tuple[str, list[float], int | None]]
) -> None:
    """Store ``(text, embedding, page_number)`` chunks for the document."""
    async with _db(database_url) as session:
        session.add_all(
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                text=text,
                embedding=embedding,
                page_number=page_number,
            )
            for index, (text, embedding, page_number) in enumerate(chunks)
        )
        await session.commit()


def _ids(result: dict[str, Any]) -> list[int]:
    return [item["id"] for item in result["results"]]


# --- semantic_search ---------------------------------------------------------


async def test_semantic_search_reports_coverage(
    connect: Connector, token: str, api_database_url: str, embeds_to_e0: list[str]
) -> None:
    """An embedded match comes back with its passages and page; an unembedded
    document that only full-text search can reach comes back without
    passages and is counted in ``coverage.unembedded``; a soft-deleted
    document nearer the query than either is neither returned nor counted."""
    tag = f"mcp-semantic-cov-{secrets.token_hex(4)}"
    embedded = await _seed_document(
        api_database_url, f"{tag}-embedded", tag_slugs=[tag], ocr_text="warranty card"
    )
    await _add_chunks(
        api_database_url,
        embedded,
        [
            ("the warranty lasts two years", graded_vector(0.9), 2),
            ("unrelated small print", graded_vector(0.2), 3),
        ],
    )
    unembedded = await _seed_document(
        api_database_url, f"{tag}-unembedded", tag_slugs=[tag], ocr_text="warranty extension"
    )
    deleted = await _seed_document(
        api_database_url,
        f"{tag}-deleted",
        tag_slugs=[tag],
        ocr_text="warranty void",
        deleted_at=datetime.now(UTC),
    )
    await _add_chunks(api_database_url, deleted, [("warranty void", graded_vector(0.99), 1)])

    async with connect(token) as session:
        result = payload(
            await session.call_tool("semantic_search", {"query": "warranty", "tags": [tag]})
        )

    assert embeds_to_e0 == ["warranty"]
    # The embedded document is ranked by both retrievers, the other by one.
    assert _ids(result) == [embedded, unembedded]
    first, second = result["results"]
    assert first["passages"] == ["the warranty lasts two years", "unrelated small print"]
    assert first["page_number"] == 2
    assert first["score"] > second["score"] > 0
    assert second["passages"] == []
    assert second["page_number"] is None
    assert result["coverage"] == {"matched": 2, "returned": 2, "unembedded": 1}
    # The rows are search_documents' rows: the same list fields ride along.
    assert first["tags"] == [{"slug": tag, "name": tag}]
    assert first["review_status"] == "unreviewed"
    assert "amount_kind" in first


async def test_semantic_search_limit_caps_results_not_coverage(
    connect: Connector, token: str, api_database_url: str, embeds_to_e0: list[str]
) -> None:
    """``limit`` cuts the ranking (nearest first) while ``coverage.matched``
    still counts every document the filters let through."""
    tag = f"mcp-semantic-limit-{secrets.token_hex(4)}"
    by_similarity = {}
    for similarity in (0.3, 0.9, 0.6):
        document_id = await _seed_document(
            api_database_url, f"{tag}-{similarity}", tag_slugs=[tag], ocr_text="nothing"
        )
        await _add_chunks(
            api_database_url, document_id, [(f"passage {similarity}", graded_vector(similarity), 1)]
        )
        by_similarity[similarity] = document_id

    async with connect(token) as session:
        result = payload(
            await session.call_tool("semantic_search", {"query": "zzzq", "tags": [tag], "limit": 2})
        )
    assert _ids(result) == [by_similarity[0.9], by_similarity[0.6]]
    assert result["coverage"] == {"matched": 3, "returned": 2, "unembedded": 0}


async def test_semantic_search_output_fits_a_client_budget(
    connect: Connector, token: str, api_database_url: str, embeds_to_e0: list[str]
) -> None:
    """At the largest ``limit``, with every document carrying three full-size
    passages, the passages together stay inside ``SEMANTIC_PASSAGE_BUDGET``
    (MCP clients cap a tool result; Claude Code's default is 25k tokens).
    Every result keeps the start of its best passage, and the cut is flagged.
    ``limit`` above ``SEMANTIC_LIMIT_MAX`` is refused."""
    tag = f"mcp-semantic-budget-{secrets.token_hex(4)}"
    limit = mcp_server.SEMANTIC_LIMIT_MAX
    passage = "x" * 1800
    for index in range(limit):
        document_id = await _seed_document(
            api_database_url, f"{tag}-{index}", tag_slugs=[tag], ocr_text="nothing"
        )
        similarity = 0.9 - index * 0.01
        await _add_chunks(
            api_database_url,
            document_id,
            [(f"best {index} {passage}", graded_vector(similarity), 1)]
            + [(passage, graded_vector(similarity - 0.001 * n), 1) for n in (1, 2)],
        )

    async with connect(token) as session:
        result = payload(
            await session.call_tool(
                "semantic_search", {"query": "zzzq", "tags": [tag], "limit": limit}
            )
        )
        too_many = await session.call_tool(
            "semantic_search", {"query": "zzzq", "tags": [tag], "limit": limit + 1}
        )

    assert len(result["results"]) == limit
    total = sum(len(text) for hit in result["results"] for text in hit["passages"])
    assert total <= mcp_server.SEMANTIC_PASSAGE_BUDGET
    for index, hit in enumerate(result["results"]):
        assert hit["passages"][0].startswith(f"best {index} ")
        assert hit["passages_truncated"] is True
    assert too_many.isError


async def test_semantic_search_short_passages_are_not_cut(
    connect: Connector, token: str, api_database_url: str, embeds_to_e0: list[str]
) -> None:
    """Passages that fit the budget come back whole and unflagged."""
    tag = f"mcp-semantic-whole-{secrets.token_hex(4)}"
    document_id = await _seed_document(
        api_database_url, f"{tag}-doc", tag_slugs=[tag], ocr_text="nothing"
    )
    await _add_chunks(
        api_database_url,
        document_id,
        [("first passage", graded_vector(0.9), 1), ("second passage", graded_vector(0.8), 1)],
    )
    async with connect(token) as session:
        result = payload(
            await session.call_tool("semantic_search", {"query": "zzzq", "tags": [tag]})
        )
    (hit,) = result["results"]
    assert hit["passages"] == ["first passage", "second passage"]
    assert hit["passages_truncated"] is False


async def test_semantic_search_filters_reach_the_retriever(
    connect: Connector, token: str, api_database_url: str, embeds_to_e0: list[str]
) -> None:
    """Each filter narrows both the hits and ``coverage.matched``, with the
    same meaning as in search_documents. A deleted copy of the first
    document matches every filter and must never appear."""
    prefix = f"mcp-semantic-filter-{secrets.token_hex(4)}"
    facet = f"{prefix}-f"

    async def seed(marker: str, **fields: Any) -> int:
        document_id = await _seed_document(api_database_url, f"{prefix}-{marker}", **fields)
        await _add_chunks(api_database_url, document_id, [(marker, graded_vector(0.5), 1)])
        return document_id

    first_fields: dict[str, Any] = {
        "kind_slug": "invoice",
        "sender_name": f"{prefix} Energy BV",
        "recipient_name": f"{prefix} Anna",
        "tag_slugs": [prefix, f"{prefix}-x"],
        "project_slugs": [f"{prefix}-p1"],
        "matter_slugs": [f"{prefix}-m1"],
        "document_date": date(2025, 2, 28),
    }
    first = await seed("first", **first_fields)
    second = await seed(
        "second",
        kind_slug="receipt",
        sender_name=f"{prefix} Water",
        recipient_name=f"{prefix} Ben",
        tag_slugs=[prefix],
        project_slugs=[f"{prefix}-p2"],
        matter_slugs=[f"{prefix}-m2"],
        document_date=date(2024, 2, 29),
    )
    bare = await seed("bare", tag_slugs=[prefix])
    deleted = await seed("deleted", deleted_at=datetime.now(UTC), **first_fields)
    await _label(api_database_url, first, facet, "v1")
    await _label(api_database_url, second, facet, "v2")
    await _label(api_database_url, deleted, facet, "v1")

    async def search(session: ClientSession, **arguments: Any) -> set[int]:
        result = payload(
            await session.call_tool(
                "semantic_search", {"query": "zzzq", "tags": [prefix], **arguments}
            )
        )
        ids = set(_ids(result))
        assert result["coverage"]["matched"] == len(ids), arguments
        return ids

    async with connect(token) as session:
        assert await search(session) == {first, second, bare}
        assert await search(session, kind="invoice") == {first}
        assert await search(session, sender=f"{prefix} energy") == {first}
        assert await search(session, recipient=f"{prefix} ben") == {second}
        assert await search(session, tags=[prefix, f"{prefix}-x"]) == {first}
        assert await search(session, projects=[f"{prefix}-p1", f"{prefix}-p2"]) == {first, second}
        assert await search(session, matters=[f"{prefix}-m2"]) == {second}
        assert await search(session, facets={facet: "v1"}) == {first}
        assert await search(session, date_from="2025-01-01") == {first}
        assert await search(session, date_to="2024-12-31") == {second}


async def test_semantic_search_embedding_down_is_tool_error(
    connect: Connector, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embedding service being down is a tool error the model can act on
    (use keyword search), not the masked generic error."""

    async def down(text: str, *, settings: Any, client: Any = None) -> list[float]:
        raise EmbeddingError("embedding request failed: connection refused")

    monkeypatch.setattr(mcp_server, "embed_query", down)
    async with connect(token) as session:
        message = error_text(await session.call_tool("semantic_search", {"query": "warranty"}))
    assert "search_documents" in message
    assert "connection refused" not in message


async def test_semantic_search_blank_query_is_refused(
    connect: Connector, token: str, embeds_to_e0: list[str]
) -> None:
    """A query of only whitespace is an argument error, before anything is
    embedded (it would otherwise rank the whole archive by an empty query)."""
    async with connect(token) as session:
        result = await session.call_tool("semantic_search", {"query": "   "})
    assert result.isError
    assert embeds_to_e0 == []


# --- get_library_context -----------------------------------------------------


async def _set_user(database_url: str, user_id: int, display_name: str, profile: str) -> None:
    async with _db(database_url) as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(display_name=display_name, preferences={"ask_profile": profile})
        )
        await session.commit()


async def _own_recipient(database_url: str, user_id: int, name: str) -> None:
    async with _db(database_url) as session:
        session.add(Recipient(name=name, user_id=user_id))
        await session.commit()


async def test_library_context_is_per_caller(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Two household members get the same vocabulary but each their own
    identity line, own recipient names and own notes, never the other's."""
    suffix = secrets.token_hex(4)
    spouse_id = await _insert_user(api_database_url, f"mcp-context-spouse-{suffix}", "irrelevant")
    spouse_token, _ = await _insert_token(api_database_url, spouse_id, "mcp-context-spouse")
    await _set_user(api_database_url, auth_user.id, f"Owner {suffix}", f"owner-note-{suffix}")
    await _set_user(api_database_url, spouse_id, f"Spouse {suffix}", f"spouse-note-{suffix}")
    await _own_recipient(api_database_url, auth_user.id, f"O. Owner {suffix}")
    await _own_recipient(api_database_url, spouse_id, f"S. Spouse {suffix}")
    facet = f"mcp-context-f-{suffix}"
    labelled = await _seed_document(api_database_url, f"mcp-context-{suffix}")
    await _label(api_database_url, labelled, facet, "v1")

    async with connect(token) as session:
        owner = payload(await session.call_tool("get_library_context", {}))["context"]
    async with connect(spouse_token) as session:
        spouse = payload(await session.call_tool("get_library_context", {}))["context"]

    assert f'The user is "Owner {suffix}".' in owner
    assert f'"O. Owner {suffix}"' in owner
    assert f"owner-note-{suffix}" in owner
    assert f'The user is "Spouse {suffix}".' in spouse
    assert f'"S. Spouse {suffix}"' in spouse
    assert f"spouse-note-{suffix}" in spouse
    for name in (f"Spouse {suffix}", f"spouse-note-{suffix}"):
        assert name not in owner
    for name in (f"Owner {suffix}", f"owner-note-{suffix}"):
        assert name not in spouse
    # The facet vocabulary search_documents' `facets` needs is in both.
    assert f"{facet} (v1)" in owner
    assert f"{facet} (v1)" in spouse


async def test_library_context_without_a_user_is_tool_error(
    connect: Connector, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token whose user cannot be resolved gets a tool error, not someone
    else's context."""
    monkeypatch.setattr(mcp_server, "_current_user_id", lambda: None)
    async with connect(token) as session:
        message = error_text(await session.call_tool("get_library_context", {}))
    assert "user" in message
