"""MCP ``create_note`` and ``get_document_file`` (W11, finding F14).

``create_note`` lets a model file text it wrote (a summary of a call, a
note about a policy) without a file round trip; it is the web app's note
path, so the note is readable and full-text searchable the moment the call
returns, and its uploader is whoever is connected.

``get_document_file`` returns MCP content blocks instead of a base64 string
in a dict: an ``image`` block for images a model can look at, an embedded
``resource`` for everything else, and a metadata part (filename, type,
size, ``download_url``). Files over 2 MB come back as metadata and a link,
with no bytes, so one call cannot flood a client's context.

As in tests/test_mcp.py, the app lifespan and the MCP session are entered
inside each test body (anyio cancel scopes must exit in the task that
entered them).
"""

import base64
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from procrastinate import PsycopgConnector

from library.config import get_settings
from library.jobs import job_app, procrastinate_conninfo
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.storage import derived_dir, store
from tests.conftest import AuthUser, _fetch_all, _insert_user
from tests.test_documents_api import _seed_document
from tests.test_mcp import MCP_URL, _asgi_client, _insert_token, error_text, payload

pytestmark = pytest.mark.integration

type Connector = Callable[[str], AbstractAsyncContextManager[ClientSession]]

#: The inline cap, restated rather than imported so a change to it is a
#: deliberate edit here too.
TWO_MB: int = 2 * 1024 * 1024
BASE_URL: str = "https://library.example.test"


# --- fixtures / helpers -------------------------------------------------------


@pytest.fixture
def connect(api_app: FastAPI, api_database_url: str) -> Connector:
    """Factory: the app's lifespan (real Procrastinate connector, so deferred
    jobs land in ``procrastinate_jobs``) plus an MCP session as ``token``."""

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
async def owner_token(api_database_url: str, auth_user: AuthUser) -> str:
    raw, _ = await _insert_token(api_database_url, auth_user.id, "mcp-files-owner")
    return raw


@pytest.fixture
async def spouse(api_database_url: str) -> tuple[int, str]:
    """A second household member with her own token: (user id, raw token).
    Created after ``auth_user`` when both are requested, so she is never the
    first user."""
    user_id = await _insert_user(
        api_database_url, f"mcp-files-spouse-{secrets.token_hex(4)}", "irrelevant"
    )
    raw, _ = await _insert_token(api_database_url, user_id, "mcp-files-spouse")
    return user_id, raw


@pytest.fixture
def public_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", f"{BASE_URL}/")
    get_settings.cache_clear()
    return BASE_URL


def _metadata(result: CallToolResult) -> dict[str, Any]:
    """The file result's metadata: the structured content, which the first
    content block repeats as JSON text for clients that only read text."""
    meta = payload(result)
    first = result.content[0]
    assert isinstance(first, TextContent)
    assert json.loads(first.text) == meta
    assert "content_base64" not in meta
    return meta


def _the_resource(result: CallToolResult) -> EmbeddedResource:
    (resource,) = [block for block in result.content if isinstance(block, EmbeddedResource)]
    return resource


# --- create_note --------------------------------------------------------------


async def test_create_note_materialises_page_and_pages_markdown(
    connect: Connector,
    api_database_url: str,
    auth_user: AuthUser,
    spouse: tuple[int, str],
    public_base_url: str,
) -> None:
    """The note is readable and full-text searchable as soon as the call
    returns (no worker run), by a word that is only in its body, and it is
    the caller's note, not the first user's."""
    spouse_id, spouse_token = spouse
    assert spouse_id != auth_user.id
    # A trashed document with the same coined word: search must not count it.
    await _seed_document(
        api_database_url,
        "mcp-note-trashed-twin",
        ocr_text="An old draft about the quarzelbrook arrangement.",
        deleted_at=datetime.now(UTC),
    )
    body = "# Garden plan\n\nAsk the neighbours about the quarzelbrook hedge.\n"
    async with connect(spouse_token) as session:
        created = payload(
            await session.call_tool("create_note", {"title": "Garden plan", "body_markdown": body})
        )
        found = payload(await session.call_tool("search_documents", {"query": "quarzelbrook"}))
        read = payload(await session.call_tool("get_document", {"document_id": created["id"]}))

    note_id = created["id"]
    assert created == {
        "id": note_id,
        "title": "Garden plan",
        "status": "received",
        "page_count": 1,
        "web_url": f"{BASE_URL}/documents/{note_id}",
    }
    assert [item["id"] for item in found["results"]] == [note_id]
    assert found["total"] == 1
    assert read["text"].strip() == body.strip()
    assert read["text_source"] == "pages"

    (row,) = await _fetch_all(
        api_database_url,
        "SELECT source, mime_type, uploader_id, pages_markdown, page_count, "
        "extra->'user_edited_fields' FROM documents WHERE id = :id",
        id=note_id,
    )
    assert row == ("note", "text/markdown", spouse_id, body.strip(), 1, ["title"])
    pages = await _fetch_all(
        api_database_url,
        "SELECT page_number, markdown FROM document_pages WHERE document_id = :id",
        id=note_id,
    )
    assert pages == [(1, body.strip())]
    events = await _fetch_all(
        api_database_url,
        "SELECT event, detail->>'source' FROM ingestion_events WHERE document_id = :id",
        id=note_id,
    )
    assert events == [("received", "note")]
    jobs = await _fetch_all(
        api_database_url,
        "SELECT task_name FROM procrastinate_jobs WHERE (args->>'document_id')::bigint = :id",
        id=note_id,
    )
    assert jobs == [("library.jobs.process_document",)]


async def test_create_note_twice_makes_two_notes(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    """Notes are never deduplicated, which is why the tool is not idempotent:
    the same call twice files two notes."""
    arguments = {"title": "Meter reading", "body_markdown": "Gas meter: 04512 on the first."}
    async with connect(owner_token) as session:
        first = payload(await session.call_tool("create_note", arguments))
        second = payload(await session.call_tool("create_note", arguments))
    assert first["id"] != second["id"]
    rows = await _fetch_all(
        api_database_url,
        "SELECT count(*) FROM documents WHERE source = 'note' AND title = 'Meter reading'",
    )
    assert rows == [(2,)]


async def test_create_note_rejects_blank_title_and_body(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    async with connect(owner_token) as session:
        blank_title = await session.call_tool(
            "create_note", {"title": "   ", "body_markdown": "some text"}
        )
        blank_body = await session.call_tool(
            "create_note", {"title": "A title", "body_markdown": " \n "}
        )
        long_title = await session.call_tool(
            "create_note", {"title": "x" * 201, "body_markdown": "some text"}
        )
    assert "at least 1 character" in error_text(blank_title)
    assert error_text(blank_body) == "body_markdown must not be blank"
    assert "at most 200 characters" in error_text(long_title)
    assert await _fetch_all(api_database_url, "SELECT id FROM documents") == []


# --- get_document_file ----------------------------------------------------------


async def test_get_document_file_roundtrip(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    """A PDF comes back as an embedded blob resource holding the exact bytes,
    plus the metadata part; the bytes are not repeated in the metadata."""
    content = b"%PDF-1.4 mcp file roundtrip " + bytes(range(256))
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    document_id = await _seed_document(
        api_database_url, "mcp-file-pdf", sha256=sha, original_filename="verzekering.pdf"
    )
    async with connect(owner_token) as session:
        result = await session.call_tool("get_document_file", {"document_id": document_id})

    assert [block.type for block in result.content] == ["text", "resource"]
    assert _metadata(result) == {
        "document_id": document_id,
        "variant": "original",
        "filename": "verzekering.pdf",
        "mime_type": "application/pdf",
        "size_bytes": len(content),
        "content_included": True,
        "download_url": None,
        "note": None,
    }
    resource = _the_resource(result).resource
    assert isinstance(resource, BlobResourceContents)
    assert base64.b64decode(resource.blob) == content
    assert resource.mimeType == "application/pdf"
    assert str(resource.uri) == f"library://documents/{document_id}/original/verzekering.pdf"


async def test_get_document_file_searchable_pdf_variant(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    content = b"original bytes for searchable variant test"
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    pdf_bytes = b"%PDF-1.4 searchable layer"
    (derived_dir(sha) / SEARCHABLE_PDF_NAME).write_bytes(pdf_bytes)
    document_id = await _seed_document(
        api_database_url,
        "mcp-file-searchable",
        sha256=sha,
        original_filename="scan.jpg",
        mime_type="image/jpeg",
        searchable_pdf=True,
    )
    async with connect(owner_token) as session:
        result = await session.call_tool(
            "get_document_file", {"document_id": document_id, "variant": "searchable_pdf"}
        )

    meta = _metadata(result)
    assert meta["variant"] == "searchable_pdf"
    assert meta["mime_type"] == "application/pdf"
    assert meta["filename"] == "scan-searchable.pdf"
    # The searchable PDF of an image scan is a PDF, so a resource, not an image.
    assert [block.type for block in result.content] == ["text", "resource"]
    resource = _the_resource(result).resource
    assert isinstance(resource, BlobResourceContents)
    assert base64.b64decode(resource.blob) == pdf_bytes
    assert str(resource.uri).endswith(f"/{document_id}/searchable_pdf/scan-searchable.pdf")


async def test_get_document_file_image_returns_image_content(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    """A PNG or JPEG comes back as an image block the model can look at. A
    TIFF or HEIC, which LLM clients do not render, comes back as a resource
    instead, so it cannot make a client reject the whole result."""
    png = b"\x89PNG\r\n\x1a\n mcp image block"
    tiff = b"II*\x00 mcp tiff resource"
    for content in (png, tiff):
        store(content)
    png_id = await _seed_document(
        api_database_url,
        "mcp-file-png",
        sha256=hashlib.sha256(png).hexdigest(),
        mime_type="image/png",
        original_filename="meterkast.png",
    )
    tiff_id = await _seed_document(
        api_database_url,
        "mcp-file-tiff",
        sha256=hashlib.sha256(tiff).hexdigest(),
        mime_type="image/tiff",
        original_filename="scan.tiff",
    )
    async with connect(owner_token) as session:
        png_result = await session.call_tool("get_document_file", {"document_id": png_id})
        tiff_result = await session.call_tool("get_document_file", {"document_id": tiff_id})

    assert [block.type for block in png_result.content] == ["text", "image"]
    image = png_result.content[1]
    assert isinstance(image, ImageContent)
    assert image.mimeType == "image/png"
    assert base64.b64decode(image.data) == png
    assert _metadata(png_result)["mime_type"] == "image/png"

    assert [block.type for block in tiff_result.content] == ["text", "resource"]
    tiff_resource = _the_resource(tiff_result).resource
    assert isinstance(tiff_resource, BlobResourceContents)
    assert tiff_resource.mimeType == "image/tiff"
    assert base64.b64decode(tiff_resource.blob) == tiff


async def test_get_document_file_text_is_a_text_resource(
    connect: Connector, owner_token: str
) -> None:
    """A Markdown or plain-text original (a note's, here) is a text resource,
    so the model reads the text instead of base64."""
    body = "# Boiler\n\nServiced on the 3rd; next visit in spring. Één keer per jaar.\n"
    async with connect(owner_token) as session:
        note = payload(
            await session.call_tool("create_note", {"title": "Boiler", "body_markdown": body})
        )
        result = await session.call_tool("get_document_file", {"document_id": note["id"]})

    meta = _metadata(result)
    assert meta["mime_type"] == "text/markdown"
    assert meta["filename"] == "Boiler.md"
    assert meta["size_bytes"] == len(body.encode())
    resource = _the_resource(result).resource
    assert isinstance(resource, TextResourceContents)
    assert resource.text == body


async def test_file_result_includes_download_url(
    connect: Connector,
    api_database_url: str,
    owner_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``download_url`` is the web app's own download link for the variant,
    on ``public_base_url``; ``null`` when that is not configured."""
    content = b"%PDF-1.4 download url test"
    sha = hashlib.sha256(content).hexdigest()
    store(content)
    (derived_dir(sha) / SEARCHABLE_PDF_NAME).write_bytes(b"%PDF-1.4 searchable download")
    document_id = await _seed_document(
        api_database_url, "mcp-file-url", sha256=sha, searchable_pdf=True
    )
    original = {"document_id": document_id}
    searchable = {"document_id": document_id, "variant": "searchable_pdf"}
    async with connect(owner_token) as session:
        without = _metadata(await session.call_tool("get_document_file", original))
        monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", f"{BASE_URL}/")
        get_settings.cache_clear()
        with_original = _metadata(await session.call_tool("get_document_file", original))
        with_searchable = _metadata(await session.call_tool("get_document_file", searchable))

    assert without["download_url"] is None
    assert with_original["download_url"] == f"{BASE_URL}/api/documents/{document_id}/original"
    assert (
        with_searchable["download_url"] == f"{BASE_URL}/api/documents/{document_id}/searchable.pdf"
    )


async def test_get_document_file_size_cap(
    connect: Connector, api_database_url: str, owner_token: str, public_base_url: str
) -> None:
    """Over 2 MB the result is the metadata and the link, with no bytes and
    no error; exactly 2 MB still comes inline."""
    over = b"%PDF-1.4 " + b"x" * (TWO_MB - 9 + 1)
    at_cap = b"%PDF-1.4 " + b"y" * (TWO_MB - 9)
    assert (len(over), len(at_cap)) == (TWO_MB + 1, TWO_MB)
    for content in (over, at_cap):
        store(content)
    over_id = await _seed_document(
        api_database_url, "mcp-file-over", sha256=hashlib.sha256(over).hexdigest()
    )
    at_cap_id = await _seed_document(
        api_database_url, "mcp-file-at-cap", sha256=hashlib.sha256(at_cap).hexdigest()
    )
    async with connect(owner_token) as session:
        over_result = await session.call_tool("get_document_file", {"document_id": over_id})
        at_cap_result = await session.call_tool("get_document_file", {"document_id": at_cap_id})

    assert not over_result.isError
    assert [block.type for block in over_result.content] == ["text"]
    meta = _metadata(over_result)
    assert meta["content_included"] is False
    assert meta["size_bytes"] == TWO_MB + 1
    assert meta["download_url"] == f"{BASE_URL}/api/documents/{over_id}/original"
    note = cast(str, meta["note"])
    assert "2 MB" in note and "download_url" in note
    assert "REST" not in note

    assert [block.type for block in at_cap_result.content] == ["text", "resource"]
    assert _metadata(at_cap_result)["content_included"] is True


async def test_get_document_file_over_cap_without_public_url_says_web_app(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    """With no link to give, the note sends the user to the web app."""
    over = b"%PDF-1.4 " + b"z" * TWO_MB
    store(over)
    document_id = await _seed_document(
        api_database_url, "mcp-file-over-nourl", sha256=hashlib.sha256(over).hexdigest()
    )
    async with connect(owner_token) as session:
        result = await session.call_tool("get_document_file", {"document_id": document_id})
    meta = _metadata(result)
    assert meta["download_url"] is None
    assert meta["content_included"] is False
    assert f"open document {document_id} in the Library web app" in cast(str, meta["note"])


async def test_get_document_file_missing_variant(
    connect: Connector, api_database_url: str, owner_token: str
) -> None:
    """A searchable PDF that was never produced, or an original that is gone
    from disk, is a tool error, not an empty result."""
    content = b"original bytes without a searchable variant"
    store(content)
    document_id = await _seed_document(
        api_database_url,
        "mcp-missing-variant",
        sha256=hashlib.sha256(content).hexdigest(),
        searchable_pdf=False,
    )
    # The row exists, its file was never stored.
    lost_id = await _seed_document(api_database_url, "mcp-file-lost-on-disk")
    async with connect(owner_token) as session:
        missing = await session.call_tool(
            "get_document_file", {"document_id": document_id, "variant": "searchable_pdf"}
        )
        lost = await session.call_tool("get_document_file", {"document_id": lost_id})
    assert error_text(missing) == f"no searchable_pdf file stored for document {document_id}"
    assert error_text(lost) == f"no original file stored for document {lost_id}"
