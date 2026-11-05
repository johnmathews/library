"""Integration tests for the MCP server (W13).

The official MCP client connects over Streamable HTTP straight through the
mounted ASGI app (httpx ASGITransport), so every test exercises the real
stack end to end: bearer-auth middleware, the FastMCP session manager, and
the tools against the test database. The app's lifespan (job queue +
FastMCP session manager) is entered explicitly because ASGITransport does
not run lifespans.

The whole stack (lifespan, HTTP client, MCP session) is entered inside
each test body via the ``running_app``/``mcp_connect`` factories rather
than yielded from async fixtures: both the lifespan and the MCP client
open anyio task groups, whose cancel scopes must be exited in the task
that entered them — pytest-asyncio runs fixture setup and teardown in
different tasks.
"""

import asyncio
import base64
import hashlib
import secrets
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from procrastinate import PsycopgConnector
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library import db, taxonomy
from library.auth.service import API_TOKEN_PREFIX, sha256_hex
from library.config import get_settings
from library.jobs import job_app, procrastinate_conninfo
from library.mcp_server import SERVER_INSTRUCTIONS, mcp
from library.models import (
    AmountKind,
    ApiToken,
    DocumentComment,
    DocumentLabel,
    DocumentLanguage,
    DocumentPage,
    DocumentStatus,
    Facet,
    FacetValue,
    ReviewStatus,
)
from library.structured_query import COVERAGE_GUIDE, SUM_AMOUNT_GUIDE
from tests.conftest import AuthUser, _fetch_all, _insert_user
from tests.test_documents_api import _seed_document

# Distinct from test_ingest_api's PDF_CONTENT: the API test database is
# shared across suites and ingest deduplicates by content hash.
MCP_PDF_CONTENT: bytes = (
    b"%PDF-1.4\n% mcp ingest test\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)
MCP_PDF_SHA: str = hashlib.sha256(MCP_PDF_CONTENT).hexdigest()

pytestmark = pytest.mark.integration

MCP_URL = "http://testserver/mcp/"

EXPECTED_TOOLS = {
    "search_documents",
    "semantic_search",
    "get_library_context",
    "get_document",
    "money_query",
    "get_document_file",
    "ingest_document",
    "list_kinds",
    "list_senders",
    "list_recipients",
    "list_tags",
    "list_projects",
    "list_matters",
    "library_stats",
    "update_document_metadata",
    "add_comment",
    "create_note",
}

# A minimal valid MCP initialize request, for raw-HTTP auth tests.
INITIALIZE_BODY: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

type AppRunner = Callable[[], AbstractAsyncContextManager[FastAPI]]
type McpConnector = Callable[..., AbstractAsyncContextManager[ClientSession]]


# --- helpers / fixtures ------------------------------------------------------


async def _insert_token(database_url: str, user_id: int, name: str) -> tuple[str, int]:
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            token = ApiToken(user_id=user_id, name=name, token_hash=sha256_hex(raw))
            session.add(token)
            await session.commit()
            return raw, token.id
    finally:
        await engine.dispose()


def create_token(database_url: str, user_id: int, name: str = "mcp-test") -> tuple[str, int]:
    """Insert an API token row; returns (raw secret, token id)."""
    return asyncio.run(_insert_token(database_url, user_id, name))


async def _revoke_token(database_url: str, token_id: int) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.execute(
                update(ApiToken).where(ApiToken.id == token_id).values(revoked_at=datetime.now(UTC))
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def running_app(api_app: FastAPI, api_database_url: str) -> AppRunner:
    """Factory: api_app with its lifespan entered and a real Procrastinate
    connector, as an in-test context manager (mirrors the api_client fixture,
    but async — the MCP client and the app must share one event loop)."""

    @asynccontextmanager
    async def run() -> AsyncIterator[FastAPI]:
        connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
        with job_app.replace_connector(connector):
            async with api_app.router.lifespan_context(api_app):
                yield api_app

    return run


@pytest.fixture
def mcp_token(api_database_url: str, auth_user: AuthUser) -> str:
    raw, _ = create_token(api_database_url, auth_user.id)
    return raw


def _asgi_client(
    app: FastAPI,
    token: str | None,
    *,
    raise_app_exceptions: bool = True,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://testserver",
        headers=headers,
        follow_redirects=follow_redirects,
        timeout=30,
    )


@pytest.fixture
def mcp_connect(running_app: AppRunner, mcp_token: str) -> McpConnector:
    """Factory: lifespan + initialized official-client MCP session, end to
    end through the mounted ASGI app. Pass a token to override the default
    valid one (e.g. for revocation tests)."""

    @asynccontextmanager
    async def connect(token: str | None = mcp_token) -> AsyncIterator[ClientSession]:
        async with (
            running_app() as app,
            _asgi_client(app, token) as http_client,
            streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    return connect


@asynccontextmanager
async def _db(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _add_pages(database_url: str, document_id: int, pages: list[str]) -> None:
    """Store ``pages`` as the document's markdown pages 1..n."""
    async with _db(database_url) as session:
        session.add_all(
            DocumentPage(
                document_id=document_id, page_number=number, markdown=text, char_count=len(text)
            )
            for number, text in enumerate(pages, start=1)
        )
        await session.commit()


async def _add_comment(
    database_url: str, document_id: int, author_id: int, body: str, created_at: datetime
) -> int:
    async with _db(database_url) as session:
        comment = DocumentComment(
            document_id=document_id, author_id=author_id, body=body, created_at=created_at
        )
        session.add(comment)
        await session.commit()
        return comment.id


async def _label(database_url: str, document_id: int, facet_key: str, value_key: str) -> None:
    """Give the document ``facet_key: value_key``, creating the facet and value."""
    async with _db(database_url) as session:
        facet = (
            await session.execute(select(Facet).where(Facet.key == facet_key))
        ).scalar_one_or_none()
        if facet is None:
            facet = Facet(key=facet_key, label=facet_key)
            session.add(facet)
            await session.flush()
        value = (
            await session.execute(
                select(FacetValue).where(
                    FacetValue.facet_id == facet.id, FacetValue.key == value_key
                )
            )
        ).scalar_one_or_none()
        if value is None:
            value = FacetValue(facet_id=facet.id, key=value_key, label=value_key)
            session.add(value)
            await session.flush()
        session.add(
            DocumentLabel(document_id=document_id, facet_id=facet.id, facet_value_id=value.id)
        )
        await session.commit()


def _ids(result: dict[str, Any]) -> list[int]:
    return [item["id"] for item in result["results"]]


def payload(result: CallToolResult) -> dict[str, Any]:
    """The structured result of a successful tool call."""
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def error_text(result: CallToolResult) -> str:
    assert result.isError, "expected a tool error"
    assert result.content and result.content[0].type == "text"
    return result.content[0].text


# --- tool listing ------------------------------------------------------------


async def test_list_tools(mcp_connect: McpConnector) -> None:
    async with mcp_connect() as session:
        tools = (await session.list_tools()).tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description.strip()) > 20, tool.name


#: The tools that change the archive. Everything else only reads.
WRITE_TOOLS: frozenset[str] = frozenset(
    {"ingest_document", "update_document_metadata", "add_comment", "create_note"}
)


async def test_tools_declare_annotations_and_titles(mcp_connect: McpConnector) -> None:
    """Every tool carries a title and behaviour hints in tools/list.

    ChatGPT treats a tool without ``readOnlyHint`` as a write and asks the
    user to confirm each call, so a missing hint turns every search into a
    prompt. ``openWorldHint=False``: no tool reaches outside the archive.
    """
    async with mcp_connect() as session:
        tools = (await session.list_tools()).tools

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.title, tool.name
        assert tool.annotations is not None, tool.name
        assert tool.annotations.openWorldHint is False, tool.name
        if tool.name in WRITE_TOOLS:
            assert tool.annotations.readOnlyHint is False, tool.name
        else:
            assert tool.annotations.readOnlyHint is True, tool.name

    ingest = next(tool for tool in tools if tool.name == "ingest_document")
    assert ingest.annotations is not None
    # Adds a document, never changes or removes one; the same bytes twice
    # return the existing document instead of a second copy.
    assert ingest.annotations.destructiveHint is False
    assert ingest.annotations.idempotentHint is True

    # Overwrites metadata (destructive), but applying the same change twice
    # leaves the same state (idempotent).
    edit = next(tool for tool in tools if tool.name == "update_document_metadata")
    assert edit.annotations is not None
    assert edit.annotations.destructiveHint is True
    assert edit.annotations.idempotentHint is True

    # Only adds; each call adds another comment.
    comment = next(tool for tool in tools if tool.name == "add_comment")
    assert comment.annotations is not None
    assert comment.annotations.destructiveHint is False
    assert comment.annotations.idempotentHint is False

    # Only adds; notes are never deduplicated, so each call files another one.
    note = next(tool for tool in tools if tool.name == "create_note")
    assert note.annotations is not None
    assert note.annotations.destructiveHint is False
    assert note.annotations.idempotentHint is False

    # Both publish an output schema; the file tool's describes its metadata
    # part, not the bytes (those travel as image/resource content blocks).
    schemas = {tool.name: tool.outputSchema for tool in tools}
    assert schemas["create_note"] is not None
    file_schema = schemas["get_document_file"]
    assert file_schema is not None
    assert "download_url" in file_schema["properties"]
    assert "content_base64" not in file_schema["properties"]


async def test_tool_text_never_points_at_the_rest_api(mcp_connect: McpConnector) -> None:
    """An LLM connector cannot call Library's REST API, so tool text must not
    send it there: no "REST" and no ``/api/`` paths in any description."""
    async with mcp_connect() as session:
        tools = (await session.list_tools()).tools

    for tool in tools:
        text = f"{tool.description} {tool.inputSchema}"
        assert "REST" not in text, tool.name
        assert "/api/" not in text, tool.name


async def test_instructions_mention_every_tool(mcp_connect: McpConnector) -> None:
    """Every registered tool is named in the instructions clients are handed.

    The server's own instructions are how an LLM client learns what it can
    do; a tool missing from them is effectively undiscoverable however well
    it is implemented (`list_matters` was, for a while). Assert against the
    live registry rather than ``EXPECTED_TOOLS`` so a newly added tool trips
    this too.
    """
    async with mcp_connect() as session:
        tools = (await session.list_tools()).tools

    # `mcp.instructions` is what the initialize response hands the client.
    assert mcp.instructions == SERVER_INSTRUCTIONS
    missing = sorted(t.name for t in tools if f"`{t.name}`" not in SERVER_INSTRUCTIONS)
    assert not missing, f"tools absent from SERVER_INSTRUCTIONS: {missing}"


# --- search ------------------------------------------------------------------


async def test_search_documents_dutch_stem(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "mcp-search-nl",
        kind_slug="invoice",
        sender_name="MCP Energie BV",
        recipient_name="MCP Klant Jan",
        tag_slugs=["mcp-search-nl"],
        topics=["betaling", "herinnering"],
        title="Energierekening mei",
        ocr_text="Uw rekeningen zijn nog niet betaald.",
        language=DocumentLanguage.NLD,
    )
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool(
                "search_documents", {"query": "rekening", "tags": ["mcp-search-nl"]}
            )
        )
    assert result["total"] == 1
    (item,) = result["results"]
    assert item["id"] == document_id
    assert item["title"] == "Energierekening mei"
    assert item["kind"] == {"slug": "invoice", "name": "Invoice"}
    assert item["sender"]["name"] == "MCP Energie BV"
    assert item["recipient"]["name"] == "MCP Klant Jan"
    assert isinstance(item["recipient"]["id"], int)
    assert item["tags"] == [{"slug": "mcp-search-nl", "name": "mcp-search-nl"}]
    assert item["topics"] == ["betaling", "herinnering"]
    assert item["language"] == "nld"
    assert "<b>rekeningen</b>" in item["snippet"]
    assert item["rank"] > 0


async def test_search_documents_sender_filter_without_query(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "mcp-search-sender",
        sender_name="MCP Waterbedrijf NV",
        tag_slugs=["mcp-search-sender"],
        title="Wateraansluiting",
    )
    async with mcp_connect() as session:
        result = payload(await session.call_tool("search_documents", {"sender": "waterbedrijf"}))
    assert [item["id"] for item in result["results"]] == [document_id]
    assert result["results"][0]["snippet"] is None
    assert result["results"][0]["rank"] is None


async def test_search_documents_recipient_filter_without_query(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "mcp-search-recipient",
        recipient_name="MCP Recipient Smith",
        tag_slugs=["mcp-search-recipient"],
        title="Addressed document",
    )
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool("search_documents", {"recipient": "recipient smith"})
        )
    assert [item["id"] for item in result["results"]] == [document_id]
    assert result["results"][0]["recipient"]["name"] == "MCP Recipient Smith"
    assert result["results"][0]["snippet"] is None
    assert result["results"][0]["rank"] is None


async def test_search_documents_project_filter(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "mcp-search-project",
        tag_slugs=["mcp-search-project"],
        project_slugs=["mcp-search-project"],
        title="Project document",
    )
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool("search_documents", {"projects": ["mcp-search-project"]})
        )
        projects = payload(await session.call_tool("list_projects", {}))["projects"]
    assert [item["id"] for item in result["results"]] == [document_id]
    assert result["results"][0]["projects"] == [
        {"slug": "mcp-search-project", "name": "mcp-search-project"}
    ]
    project = next(p for p in projects if p["slug"] == "mcp-search-project")
    assert project["document_count"] == 1


async def test_search_documents_matter_filter(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "mcp-search-matter",
        tag_slugs=["mcp-search-matter"],
        matter_slugs=["mcp-search-matter"],
        title="Matter document",
    )
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool("search_documents", {"matters": ["mcp-search-matter"]})
        )
        matters = payload(await session.call_tool("list_matters", {}))["matters"]
    assert [item["id"] for item in result["results"]] == [document_id]
    assert result["results"][0]["matters"] == [
        {"slug": "mcp-search-matter", "name": "mcp-search-matter"}
    ]
    matter = next(m for m in matters if m["slug"] == "mcp-search-matter")
    assert matter["document_count"] == 1


async def test_search_offset_pages_past_limit(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """``offset`` walks past ``limit``; ``next_offset`` says where the next page
    starts and is null on the last one. A deleted document with the same tag
    is never counted or returned."""
    tag = f"mcp-offset-{secrets.token_hex(4)}"
    dates = [date(2026, 2, 1), date(2025, 2, 28), date(2024, 2, 29), date(2023, 2, 28), None]
    expected = [
        await _seed_document(
            api_database_url, f"{tag}-{index}", tag_slugs=[tag], document_date=document_date
        )
        for index, document_date in enumerate(dates)
    ]
    await _seed_document(
        api_database_url,
        f"{tag}-deleted",
        tag_slugs=[tag],
        document_date=date(2026, 2, 28),
        deleted_at=datetime.now(UTC),
    )
    async with mcp_connect() as session:
        pages = [
            payload(
                await session.call_tool(
                    "search_documents", {"tags": [tag], "limit": 2, "offset": offset}
                )
            )
            for offset in (0, 2, 4)
        ]
    assert [_ids(page) for page in pages] == [expected[0:2], expected[2:4], expected[4:5]]
    assert [page["total"] for page in pages] == [5, 5, 5]
    assert [page["offset"] for page in pages] == [0, 2, 4]
    assert [page["next_offset"] for page in pages] == [2, 4, None]


async def test_search_multi_tag_and_facet_filters(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """Tags AND-compose, matters OR-compose, facets AND-compose, and status and
    review_status narrow further — the same builder as the web app's list."""
    prefix = f"mcp-multi-{secrets.token_hex(4)}"
    tag_a, tag_b = f"{prefix}-a", f"{prefix}-b"
    matter_1, matter_2 = f"{prefix}-m1", f"{prefix}-m2"
    facet = f"{prefix}-f"

    both = await _seed_document(
        api_database_url,
        f"{prefix}-both",
        tag_slugs=[tag_a, tag_b],
        matter_slugs=[matter_1],
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    only_a = await _seed_document(api_database_url, f"{prefix}-only-a", tag_slugs=[tag_a])
    other_value = await _seed_document(
        api_database_url, f"{prefix}-other-value", tag_slugs=[tag_a, tag_b]
    )
    failed = await _seed_document(
        api_database_url,
        f"{prefix}-failed",
        tag_slugs=[tag_a, tag_b],
        matter_slugs=[matter_2],
        status=DocumentStatus.FAILED,
    )
    deleted = await _seed_document(
        api_database_url,
        f"{prefix}-deleted",
        tag_slugs=[tag_a, tag_b],
        matter_slugs=[matter_1],
        deleted_at=datetime.now(UTC),
    )
    for document_id in (both, only_a, failed, deleted):
        await _label(api_database_url, document_id, facet, "v1")
    await _label(api_database_url, other_value, facet, "v2")

    async def search(session: ClientSession, **arguments: Any) -> set[int]:
        return set(_ids(payload(await session.call_tool("search_documents", arguments))))

    async with mcp_connect() as session:
        assert await search(session, tags=[tag_a, tag_b]) == {both, other_value, failed}
        assert await search(session, tags=[tag_a], facets={facet: "v1"}) == {both, only_a, failed}
        assert await search(session, tags=[tag_a, tag_b], facets={facet: "v1"}) == {both, failed}
        assert await search(
            session, tags=[tag_a, tag_b], facets={facet: "v1"}, status="indexed"
        ) == {both}
        assert await search(session, tags=[tag_a], status="failed") == {failed}
        assert await search(session, tags=[tag_a], review_status="needs_review") == {both}
        assert await search(session, matters=[matter_1, matter_2]) == {both, failed}
        assert await search(session, tags=[tag_a], facets={facet: "nope"}) == set()


async def test_search_projects_match_any(mcp_connect: McpConnector, api_database_url: str) -> None:
    """Projects OR-compose like matters: `projects=[a, b]` is the union, not
    the documents filed under both, and a deleted document stays out."""
    prefix = f"mcp-projects-{secrets.token_hex(4)}"
    project_a, project_b = f"{prefix}-a", f"{prefix}-b"
    in_a = await _seed_document(api_database_url, f"{prefix}-in-a", project_slugs=[project_a])
    in_b = await _seed_document(api_database_url, f"{prefix}-in-b", project_slugs=[project_b])
    in_both = await _seed_document(
        api_database_url, f"{prefix}-in-both", project_slugs=[project_a, project_b]
    )
    await _seed_document(
        api_database_url,
        f"{prefix}-deleted",
        project_slugs=[project_a],
        deleted_at=datetime.now(UTC),
    )
    async with mcp_connect() as session:
        union = payload(
            await session.call_tool("search_documents", {"projects": [project_a, project_b]})
        )
        only_b = payload(await session.call_tool("search_documents", {"projects": [project_b]}))
    assert set(_ids(union)) == {in_a, in_b, in_both}
    assert union["total"] == 3
    assert set(_ids(only_b)) == {in_b, in_both}


async def test_search_results_carry_amount_kind(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """A refund's amount_total is a magnitude; amount_kind is what says it is
    money coming back, so every result carries both."""
    tag = f"mcp-amount-kind-{secrets.token_hex(4)}"
    refund = await _seed_document(
        api_database_url,
        f"{tag}-refund",
        tag_slugs=[tag],
        amount_total=Decimal("12.50"),
        currency="EUR",
        amount_kind=AmountKind.REFUND,
        document_date=date(2025, 2, 28),
    )
    paid = await _seed_document(
        api_database_url,
        f"{tag}-paid",
        tag_slugs=[tag],
        amount_total=Decimal("12.50"),
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2025, 2, 1),
    )
    undecided = await _seed_document(
        api_database_url, f"{tag}-undecided", tag_slugs=[tag], document_date=date(2024, 2, 29)
    )
    async with mcp_connect() as session:
        result = payload(await session.call_tool("search_documents", {"tags": [tag]}))
    by_id = {item["id"]: item for item in result["results"]}
    assert set(by_id) == {refund, paid, undecided}
    assert by_id[refund]["amount_kind"] == "refund"
    assert by_id[paid]["amount_kind"] == "payment_made"
    assert by_id[undecided]["amount_kind"] is None
    assert by_id[refund]["amount_total"] == by_id[paid]["amount_total"] == "12.50"
    assert by_id[refund]["currency"] == "EUR"
    # The other fields the web app's list rows carry ride along too.
    assert by_id[refund]["review_status"] == "unreviewed"
    assert by_id[refund]["due_date"] is None


def _properties(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """``node``'s properties, following a local ``$ref`` if it is one."""
    if "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return node["properties"]


async def test_read_tools_publish_output_schemas(mcp_connect: McpConnector) -> None:
    """Typed returns: tools/list carries an outputSchema for every read tool
    that returns documents or context, naming the fields a client can rely on."""
    async with mcp_connect() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    document_schema = tools["get_document"].outputSchema
    assert document_schema is not None
    for field in (
        "text",
        "next_start_page",
        "continuation",
        "comments",
        "review_status",
        "amount_kind",
        "web_url",
    ):
        assert field in document_schema["properties"], field
    assert "ocr_text" not in document_schema["properties"]

    search_schema = tools["search_documents"].outputSchema
    assert search_schema is not None
    assert {"results", "total", "offset", "next_offset"} <= set(search_schema["properties"])
    item_properties = _properties(search_schema, search_schema["properties"]["results"]["items"])
    for field in ("amount_kind", "amount_total", "review_status", "snippet", "rank"):
        assert field in item_properties, field

    semantic_schema = tools["semantic_search"].outputSchema
    assert semantic_schema is not None
    assert {"results", "coverage"} <= set(semantic_schema["properties"])
    hit_properties = _properties(semantic_schema, semantic_schema["properties"]["results"]["items"])
    for field in ("passages", "page_number", "score", "amount_kind", "review_status", "tags"):
        assert field in hit_properties, field
    coverage_properties = _properties(semantic_schema, semantic_schema["properties"]["coverage"])
    assert set(coverage_properties) == {"matched", "returned", "unembedded"}

    context_schema = tools["get_library_context"].outputSchema
    assert context_schema is not None
    assert "context" in context_schema["properties"]

    money_schema = tools["money_query"].outputSchema
    assert money_schema is not None
    assert {"result_type", "rows", "coverage"} <= set(money_schema["properties"])
    money_coverage = _properties(money_schema, money_schema["properties"]["coverage"])
    assert set(money_coverage) == {"matched", "included", "excluded", "needs_review"}
    row_types = money_schema["properties"]["rows"]["items"]["anyOf"]
    row_fields = [set(_properties(money_schema, row)) for row in row_types]
    assert {"key", "total", "currency", "document_count", "document_ids"} in row_fields

    description = tools["get_document"].description or ""
    assert "Do not add up amount_total" in description
    assert "money_query" in description
    assert "money_query" in (tools["search_documents"].description or "")
    # One wording for sign, dedup and coverage, shared with Ask's tool.
    money_description = tools["money_query"].description or ""
    assert SUM_AMOUNT_GUIDE in money_description
    assert COVERAGE_GUIDE in money_description

    # The facet vocabulary lives in get_library_context, so the parameter
    # that needs it says where to find it.
    facets = tools["search_documents"].inputSchema["properties"]["facets"]
    assert "get_library_context" in facets["description"]


# --- get_document ------------------------------------------------------------


async def test_get_document_reads_page_markdown_not_ocr(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """A thin-OCR scan whose words exist only in its page markdown: search
    matches on them, so get_document must show them."""
    document_id = await _seed_document(
        api_database_url,
        "mcp-pages-not-ocr",
        title="Scanned policy",
        ocr_text="Polis",
        page_count=2,
    )
    await _add_pages(
        api_database_url,
        document_id,
        ["# Polis\n\nDekking: inboedel", "Eigen risico: mcpmarkdownonly"],
    )
    async with mcp_connect() as session:
        result = payload(await session.call_tool("get_document", {"document_id": document_id}))
    assert result["text_source"] == "pages"
    assert "mcpmarkdownonly" in result["text"]
    assert result["text"].index("Dekking: inboedel") < result["text"].index("mcpmarkdownonly")
    assert (result["text_start_page"], result["text_end_page"]) == (1, 2)
    assert result["text_page_count"] == 2
    assert result["text_truncated"] is False
    assert result["next_start_page"] is None
    assert result["continuation"] is None
    assert "ocr_text" not in result


async def test_get_document_truncates_long_ocr_text(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """Over ``max_chars`` (default 20 000) the text stops, and the hint names
    the page to continue at: the one that was cut, read whole next time.
    (The name predates page text; the text is now the page markdown.)"""
    pages = [f"page-{n}-start " + "x" * 9_000 for n in (1, 2, 3)]
    document_id = await _seed_document(
        api_database_url, "mcp-truncate", title="Lange tekst", topics=["water", "rekening"]
    )
    await _add_pages(api_database_url, document_id, pages)
    async with mcp_connect() as session:
        result = payload(await session.call_tool("get_document", {"document_id": document_id}))
    assert result["id"] == document_id
    assert result["topics"] == ["water", "rekening"]
    assert len(result["text"]) == 20_000
    assert result["text"].startswith("page-1-start")
    assert "page-3-start" in result["text"]
    assert result["text_truncated"] is True
    assert result["text_total_chars"] == len("\n\n".join(pages))
    assert (result["text_start_page"], result["text_end_page"]) == (1, 3)
    assert result["next_start_page"] == 3
    assert "start_page=3" in result["continuation"]
    assert "REST" not in result["continuation"] and "/api/" not in result["continuation"]
    assert isinstance(result["events"], list)


async def test_get_document_pages_through_long_document(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """Following ``next_start_page`` reaches every page, in order, and a page
    longer than ``max_chars`` says to ask for more."""
    sizes = [1_500, 2_500, 900, 6_000, 300, 3_800, 2_000]
    pages = [f"[p{n}] " + "y" * size for n, size in enumerate(sizes, start=1)]
    document_id = await _seed_document(api_database_url, "mcp-page-through")
    await _add_pages(api_database_url, document_id, pages)

    seen: list[str] = []
    calls: list[dict[str, Any]] = []
    start_page: int | None = 1
    async with mcp_connect() as session:
        while start_page is not None and len(calls) < 20:
            result = payload(
                await session.call_tool(
                    "get_document",
                    {"document_id": document_id, "start_page": start_page, "max_chars": 5_000},
                )
            )
            calls.append(result)
            assert len(result["text"]) <= 5_000
            assert result["text_start_page"] == start_page
            seen.extend(marker for n in range(1, 8) if (marker := f"[p{n}]") in result["text"])
            start_page = result["next_start_page"]
        # Page 4 alone is over max_chars: a larger max_chars returns it whole.
        whole = payload(
            await session.call_tool(
                "get_document",
                {"document_id": document_id, "start_page": 4, "max_chars": 6_100},
            )
        )

    # 1-3 + the start of 4 | 4 cut | 5, 6 + the start of 7 | 7.
    assert [call["text_start_page"] for call in calls] == [1, 4, 5, 7]
    assert seen == ["[p1]", "[p2]", "[p3]", "[p4]", "[p4]", "[p5]", "[p6]", "[p7]", "[p7]"]
    cut = calls[1]
    assert cut["text_end_page"] == 4
    assert cut["text_truncated"] is True
    assert "max_chars" in cut["continuation"]
    assert calls[-1]["text_truncated"] is False
    assert calls[-1]["text_end_page"] == 7
    assert calls[-1]["continuation"] is None
    assert whole["text"].startswith(pages[3])
    assert whole["text_end_page"] == 5


async def test_get_document_text_window_edges(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """A window that ends exactly at a page's end continues at the next
    page, and an over-long last page has no next page, only a bigger
    max_chars."""
    exact_id = await _seed_document(api_database_url, "mcp-window-exact")
    await _add_pages(api_database_url, exact_id, ["e" * 1_000, "[second]"])
    oversize_id = await _seed_document(api_database_url, "mcp-window-oversize")
    await _add_pages(api_database_url, oversize_id, ["short", "[last] " + "z" * 2_000])
    async with mcp_connect() as session:
        exact = payload(
            await session.call_tool("get_document", {"document_id": exact_id, "max_chars": 1_000})
        )
        oversize = payload(
            await session.call_tool(
                "get_document", {"document_id": oversize_id, "start_page": 2, "max_chars": 1_000}
            )
        )
    assert exact["text"] == "e" * 1_000
    assert (exact["text_end_page"], exact["next_start_page"]) == (1, 2)
    assert exact["continuation"] == (
        "The text stops after page 1 of 2. Call get_document again with start_page=2 to continue."
    )
    assert oversize["text"].startswith("[last] ")
    assert len(oversize["text"]) == 1_000
    assert oversize["text_truncated"] is True
    assert (oversize["text_end_page"], oversize["next_start_page"]) == (2, None)
    assert "larger max_chars" in oversize["continuation"]
    assert "next page" not in oversize["continuation"]


async def test_get_document_window_cut_at_separator_end(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """A window that ends exactly after the blank line between two pages holds
    page 1 whole and none of page 2, so page 1 is its last page and page 2 is
    next. (Page 2 starts at the window's length: it is not in the window.)"""
    document_id = await _seed_document(api_database_url, "mcp-window-separator")
    await _add_pages(api_database_url, document_id, ["s" * 998, "[second]"])
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool(
                "get_document", {"document_id": document_id, "max_chars": 1_000}
            )
        )
    assert result["text"] == "s" * 998 + "\n\n"
    assert (result["text_end_page"], result["next_start_page"]) == (1, 2)
    assert result["text_truncated"] is True
    assert result["continuation"] == (
        "The text stops after page 1 of 2. Call get_document again with start_page=2 to continue."
    )


async def test_get_document_trailing_whitespace_is_not_more_text(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """Only blank text after the window (an empty last page, trailing spaces
    in OCR text) is not more to read: no continuation, so the model does not
    spend a call fetching nothing."""
    paged_id = await _seed_document(api_database_url, "mcp-window-blank-tail")
    await _add_pages(api_database_url, paged_id, ["b" * 1_000, " \n "])
    ocr_id = await _seed_document(
        api_database_url, "mcp-ocr-blank-tail", ocr_text="o" * 1_000 + "  \n\n  "
    )
    async with mcp_connect() as session:
        paged = payload(
            await session.call_tool("get_document", {"document_id": paged_id, "max_chars": 1_000})
        )
        ocr = payload(
            await session.call_tool("get_document", {"document_id": ocr_id, "max_chars": 1_000})
        )
    assert paged["text"] == "b" * 1_000
    assert paged["text_truncated"] is False
    assert paged["next_start_page"] is None
    assert paged["continuation"] is None
    assert ocr["text"] == "o" * 1_000
    assert ocr["text_truncated"] is False
    assert ocr["continuation"] is None


async def test_get_document_falls_back_to_ocr_text_without_pages(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """No markdown pages yet: the OCR text, capped the same way, with a hint
    that only a larger max_chars or the file gives more."""
    long_text = "rekening en water " * 2000  # 36k chars
    document_id = await _seed_document(
        api_database_url, "mcp-ocr-fallback", title="Lange tekst", ocr_text=long_text
    )
    empty_id = await _seed_document(api_database_url, "mcp-no-text")
    async with mcp_connect() as session:
        capped = payload(await session.call_tool("get_document", {"document_id": document_id}))
        whole = payload(
            await session.call_tool(
                "get_document", {"document_id": document_id, "max_chars": 50_000}
            )
        )
        paged = await session.call_tool(
            "get_document", {"document_id": document_id, "start_page": 2}
        )
        empty = payload(await session.call_tool("get_document", {"document_id": empty_id}))
    assert capped["text_source"] == "ocr_text"
    assert capped["text"] == long_text[:20_000]
    assert capped["text_truncated"] is True
    assert capped["text_total_chars"] == len(long_text)
    assert capped["next_start_page"] is None
    assert capped["text_start_page"] is None
    assert "max_chars" in capped["continuation"]
    assert "get_document_file" in capped["continuation"]
    assert whole["text"] == long_text
    assert whole["text_truncated"] is False and whole["continuation"] is None
    assert "no page text" in error_text(paged)
    assert empty["text_source"] == "none"
    assert empty["text"] == ""
    assert empty["text_truncated"] is False


async def test_get_document_start_page_past_the_end_errors(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    document_id = await _seed_document(api_database_url, "mcp-start-past-end")
    await _add_pages(api_database_url, document_id, ["one", "two"])
    async with mcp_connect() as session:
        result = await session.call_tool(
            "get_document", {"document_id": document_id, "start_page": 3}
        )
    assert error_text(result) == (
        f"document {document_id} has 2 pages of text; start_page must be at most 2"
    )


async def test_get_document_includes_comments_and_review_status(
    mcp_connect: McpConnector,
    api_database_url: str,
    auth_user: AuthUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comments from both household members (newest first), whether the
    extracted details are trusted, what the amount means, and a link to the
    document in the web app once the public URL is configured."""
    other_user = await _insert_user(
        api_database_url, f"mcp-other-{secrets.token_hex(4)}", "irrelevant"
    )
    document_id = await _seed_document(
        api_database_url,
        "mcp-comments-review",
        title="Refund letter",
        amount_total=Decimal("12.50"),
        currency="EUR",
        amount_kind=AmountKind.REFUND,
        document_date=date(2024, 2, 29),
        review_status=ReviewStatus.NEEDS_REVIEW,
        extra={
            "validation": {
                "findings": [
                    {
                        "rule": "amount_currency_coupling",
                        "field": "currency",
                        "message": "amount without a currency",
                    }
                ]
            }
        },
    )
    older = await _add_comment(
        api_database_url,
        document_id,
        auth_user.id,
        "paid back into the joint account",
        datetime(2024, 2, 29, 9, 0, tzinfo=UTC),
    )
    newer = await _add_comment(
        api_database_url,
        document_id,
        other_user,
        "the second refund is still due",
        datetime(2024, 3, 1, 9, 0, tzinfo=UTC),
    )
    async with mcp_connect() as session:
        without_url = payload(await session.call_tool("get_document", {"document_id": document_id}))
        monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", "https://library.example.test/")
        get_settings.cache_clear()
        with_url = payload(await session.call_tool("get_document", {"document_id": document_id}))

    assert [c["id"] for c in without_url["comments"]] == [newer, older]
    assert [c["author_id"] for c in without_url["comments"]] == [other_user, auth_user.id]
    assert without_url["comments"][1]["body"] == "paid back into the joint account"
    assert without_url["review_status"] == "needs_review"
    assert without_url["review_findings"] == [
        {
            "rule": "amount_currency_coupling",
            "field": "currency",
            "message": "amount without a currency",
        }
    ]
    assert without_url["amount_kind"] == "refund"
    assert without_url["amount_total"] == "12.50"
    assert without_url["document_date"] == "2024-02-29"
    assert without_url["web_url"] is None
    assert with_url["web_url"] == f"https://library.example.test/documents/{document_id}"


async def test_get_document_unknown_id_errors(mcp_connect: McpConnector) -> None:
    async with mcp_connect() as session:
        result = await session.call_tool("get_document", {"document_id": 999_999_999})
    assert "not found" in error_text(result)


async def test_get_document_soft_deleted_errors(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """A document in the trash is as invisible to MCP as an unknown id."""
    document_id = await _seed_document(
        api_database_url, "mcp-soft-deleted", deleted_at=datetime.now(UTC)
    )
    async with mcp_connect() as session:
        result = await session.call_tool("get_document", {"document_id": document_id})
        file_result = await session.call_tool("get_document_file", {"document_id": document_id})
    assert error_text(result) == f"document {document_id} not found"
    assert error_text(file_result) == f"document {document_id} not found"


@pytest.mark.parametrize(
    ("tool", "document_id", "bound"),
    [
        ("get_document", 2**63, "less than or equal to 9223372036854775807"),
        ("get_document", 0, "greater than or equal to 1"),
        ("get_document_file", 2**63, "less than or equal to 9223372036854775807"),
    ],
)
async def test_out_of_range_id_is_validation_error(
    mcp_connect: McpConnector, tool: str, document_id: int, bound: str
) -> None:
    """An id Postgres cannot hold is refused by argument validation, before
    any SQL runs, so the error names the bound, not the query."""
    async with mcp_connect() as session:
        result = await session.call_tool(tool, {"document_id": document_id})
    message = error_text(result)
    assert bound in message
    assert "SQL" not in message and "asyncpg" not in message


async def test_unexpected_error_is_masked(
    mcp_connect: McpConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug or database failure inside a tool reaches the client as a generic
    error, never as the exception text (which can carry SQL and schema)."""

    async def broken(session: AsyncSession) -> list[Any]:
        raise RuntimeError("SELECT secret_column FROM internal_table")

    monkeypatch.setattr(taxonomy, "list_kinds", broken)
    async with mcp_connect() as session:
        result = await session.call_tool("list_kinds", {})
    message = error_text(result)
    assert message == "Error calling tool 'list_kinds'"
    assert "secret_column" not in message


# --- ingest_document ---------------------------------------------------------


async def test_ingest_document_creates_row_and_defers_job(
    mcp_connect: McpConnector, api_database_url: str, auth_user: AuthUser
) -> None:
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool(
                "ingest_document",
                {
                    "filename": "mcp-upload.pdf",
                    "content_base64": base64.b64encode(MCP_PDF_CONTENT).decode(),
                    "source_note": "found in claude conversation",
                },
            )
        )
        assert result["sha256"] == MCP_PDF_SHA
        assert result["duplicate"] is False
        document_id = result["id"]

        # Same bytes again: duplicate, no new row.
        again = payload(
            await session.call_tool(
                "ingest_document",
                {
                    "filename": "again.pdf",
                    "content_base64": base64.b64encode(MCP_PDF_CONTENT).decode(),
                },
            )
        )
    assert again["duplicate"] is True
    assert again["id"] == document_id

    rows = await _fetch_all(
        api_database_url,
        "SELECT source, original_filename, uploader_id FROM documents WHERE id = :id",
        id=document_id,
    )
    assert rows == [("mcp", "mcp-upload.pdf", auth_user.id)]

    jobs = await _fetch_all(
        api_database_url,
        "SELECT task_name FROM procrastinate_jobs WHERE (args->>'document_id')::bigint = :id",
        id=document_id,
    )
    assert jobs == [("library.jobs.process_document",)]

    events = await _fetch_all(
        api_database_url,
        "SELECT event, detail->>'note' FROM ingestion_events WHERE document_id = :id",
        id=document_id,
    )
    assert ("received", None) in events
    assert ("mcp_source_note", "found in claude conversation") in events


async def test_ingest_document_rejects_bad_base64(mcp_connect: McpConnector) -> None:
    async with mcp_connect() as session:
        result = await session.call_tool(
            "ingest_document", {"filename": "x.pdf", "content_base64": "not base64!!!"}
        )
    assert "base64" in error_text(result)


async def test_ingest_over_upload_limit(
    mcp_connect: McpConnector, api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decoded size is checked against LIBRARY_MAX_UPLOAD_BYTES before
    anything is stored."""
    monkeypatch.setenv("LIBRARY_MAX_UPLOAD_BYTES", "64")
    get_settings.cache_clear()
    content = b"%PDF-1.4 " + b"x" * 100
    async with mcp_connect() as session:
        result = await session.call_tool(
            "ingest_document",
            {"filename": "big.pdf", "content_base64": base64.b64encode(content).decode()},
        )
    assert error_text(result) == f"file is {len(content)} bytes, over the 64 byte upload limit"
    rows = await _fetch_all(
        api_database_url,
        "SELECT id FROM documents WHERE sha256 = :sha",
        sha=hashlib.sha256(content).hexdigest(),
    )
    assert rows == []


async def test_ingest_deleted_duplicate(mcp_connect: McpConnector, api_database_url: str) -> None:
    """Content matching a document in the trash is refused (sha256 is unique),
    and the error says how to get past it without pointing at an API."""
    content = b"%PDF-1.4\n% mcp deleted duplicate\n%%EOF\n"
    deleted_id = await _seed_document(
        api_database_url,
        "mcp-deleted-duplicate",
        sha256=hashlib.sha256(content).hexdigest(),
        deleted_at=datetime.now(UTC),
    )
    async with mcp_connect() as session:
        result = await session.call_tool(
            "ingest_document",
            {"filename": "again.pdf", "content_base64": base64.b64encode(content).decode()},
        )
    message = error_text(result)
    assert f"content matches soft-deleted document {deleted_id}" in message
    assert "restore it from the trash" in message
    assert "REST" not in message and "/api/" not in message


async def test_ingest_document_rejects_unsupported_type(mcp_connect: McpConnector) -> None:
    async with mcp_connect() as session:
        result = await session.call_tool(
            "ingest_document",
            {
                "filename": "archive.zip",
                "content_base64": base64.b64encode(b"PK\x03\x04 not a document").decode(),
            },
        )
    assert "unsupported" in error_text(result)


# --- taxonomy + stats --------------------------------------------------------


async def test_list_kinds_senders_tags(mcp_connect: McpConnector, api_database_url: str) -> None:
    await _seed_document(
        api_database_url,
        "mcp-taxonomy",
        kind_slug="invoice",
        sender_name="MCP Taxonomie BV",
        recipient_name="MCP Taxonomie Ontvanger",
        tag_slugs=["mcp-taxonomy-tag"],
    )
    async with mcp_connect() as session:
        kinds = payload(await session.call_tool("list_kinds", {}))["kinds"]
        senders = payload(await session.call_tool("list_senders", {}))["senders"]
        recipients = payload(await session.call_tool("list_recipients", {}))["recipients"]
        tags = payload(await session.call_tool("list_tags", {}))["tags"]

    invoice = next(kind for kind in kinds if kind["slug"] == "invoice")
    assert invoice["name"] == "Invoice"
    assert invoice["document_count"] >= 1

    taxonomie = next(s for s in senders if s["name"] == "MCP Taxonomie BV")
    assert taxonomie["document_count"] == 1
    assert isinstance(taxonomie["id"], int)

    ontvanger = next(r for r in recipients if r["name"] == "MCP Taxonomie Ontvanger")
    assert ontvanger["document_count"] == 1
    assert isinstance(ontvanger["id"], int)

    tag = next(t for t in tags if t["slug"] == "mcp-taxonomy-tag")
    assert tag["document_count"] == 1


async def test_library_stats(mcp_connect: McpConnector, api_database_url: str) -> None:
    """Exact counts: the API database is truncated after every test, so the
    two seeded rows are the whole archive.

    The soft-deleted document differs from the live one on every reported
    axis (status, kind, an extreme date, and it was created just now), so
    leaking it into any single figure changes that figure.
    """
    await _seed_document(
        api_database_url,
        "mcp-stats",
        kind_slug="receipt",
        document_date=date(2001, 2, 3),
    )
    await _seed_document(
        api_database_url,
        "mcp-stats-deleted",
        kind_slug="invoice",
        status=DocumentStatus.FAILED,
        document_date=date(1901, 2, 28),
        deleted_at=datetime.now(UTC),
    )
    async with mcp_connect() as session:
        stats = payload(await session.call_tool("library_stats", {}))
    assert stats == {
        "total_documents": 1,
        "by_status": {"indexed": 1},
        "by_kind": {"receipt": 1},
        "ingested_last_7_days": 1,
        "oldest_document_date": "2001-02-03",
        "newest_document_date": "2001-02-03",
    }


# --- auth --------------------------------------------------------------------


async def test_no_token_is_rejected(running_app: AppRunner) -> None:
    async with running_app() as app, _asgi_client(app, token=None) as client:
        response = await client.post("/mcp/", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert response.status_code == 401
    assert "Bearer" in response.headers["WWW-Authenticate"]


async def test_garbage_token_is_rejected(running_app: AppRunner) -> None:
    async with running_app() as app, _asgi_client(app, "library_not-a-real-token") as client:
        response = await client.post("/mcp/", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert response.status_code == 401


async def test_revoked_token_is_rejected(
    running_app: AppRunner,
    mcp_connect: McpConnector,
    api_database_url: str,
    auth_user: AuthUser,
) -> None:
    raw, token_id = await _insert_token(api_database_url, auth_user.id, "to-revoke")

    # Works before revocation...
    async with mcp_connect(raw) as session:
        assert (await session.list_tools()).tools

    await _revoke_token(api_database_url, token_id)

    # ...and is dead immediately after.
    async with running_app() as app, _asgi_client(app, raw) as client:
        response = await client.post("/mcp/", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert response.status_code == 401


async def test_disabled_user_rejected(running_app: AppRunner, api_database_url: str) -> None:
    """An unrevoked token whose user is disabled is dead (``library user
    disable`` must cut MCP access without hunting down each token)."""
    disabled_id = await _insert_user(
        api_database_url, f"disabled-{secrets.token_hex(6)}", "irrelevant", is_active=False
    )
    raw, _ = await _insert_token(api_database_url, disabled_id, "disabled-user")
    async with running_app() as app, _asgi_client(app, raw) as client:
        response = await client.post("/mcp/", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert response.status_code == 401


async def test_slashless_post_mcp_works(running_app: AppRunner, mcp_token: str) -> None:
    """``/mcp``, the URL users paste, answers exactly like ``/mcp/``.

    Redirects are not followed: a 307 is not good enough, because several MCP
    clients do not re-send a POST body across a redirect.
    """
    async with (
        running_app() as app,
        _asgi_client(app, mcp_token, follow_redirects=False) as client,
    ):
        initialize = await client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
        call = await client.post(
            "/mcp", json=_tool_call_body(2, "library_stats", {}), headers=MCP_HEADERS
        )
        # Every other method lands on the MCP app too, not on the router.
        others = {
            method: (
                (await client.request(method, "/mcp", headers=MCP_HEADERS)).status_code,
                (await client.request(method, "/mcp/", headers=MCP_HEADERS)).status_code,
            )
            for method in ("GET", "DELETE")
        }
    assert initialize.status_code == 200, initialize.text
    assert initialize.json()["result"]["serverInfo"]["name"] == "Library"
    assert call.status_code == 200, call.text
    assert call.json()["result"]["isError"] is False
    for method, (slashless, slashed) in others.items():
        assert slashless == slashed, method


async def test_slashless_post_mcp_without_token_401(running_app: AppRunner) -> None:
    async with (
        running_app() as app,
        _asgi_client(app, token=None, follow_redirects=False) as client,
    ):
        response = await client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    assert response.status_code == 401
    assert "Bearer" in response.headers["WWW-Authenticate"]


async def test_valid_token_works(mcp_connect: McpConnector) -> None:
    async with mcp_connect() as session:
        stats = payload(await session.call_tool("library_stats", {}))
    assert "total_documents" in stats


# --- connection handling -----------------------------------------------------

#: More concurrent tool calls than the test Postgres accepts connections
#: (``max_connections`` = 100). Each call needs a connection for the token
#: check and another for the tool, so a connection-per-call design fails here
#: while a bounded pool queues.
PARALLEL_CALLS: int = 150


def _tool_call_body(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


async def test_parallel_calls_share_bounded_pool(running_app: AppRunner, mcp_token: str) -> None:
    """A client fanning out tool calls queues on the shared pool, never 500s.

    Raw stateless POSTs rather than the SDK client, so all calls are truly in
    flight at once. On the old engine-per-call code a third of them died with
    ``TooManyConnectionsError`` surfacing as a bare HTTP 500.
    """
    # A server answers an unhandled exception with a 500; count those rather
    # than letting the first one abort the gather.
    async with (
        running_app() as app,
        _asgi_client(app, mcp_token, raise_app_exceptions=False) as client,
    ):
        responses = await asyncio.gather(
            *(
                client.post(
                    "/mcp/",
                    json=_tool_call_body(index, "library_stats", {}),
                    headers=MCP_HEADERS,
                )
                for index in range(PARALLEL_CALLS)
            )
        )
    outcomes = Counter(
        f"{response.status_code}:isError={response.json()['result']['isError']}"
        if response.status_code == 200
        else str(response.status_code)
        for response in responses
    )
    assert outcomes == {"200:isError=False": PARALLEL_CALLS}


async def test_lifespan_disposes_shared_engine(mcp_connect: McpConnector) -> None:
    """The shared engine does not outlive the app run that built it.

    asyncpg connections belong to the event loop that opened them, and every
    test (like every TestClient) runs the app in a loop of its own. If the
    cached engine survived the lifespan, the next run would be handed pooled
    connections from a loop that no longer exists.
    """
    async with mcp_connect() as session:
        payload(await session.call_tool("library_stats", {}))
        assert db.get_engine.cache_info().currsize == 1
    assert db.get_engine.cache_info().currsize == 0
    assert db.get_sessionmaker.cache_info().currsize == 0
