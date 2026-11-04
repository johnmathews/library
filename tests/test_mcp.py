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
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from procrastinate import PsycopgConnector
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library import db, taxonomy
from library.auth.service import API_TOKEN_PREFIX, sha256_hex
from library.config import get_settings
from library.jobs import job_app, procrastinate_conninfo
from library.mcp_server import SERVER_INSTRUCTIONS, mcp
from library.models import ApiToken, DocumentLanguage, DocumentStatus
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.storage import derived_dir, store
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
    "get_document",
    "get_document_file",
    "ingest_document",
    "list_kinds",
    "list_senders",
    "list_recipients",
    "list_tags",
    "list_projects",
    "list_matters",
    "library_stats",
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


#: The only tool that changes the archive. Everything else only reads.
WRITE_TOOLS: frozenset[str] = frozenset({"ingest_document"})


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
                "search_documents", {"query": "rekening", "tag": "mcp-search-nl"}
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
            await session.call_tool("search_documents", {"project": "mcp-search-project"})
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
            await session.call_tool("search_documents", {"matter": "mcp-search-matter"})
        )
        matters = payload(await session.call_tool("list_matters", {}))["matters"]
    assert [item["id"] for item in result["results"]] == [document_id]
    assert result["results"][0]["matters"] == [
        {"slug": "mcp-search-matter", "name": "mcp-search-matter"}
    ]
    matter = next(m for m in matters if m["slug"] == "mcp-search-matter")
    assert matter["document_count"] == 1


# --- get_document ------------------------------------------------------------


async def test_get_document_truncates_long_ocr_text(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    long_text = "rekening en water " * 2000  # 36k chars
    document_id = await _seed_document(
        api_database_url,
        "mcp-truncate",
        title="Lange tekst",
        ocr_text=long_text,
        topics=["water", "rekening"],
    )
    async with mcp_connect() as session:
        result = payload(await session.call_tool("get_document", {"document_id": document_id}))
    assert result["id"] == document_id
    assert result["topics"] == ["water", "rekening"]
    assert result["ocr_text_truncated"] is True
    assert len(result["ocr_text"]) < 21_000
    assert result["ocr_text"].startswith("rekening en water")
    assert f"truncated: the full text is {len(long_text)} characters" in result["ocr_text"]
    assert "REST" not in result["ocr_text"] and "/api/" not in result["ocr_text"]
    assert isinstance(result["events"], list)


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


# --- get_document_file -------------------------------------------------------


async def test_get_document_file_roundtrip(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    content = b"%PDF-1.4 mcp file roundtrip " + bytes(range(256))
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    document_id = await _seed_document(
        api_database_url,
        "ignored-marker",
        sha256=sha,
        original_filename="verzekering.pdf",
    )
    async with mcp_connect() as session:
        result = payload(await session.call_tool("get_document_file", {"document_id": document_id}))
    assert base64.b64decode(result["content_base64"]) == content
    assert result["mime_type"] == "application/pdf"
    assert result["filename"] == "verzekering.pdf"
    assert result["size_bytes"] == len(content)


async def test_get_document_file_searchable_pdf_variant(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    content = b"original bytes for searchable variant test"
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    pdf_bytes = b"%PDF-1.4 searchable layer"
    (derived_dir(sha) / SEARCHABLE_PDF_NAME).write_bytes(pdf_bytes)
    document_id = await _seed_document(
        api_database_url,
        "ignored-marker-2",
        sha256=sha,
        original_filename="scan.jpg",
        mime_type="image/jpeg",
        searchable_pdf=True,
    )
    async with mcp_connect() as session:
        result = payload(
            await session.call_tool(
                "get_document_file",
                {"document_id": document_id, "variant": "searchable_pdf"},
            )
        )
    assert base64.b64decode(result["content_base64"]) == pdf_bytes
    assert result["mime_type"] == "application/pdf"
    assert result["filename"] == "scan-searchable.pdf"


async def test_get_document_file_missing_variant(
    mcp_connect: McpConnector, api_database_url: str
) -> None:
    """Asking for a searchable PDF that was never produced is a tool error."""
    content = b"original bytes without a searchable variant"
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    document_id = await _seed_document(
        api_database_url, "mcp-missing-variant", sha256=sha, searchable_pdf=False
    )
    async with mcp_connect() as session:
        result = await session.call_tool(
            "get_document_file", {"document_id": document_id, "variant": "searchable_pdf"}
        )
    assert error_text(result) == f"no searchable_pdf file stored for document {document_id}"


async def test_get_document_file_size_cap(mcp_connect: McpConnector, api_database_url: str) -> None:
    content = b"x" * (10 * 1024 * 1024 + 1)
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    document_id = await _seed_document(api_database_url, "ignored-marker-3", sha256=sha)
    async with mcp_connect() as session:
        result = await session.call_tool("get_document_file", {"document_id": document_id})
    message = error_text(result)
    assert "10 MB" in message
    assert f"open document {document_id} in the Library web app" in message
    assert "REST" not in message and "/api/" not in message


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
