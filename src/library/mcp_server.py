"""MCP server (W13): FastMCP 3.x tools over the Library archive.

Mounted into the FastAPI app at ``/mcp`` (Streamable HTTP, stateless,
JSON responses) by ``create_app`` in app.py, which also runs this app's
lifespan alongside the job-queue lifespan. See docs/mcp.md.

Auth
----
``LibraryTokenVerifier`` subclasses FastMCP's ``TokenVerifier`` and calls
the same ``validate_api_token`` service the REST API uses — one code path
for credential checks, including revocation and disabled accounts.
FastMCP wires the verifier into bearer middleware that 401s (with
``WWW-Authenticate: Bearer``) before any tool code runs; the token's user
id rides along in the access token claims for attribution.

Database sessions
-----------------
Tools and the verifier run outside FastAPI's dependency injection, so
they open a short-lived NullPool engine per call (same pattern as the
CLI): no loop-bound pooled connections, which matters because test
clients and production servers run the app in different event loops.
At family scale the per-call connection cost is irrelevant.
"""

import base64
import binascii
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import library
from library import projects, taxonomy
from library.auth.service import validate_api_token
from library.config import get_settings
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    IngestionEvent,
    Kind,
)
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.search import DocumentFilters, build_document_query
from library.storage import derived_path, path_for

OCR_TEXT_LIMIT: int = 20_000
MAX_FILE_BYTES: int = 10 * 1024 * 1024
SEARCH_LIMIT_MAX: int = 50

SERVER_INSTRUCTIONS = """\
Library is a self-hosted family document archive: scanned letters,
invoices, receipts, contracts, and similar documents in Dutch and
English, made searchable by OCR and metadata extraction.

Start with `search_documents` to find documents (full-text search with
Dutch and English stemming, plus metadata filters), then `get_document`
for the full text of one document. Use `list_kinds`, `list_senders`,
`list_tags`, and `list_projects` to discover valid filter values,
`library_stats` for an overview, `get_document_file` to retrieve the
actual file, and `ingest_document` to add a new document to the archive.
"""


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """One database session on a fresh NullPool engine (see module docstring)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


class LibraryTokenVerifier(TokenVerifier):
    """Validates Library API bearer tokens (``library_…``) against the database."""

    async def verify_token(self, token: str) -> AccessToken | None:
        async with _session() as session:
            user = await validate_api_token(session, token)
        if user is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"library-user-{user.id}",
            scopes=[],
            claims={"user_id": user.id, "username": user.username},
        )


mcp: FastMCP = FastMCP(
    name="Library",
    version=library.__version__,
    instructions=SERVER_INSTRUCTIONS,
    auth=LibraryTokenVerifier(),
)


def create_mcp_http_app() -> StarletteWithLifespan:
    """The Streamable-HTTP ASGI app, ready to mount at ``/mcp``.

    Stateless + JSON responses: no session affinity, no SSE streams —
    maximally compatible with reverse proxies and in-process test
    transports, and sufficient for request/response tools like ours.
    """
    return mcp.http_app(path="/", stateless_http=True, json_response=True)


# ------------------------------------------------------------------ helpers


def _current_user_id() -> int | None:
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    user_id = claims.get("user_id")
    return user_id if isinstance(user_id, int) else None


async def _get_document(session: AsyncSession, document_id: int) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise ToolError(f"document {document_id} not found")
    return document


def _document_summary(document: Document) -> dict[str, Any]:
    """The metadata fields shared by search results and document detail."""
    return {
        "id": document.id,
        "title": document.title,
        "summary": document.summary,
        "kind": (
            {"slug": document.kind.slug, "name": document.kind.name} if document.kind else None
        ),
        "sender": (
            {"id": document.sender.id, "name": document.sender.name} if document.sender else None
        ),
        "tags": [
            {"slug": tag.slug, "name": tag.name}
            for tag in sorted(document.tags, key=lambda tag: tag.slug)
        ],
        "projects": [
            {"slug": project.slug, "name": project.name}
            for project in sorted(document.projects, key=lambda project: project.slug)
        ],
        "document_date": document.document_date.isoformat() if document.document_date else None,
        "language": document.language.value,
        "status": document.status.value,
        "mime_type": document.mime_type,
        "page_count": document.page_count,
        "created_at": document.created_at.isoformat(),
    }


# -------------------------------------------------------------------- tools


@mcp.tool
async def search_documents(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Full-text search over title, summary, and OCR text, stemmed in "
                "both Dutch and English (searching 'rekening' finds 'rekeningen'). "
                "Websearch syntax: quoted phrases, OR, -exclusion. Omit to browse "
                "by filters only."
            )
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Field(description="Filter by document kind slug (see list_kinds), e.g. 'invoice'."),
    ] = None,
    sender: Annotated[
        str | None,
        Field(
            description=(
                "Filter by sender name, case-insensitive substring match "
                "('eneco' matches 'Eneco BV'). See list_senders."
            )
        ),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description="Filter by tag slug (see list_tags)."),
    ] = None,
    project: Annotated[
        str | None,
        Field(description="Filter by project slug (see list_projects)."),
    ] = None,
    language: Annotated[
        DocumentLanguage | None,
        Field(description="Filter by detected document language."),
    ] = None,
    date_from: Annotated[
        date | None,
        Field(description="Only documents dated on or after this ISO date (document_date)."),
    ] = None,
    date_to: Annotated[
        date | None,
        Field(description="Only documents dated on or before this ISO date (document_date)."),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=SEARCH_LIMIT_MAX, description="Maximum number of results."),
    ] = 10,
) -> dict[str, Any]:
    """Search and filter the document archive; the primary way to find documents.

    Returns the matching documents (id, title, summary, kind, sender, tags,
    document_date, language) plus the total match count. With `query`,
    results are ranked by relevance and each carries a `snippet` (matching
    OCR-text fragments, matches wrapped in <b>...</b>) and a `rank` score;
    without it, newest documents come first. All filters AND-combine.
    Follow up with get_document for the full text of a result.
    """
    document_query = build_document_query(
        query,
        DocumentFilters(
            kind_slug=kind,
            sender_contains=sender,
            tag_slugs=(tag,) if tag else (),
            project_slug=project,
            language=language,
            date_from=date_from,
            date_to=date_to,
        ),
    )
    async with _session() as session:
        total = (await session.execute(document_query.count)).scalar_one()
        result = await session.execute(document_query.statement.limit(limit))
        if document_query.has_rank:
            results = [
                {**_document_summary(document), "snippet": snippet, "rank": float(rank)}
                for document, rank, snippet in result.all()
            ]
        else:
            results = [
                {**_document_summary(document), "snippet": None, "rank": None}
                for document in result.scalars().all()
            ]
    return {"results": results, "total": total}


@mcp.tool
async def get_document(
    document_id: Annotated[int, Field(description="Document id, e.g. from search_documents.")],
) -> dict[str, Any]:
    """Fetch one document's full metadata, OCR text, and processing history.

    Returns everything search_documents shows plus financial fields
    (amount_total, currency, due_date, expiry_date), provenance (source,
    original_filename, sha256, ocr_confidence), the audit-trail events,
    and the full OCR text. OCR text longer than 20,000 characters is
    truncated with an explanatory note and `ocr_text_truncated: true`;
    fetch the file via get_document_file or the REST API if you need all
    of it. Errors if the document id is unknown or deleted.
    """
    async with _session() as session:
        document = await _get_document(session, document_id)
        ocr_text = document.ocr_text
        truncated = False
        if ocr_text is not None and len(ocr_text) > OCR_TEXT_LIMIT:
            note = (
                f"\n[... truncated: the full text is {len(ocr_text)} characters; "
                "use the REST API (GET /api/documents/{id}) for all of it]"
            )
            ocr_text = ocr_text[:OCR_TEXT_LIMIT] + note
            truncated = True
        return {
            **_document_summary(document),
            "source": document.source.value,
            "original_filename": document.original_filename,
            "sha256": document.sha256,
            "amount_total": (
                str(document.amount_total) if document.amount_total is not None else None
            ),
            "currency": document.currency,
            "due_date": document.due_date.isoformat() if document.due_date else None,
            "expiry_date": document.expiry_date.isoformat() if document.expiry_date else None,
            "ocr_confidence": document.ocr_confidence,
            "ocr_text": ocr_text,
            "ocr_text_truncated": truncated,
            "has_searchable_pdf": document.searchable_pdf,
            "events": [
                {"event": event.event, "created_at": event.created_at.isoformat()}
                for event in sorted(document.events, key=lambda event: event.id)
            ],
        }


@mcp.tool
async def get_document_file(
    document_id: Annotated[int, Field(description="Document id, e.g. from search_documents.")],
    variant: Annotated[
        Literal["original", "searchable_pdf"],
        Field(
            description=(
                "'original' (default) for the stored file as uploaded; "
                "'searchable_pdf' for the OCR-produced PDF with a text layer "
                "(only exists when the document's has_searchable_pdf is true)."
            )
        ),
    ] = "original",
) -> dict[str, Any]:
    """Retrieve a document's file as base64 (for reading or re-saving it).

    Returns filename, mime_type, size_bytes, and content_base64. Files
    larger than 10 MB are refused — download those over the REST API
    (GET /api/documents/{id}/original or /searchable.pdf) instead.
    """
    async with _session() as session:
        document = await _get_document(session, document_id)

    if variant == "original":
        path = path_for(document.sha256)
        mime = document.mime_type
        filename = document.original_filename or f"document-{document.id}"
    else:
        path = derived_path(document.sha256) / SEARCHABLE_PDF_NAME
        mime = "application/pdf"
        stem = (
            Path(document.original_filename).stem
            if document.original_filename
            else f"document-{document.id}"
        )
        filename = f"{stem}-searchable.pdf"

    if not path.is_file():
        raise ToolError(f"no {variant} file stored for document {document_id}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(
            f"file is {size} bytes, over the 10 MB MCP limit; download it via the "
            f"REST API instead (GET /api/documents/{document_id}/"
            f"{'original' if variant == 'original' else 'searchable.pdf'})"
        )
    return {
        "filename": filename,
        "mime_type": mime,
        "size_bytes": size,
        "content_base64": base64.b64encode(path.read_bytes()).decode(),
    }


@mcp.tool
async def ingest_document(
    filename: Annotated[
        str, Field(description="Original filename, including extension, e.g. 'invoice.pdf'.")
    ],
    content_base64: Annotated[
        str,
        Field(
            description=(
                "The file content, base64-encoded. Supported types: PDF, JPEG, "
                "PNG, HEIC/HEIF, TIFF, plain text."
            )
        ),
    ],
    source_note: Annotated[
        str | None,
        Field(description="Optional provenance note recorded on the document's audit trail."),
    ] = None,
) -> dict[str, Any]:
    """Add a new document to the archive (same pipeline as a web upload).

    The file is stored, deduplicated by content hash, and queued for OCR
    and metadata extraction; `source` is recorded as 'mcp' and the token's
    user as uploader. Returns {id, sha256, status, duplicate} — duplicate
    true means identical content already existed and no new document was
    created (the existing document's id is returned). Errors on invalid
    base64, unsupported file types, and content matching a deleted
    document.
    """
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(f"content_base64 is not valid base64: {exc}") from exc
    max_bytes = get_settings().max_upload_bytes
    if len(content) > max_bytes:
        raise ToolError(f"file is {len(content)} bytes, over the {max_bytes} byte upload limit")

    async with _session() as session:
        try:
            result = await ingest_file(
                session,
                content=content,
                filename=filename,
                source=DocumentSource.MCP,
                uploader_id=_current_user_id(),
            )
        except UnsupportedMimeTypeError as exc:
            raise ToolError(str(exc)) from exc
        except DeletedDuplicateError as exc:
            raise ToolError(
                f"{exc} — restore or purge it before re-ingesting this content"
            ) from exc
        if source_note and not result.duplicate:
            session.add(
                IngestionEvent(
                    document_id=result.document.id,
                    event="mcp_source_note",
                    detail={"note": source_note},
                )
            )
            await session.commit()
        return {
            "id": result.document.id,
            "sha256": result.document.sha256,
            "status": result.document.status.value,
            "duplicate": result.duplicate,
        }


@mcp.tool
async def list_kinds() -> dict[str, Any]:
    """List all document kinds (categories) with per-kind document counts.

    Use the returned slugs as the `kind` filter of search_documents.
    """
    async with _session() as session:
        kinds = await taxonomy.list_kinds(session)
    return {"kinds": [asdict(kind) for kind in kinds]}


@mcp.tool
async def list_senders() -> dict[str, Any]:
    """List all known senders (correspondents) with per-sender document counts.

    Use the returned names with the `sender` filter of search_documents
    (case-insensitive substring match).
    """
    async with _session() as session:
        senders = await taxonomy.list_senders(session)
    return {"senders": [asdict(sender) for sender in senders]}


@mcp.tool
async def list_tags() -> dict[str, Any]:
    """List all tags with per-tag document counts.

    Use the returned slugs as the `tag` filter of search_documents.
    """
    async with _session() as session:
        tags = await taxonomy.list_tags(session)
    return {"tags": [asdict(tag) for tag in tags]}


@mcp.tool
async def list_projects() -> dict[str, Any]:
    """List all projects (document collections) with per-project document counts.

    Use the returned slugs as the `project` filter of search_documents.
    Archived projects are omitted.
    """
    async with _session() as session:
        items = await projects.list_projects(session)
    return {"projects": [asdict(item) for item in items]}


@mcp.tool
async def library_stats() -> dict[str, Any]:
    """Overview statistics of the archive: sizes, processing state, date range.

    Returns total_documents, by_status (processing lifecycle counts:
    received/ocr/extract/indexed/failed), by_kind (counts per kind slug,
    'unclassified' for documents without a kind), ingested_last_7_days,
    and the oldest/newest document_date (ISO dates or null). Deleted
    documents are excluded everywhere.
    """
    week_ago = datetime.now(UTC) - timedelta(days=7)
    async with _session() as session:
        active = Document.deleted_at.is_(None)
        total = (
            await session.execute(select(func.count()).select_from(Document).where(active))
        ).scalar_one()
        by_status = (
            await session.execute(
                select(Document.status, func.count()).where(active).group_by(Document.status)
            )
        ).all()
        by_kind = (
            await session.execute(
                select(Kind.slug, func.count())
                .join_from(Document, Kind, Document.kind_id == Kind.id, isouter=True)
                .where(active)
                .group_by(Kind.slug)
            )
        ).all()
        recent = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(active, Document.created_at >= week_ago)
            )
        ).scalar_one()
        oldest, newest = (
            await session.execute(
                select(func.min(Document.document_date), func.max(Document.document_date)).where(
                    active
                )
            )
        ).one()
    return {
        "total_documents": total,
        "by_status": {status.value: count for status, count in by_status},
        "by_kind": {slug if slug else "unclassified": count for slug, count in by_kind},
        "ingested_last_7_days": recent,
        "oldest_document_date": oldest.isoformat() if oldest else None,
        "newest_document_date": newest.isoformat() if newest else None,
    }
