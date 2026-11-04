"""MCP server (W13): FastMCP 3.x tools over the Library archive.

Mounted into the FastAPI app at ``/mcp`` (Streamable HTTP, stateless,
JSON responses) by ``create_app`` in app.py, which also runs this app's
lifespan alongside the job-queue lifespan. See docs/mcp.md.

Auth
----
``build_mcp_auth`` picks the auth for one app build from the settings:

- ``LibraryTokenVerifier`` always: it subclasses FastMCP's ``TokenVerifier``
  and calls the same ``validate_api_token`` service the REST API uses — one
  code path for static ``library_…`` tokens, including revocation and
  disabled accounts.
- When ``public_base_url`` is set (and is HTTPS, or loopback HTTP), Library is
  also an OAuth 2.1 authorization server (``LibraryOAuthProvider``), and the
  two are combined in ``LibraryMultiAuth``: claude.ai, ChatGPT and Claude
  Code discover it from the 401's ``resource_metadata``, register, send the
  user to the consent page and come back with ``libmcp_at_…`` tokens. The
  OAuth endpoints live inside the mount (``/mcp/authorize`` …); the RFC 9728
  and RFC 8414 discovery documents are mounted at the site root by app.py.

FastMCP wires the result into bearer middleware that 401s before any tool
code runs. Either kind of token carries the user's id in its claims
(``auth`` says which kind), which is what ``_current_user_id`` reads for
attribution. The auth object is passed to ``create_mcp_http_app`` rather
than set on the module-level ``mcp`` server, so app builds with different
settings (tests) never share it.

Database sessions
-----------------
Tools and the verifier run outside FastAPI's dependency injection, so
they take sessions straight from the process-wide pool
(``library.db.get_sessionmaker``) that REST requests use too. The pool is
bounded and queues when busy, so a client fanning out many parallel tool
calls waits its turn instead of exhausting Postgres connections. asyncpg
connections are bound to an event loop; ``create_app``'s lifespan disposes
the engine on the way out (``db.dispose_engine``), so each app run (and
each test client's loop) starts with a fresh pool.

Errors
------
``ToolError`` messages are written for the model and reach it verbatim.
Anything else a tool raises is masked (``mask_error_details=True``) to a
generic "Error calling tool …", so database errors never leak SQL or
schema to a client. Error text never points at Library's REST API: an
LLM connector cannot call it.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan, create_streamable_http_app
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.routes import build_metadata, build_resource_metadata_url, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import ProtectedResourceMetadata
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

import library
from library import comments_service, matters, projects, taxonomy
from library.auth.oauth_provider import LibraryOAuthProvider
from library.auth.service import validate_api_token
from library.config import Settings, get_settings
from library.db import get_sessionmaker
from library.documents_service import (
    DocumentEditError,
    edit_document,
    preview_current,
    preview_update,
)
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    IngestionEvent,
    Kind,
)
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.schemas import CommentOut, CurrencyCode, DocumentUpdate
from library.search import DocumentFilters, build_document_query
from library.spend_lines import AllocationError
from library.storage import derived_path, path_for

logger = logging.getLogger(__name__)

OCR_TEXT_LIMIT: int = 20_000
MAX_FILE_BYTES: int = 10 * 1024 * 1024
SEARCH_LIMIT_MAX: int = 50
#: The largest value a Postgres BIGINT (``documents.id``) holds. Bounding the
#: argument makes an out-of-range id a validation error, not a SQL error.
MAX_DOCUMENT_ID: int = 2**63 - 1

#: The single OAuth scope: full access to the archive as the calling user.
OAUTH_SCOPE: str = "library"

#: Hints for every tool that only reads the archive. ``openWorldHint=False``:
#: no tool reaches anything outside Library. ChatGPT asks the user to confirm
#: any tool that does not declare ``readOnlyHint``.
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

DocumentId = Annotated[
    int,
    Field(ge=1, le=MAX_DOCUMENT_ID, description="Document id, e.g. from search_documents."),
]

SERVER_INSTRUCTIONS = """\
Library is a self-hosted family document archive: scanned letters,
invoices, receipts, contracts, and similar documents in Dutch and
English, made searchable by OCR and metadata extraction.

Start with `search_documents` to find documents (full-text search with
Dutch and English stemming, plus metadata filters), then `get_document`
for the full text of one document. Use `list_kinds`, `list_senders`,
`list_recipients`, `list_tags`, `list_projects`, and `list_matters` to
discover valid filter values,
`library_stats` for an overview, `get_document_file` to retrieve the
actual file, and `ingest_document` to add a new document to the archive.

To correct a document's details (title, kind, sender, dates, amount,
tags, ...) use `update_document_metadata` in two steps: call it without
`apply` to get a preview of the changes, show the user that preview,
and only after they agree call it again with `apply=true` and the
`preview_token` the preview returned. `add_comment` attaches a dated
note to a document (for example "this is our current house"); it is
recorded as the connected user and becomes searchable. Everything these
tools change is recorded as the connected user.
"""


def _session() -> AsyncSession:
    """One database session from the shared pool (see module docstring)."""
    return get_sessionmaker()()


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
            # A static token is full account access, so it holds the one scope
            # there is. Nothing requires a scope (``required_scopes`` stays
            # empty), so this describes the token and never gates it.
            scopes=[OAUTH_SCOPE],
            claims={"user_id": user.id, "username": user.username, "auth": "api_token"},
        )


# --------------------------------------------------------------------- OAuth

#: Where the SPA's consent page lives, relative to ``public_base_url``.
CONSENT_PATH: str = "/oauth/consent"

#: Client authentication the token and revocation endpoints accept. ``none``
#: is a public client (PKCE only); registration accepts it and the SDK's
#: client authenticator handles it, but the SDK's metadata omits it.
TOKEN_ENDPOINT_AUTH_METHODS: tuple[str, ...] = (
    "client_secret_post",
    "client_secret_basic",
    "none",
)

#: Discovery documents change only with a deploy; clients may cache them.
_METADATA_CACHE_CONTROL: str = "public, max-age=3600"
_METADATA_METHODS: list[str] = ["GET", "OPTIONS"]


def oauth_base_url(settings: Settings) -> str | None:
    """``public_base_url`` without a trailing slash, when OAuth can run on it.

    ``None`` when it is unset, or when it is plain HTTP on a non-loopback
    host: RFC 8414 requires an HTTPS issuer and the MCP SDK refuses to build
    the OAuth routes otherwise. That case is logged, not raised — the setting
    predates OAuth (notification deep-links use it), so an HTTP value must
    leave the server up on static tokens rather than stop it booting.
    """
    if not settings.public_base_url:
        return None
    base = settings.public_base_url.rstrip("/")
    parts = urlsplit(base)
    host = parts.hostname or ""
    # ``startswith("127.0.0.1")`` deliberately mirrors the MCP SDK's
    # ``validate_issuer_url`` (mcp/server/auth/routes.py), which is what would
    # otherwise refuse to build the routes; matching it exactly keeps "OAuth
    # on" and "SDK accepts the issuer" the same set. The value is operator
    # config, not client input, so the loose prefix match is not an attack
    # surface.
    if parts.scheme == "https" or (
        parts.scheme == "http" and (host == "localhost" or host.startswith("127.0.0.1"))
    ):
        return base
    logger.warning(
        "LIBRARY_PUBLIC_BASE_URL is not HTTPS (%s); MCP OAuth stays off and /mcp "
        "accepts static library_ tokens only",
        base,
    )
    return None


class LibraryMultiAuth(MultiAuth):
    """Library's OAuth server plus static API tokens, on one MCP endpoint.

    ``MultiAuth`` tries the OAuth provider first, then each verifier. On top
    of that this class owns the URLs a client discovers, because FastMCP's
    defaults are wrong for an app mounted at ``/mcp``:

    - the protected resource is exactly ``{public_base_url}/mcp`` (no trailing
      slash) in the 401's ``resource_metadata``, in the metadata's
      ``resource`` and in the path-aware metadata URL, matching the URL users
      paste and the audience tokens are bound to;
    - the authorization-server metadata also advertises RFC 9207 ``iss`` on
      the authorization response (the consent API adds it) and ``none`` as a
      token-endpoint auth method (public clients);
    - the discovery documents are served at the site root
      (``get_well_known_routes``), where clients look; FastMCP would put
      them only under ``/mcp/.well-known/…``.
    """

    def __init__(self, *, server: LibraryOAuthProvider, verifiers: list[TokenVerifier]) -> None:
        super().__init__(server=server, verifiers=verifiers)
        self.oauth: LibraryOAuthProvider = server

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        """Always the provider's ``resource_url`` (FastMCP would append the
        in-mount path, giving ``…/mcp/``)."""
        return AnyHttpUrl(self.oauth.resource_url)

    # ---------------------------------------------------------- documents

    def authorization_server_metadata(self) -> dict[str, Any]:
        """RFC 8414 metadata: the SDK's document plus ``iss`` support and ``none``."""
        metadata = build_metadata(
            AnyHttpUrl(self.oauth.issuer),
            None,
            self.oauth.client_registration_options or ClientRegistrationOptions(),
            self.oauth.revocation_options or RevocationOptions(),
        )
        metadata.token_endpoint_auth_methods_supported = list(TOKEN_ENDPOINT_AUTH_METHODS)
        if metadata.revocation_endpoint is not None:
            metadata.revocation_endpoint_auth_methods_supported = list(TOKEN_ENDPOINT_AUTH_METHODS)
        metadata.code_challenge_methods_supported = ["S256"]
        document = metadata.model_dump(mode="json", exclude_none=True)
        document["authorization_response_iss_parameter_supported"] = True
        return document

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        """RFC 9728 metadata for ``/mcp``: the resource, its issuer, its scope."""
        return ProtectedResourceMetadata(
            resource=AnyHttpUrl(self.oauth.resource_url),
            authorization_servers=[AnyHttpUrl(self.oauth.issuer)],
            scopes_supported=[OAUTH_SCOPE],
            resource_name="Library",
        )

    def _as_metadata_endpoint(self) -> ASGIApp:
        document = self.authorization_server_metadata()

        async def handle(request: Request) -> Response:
            return JSONResponse(document, headers={"Cache-Control": _METADATA_CACHE_CONTROL})

        return cors_middleware(handle, _METADATA_METHODS)

    def _protected_resource_endpoint(self) -> ASGIApp:
        handler = ProtectedResourceMetadataHandler(self.protected_resource_metadata())
        return cors_middleware(handler.handle, _METADATA_METHODS)

    # ------------------------------------------------------------- routes

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Routes inside the ``/mcp`` mount: the SDK's OAuth endpoints
        (``/authorize``, ``/token``, ``/register``, ``/revoke``) and the AS
        metadata at the in-mount paths a client derives from the issuer
        (``{issuer}/.well-known/oauth-authorization-server`` and the OIDC
        ``{issuer}/.well-known/openid-configuration``)."""
        operational = [
            route
            for route in self.oauth.get_routes(mcp_path)
            if not route.path.startswith("/.well-known/")
        ]
        as_metadata = self._as_metadata_endpoint()
        return [
            *operational,
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=as_metadata,
                methods=_METADATA_METHODS,
            ),
            Route(
                "/.well-known/openid-configuration", endpoint=as_metadata, methods=_METADATA_METHODS
            ),
        ]

    def get_well_known_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Discovery routes for the site root (app.py adds them before the SPA).

        Protected-resource metadata at its RFC 9728 path-aware URL
        (``/.well-known/oauth-protected-resource/mcp``, where the 401 points)
        and the bare root; AS metadata at RFC 8414's path-aware URL for the
        ``/mcp`` issuer, the bare root, and the matching
        ``openid-configuration`` spellings. The OIDC spellings serve the same
        OAuth document: Library is not an OpenID provider, but MCP clients
        may probe those URLs, and a 404 there ends discovery for no gain.
        """
        resource_url = AnyHttpUrl(self.oauth.resource_url)
        prm_path = urlsplit(str(build_resource_metadata_url(resource_url))).path
        issuer_path = urlsplit(self.oauth.issuer).path.rstrip("/")
        prm = self._protected_resource_endpoint()
        as_metadata = self._as_metadata_endpoint()
        endpoints: dict[str, ASGIApp] = {
            prm_path: prm,
            "/.well-known/oauth-protected-resource": prm,
            f"/.well-known/oauth-authorization-server{issuer_path}": as_metadata,
            "/.well-known/oauth-authorization-server": as_metadata,
            f"/.well-known/openid-configuration{issuer_path}": as_metadata,
            "/.well-known/openid-configuration": as_metadata,
        }
        return [
            Route(path, endpoint=endpoint, methods=_METADATA_METHODS)
            for path, endpoint in endpoints.items()
        ]


def build_mcp_auth(settings: Settings) -> AuthProvider:
    """The MCP endpoint's auth for these settings (see the module docstring)."""
    verifier = LibraryTokenVerifier()
    base = oauth_base_url(settings)
    if base is None:
        return verifier
    provider = LibraryOAuthProvider(
        base_url=f"{base}/mcp",
        resource_url=f"{base}/mcp",
        consent_url=f"{base}{CONSENT_PATH}",
        redirect_allowlist=settings.oauth_redirect_allowlist,
        # A client that registers without a scope gets the one scope, so an
        # /authorize that then asks for it is not refused as unregistered.
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[OAUTH_SCOPE], default_scopes=[OAUTH_SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    return LibraryMultiAuth(server=provider, verifiers=[verifier])


def oauth_provider_of(auth: AuthProvider) -> LibraryOAuthProvider | None:
    """The OAuth provider inside ``auth``, or ``None`` when OAuth is off."""
    return auth.oauth if isinstance(auth, LibraryMultiAuth) else None


#: The tool server. Deliberately built without ``auth=``: auth belongs to
#: each HTTP app build (``create_mcp_http_app``), not to this module global.
mcp: FastMCP = FastMCP(
    name="Library",
    version=library.__version__,
    instructions=SERVER_INSTRUCTIONS,
    mask_error_details=True,
)


def create_mcp_http_app(auth: AuthProvider) -> StarletteWithLifespan:
    """The Streamable-HTTP ASGI app behind ``auth``, ready to mount at ``/mcp``.

    Stateless + JSON responses: no session affinity, no SSE streams —
    maximally compatible with reverse proxies and in-process test
    transports, and sufficient for request/response tools like ours.
    FastMCP's own factory (what ``mcp.http_app`` calls, with the same
    global-settings defaults) takes the auth as an argument, so two app
    builds with different settings never share one.
    """
    return create_streamable_http_app(
        server=mcp,
        streamable_http_path="/",
        auth=auth,
        json_response=True,
        stateless_http=True,
        debug=fastmcp.settings.debug,
        host_origin_protection=fastmcp.settings.http_host_origin_protection,
        allowed_hosts=fastmcp.settings.http_allowed_hosts,
        allowed_origins=fastmcp.settings.http_allowed_origins,
    )


# ------------------------------------------------------------------ helpers


def _current_user_id() -> int | None:
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    user_id = claims.get("user_id")
    return user_id if isinstance(user_id, int) else None


async def _get_document(
    session: AsyncSession, document_id: int, *, for_update: bool = False
) -> Document:
    """The live document, or a ``ToolError``; ``for_update`` row-locks it
    until the session's transaction ends."""
    document = await session.get(Document, document_id, with_for_update=for_update)
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
        "recipient": (
            {"id": document.recipient.id, "name": document.recipient.name}
            if document.recipient
            else None
        ),
        "tags": [
            {"slug": tag.slug, "name": tag.name}
            for tag in sorted(document.tags, key=lambda tag: tag.slug)
        ],
        "topics": list(document.topics or []),
        "projects": [
            {"slug": project.slug, "name": project.name}
            for project in sorted(document.projects, key=lambda project: project.slug)
        ],
        "matters": [
            {"slug": matter.slug, "name": matter.name}
            for matter in sorted(document.matters, key=lambda matter: matter.slug)
        ],
        "document_date": document.document_date.isoformat() if document.document_date else None,
        "language": document.language.value,
        "status": document.status.value,
        "mime_type": document.mime_type,
        "page_count": document.page_count,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }


# -------------------------------------------------------------------- tools


@mcp.tool(title="Search documents", annotations=READ_ONLY)
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
    recipient: Annotated[
        str | None,
        Field(
            description=(
                "Filter by recipient name, case-insensitive substring match "
                "('john' matches 'John Smith'). See list_recipients."
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
    matter: Annotated[
        str | None,
        Field(description="Filter by matter slug (see list_matters)."),
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

    Returns the matching documents (id, title, summary, kind, sender,
    recipient, tags, document_date, language) plus the total match count. With `query`,
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
            recipient_contains=recipient,
            tag_slugs=(tag,) if tag else (),
            project_slugs=(project,) if project else (),
            matter_slugs=(matter,) if matter else (),
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


@mcp.tool(title="Get document", annotations=READ_ONLY)
async def get_document(
    document_id: DocumentId,
) -> dict[str, Any]:
    """Fetch one document's full metadata, OCR text, and processing history.

    Returns everything search_documents shows plus financial fields
    (amount_total, currency, due_date, expiry_date), provenance (source,
    original_filename, sha256, ocr_confidence), the audit-trail events,
    and the full OCR text. OCR text longer than 20,000 characters is
    truncated with an explanatory note and `ocr_text_truncated: true`;
    get_document_file returns the original file if you need all of it.
    Errors if the document id is unknown or deleted.
    """
    async with _session() as session:
        document = await _get_document(session, document_id)
        ocr_text = document.ocr_text
        truncated = False
        if ocr_text is not None and len(ocr_text) > OCR_TEXT_LIMIT:
            note = (
                f"\n[... truncated: the full text is {len(ocr_text)} characters; "
                "get_document_file returns the original file, or open the "
                "document in the Library web app]"
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


@mcp.tool(title="Get document file", annotations=READ_ONLY)
async def get_document_file(
    document_id: DocumentId,
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
    larger than 10 MB are refused; the user can download those by opening
    the document in the Library web app. Errors if the document id is
    unknown or deleted, or the requested variant was never produced.
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
            f"file is {size} bytes, over the 10 MB MCP limit; open document "
            f"{document_id} in the Library web app to download it"
        )
    return {
        "filename": filename,
        "mime_type": mime,
        "size_bytes": size,
        "content_base64": base64.b64encode(path.read_bytes()).decode(),
    }


@mcp.tool(
    title="Ingest document",
    # Adds a document and never changes or removes one; the same bytes sent
    # again return the existing document rather than a second copy.
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
)
async def ingest_document(
    filename: Annotated[
        str, Field(description="Original filename, including extension, e.g. 'invoice.pdf'.")
    ],
    content_base64: Annotated[
        str,
        Field(
            description=(
                "The file content, base64-encoded. Supported types: PDF, JPEG, "
                "PNG, HEIC/HEIF, TIFF, plain text, Markdown, and Word (DOCX)."
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
                f"{exc}; restore it from the trash in the Library web app (or "
                "purge it there) before ingesting this content again"
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


# ---------------------------------------------------------- edit + comment

#: The ``DocumentUpdate`` fields ``clear_fields`` can name: every nullable
#: scalar. ``tags``/``projects``/``matters`` clear with ``[]`` and
#: ``language`` cannot be cleared (the edit service refuses ``null`` for
#: both), so they are not offered.
ClearableField = Literal[
    "title",
    "summary",
    "document_date",
    "kind_slug",
    "sender",
    "recipient",
    "amount_total",
    "currency",
    "due_date",
    "expiry_date",
]

#: Versions the preview-token hash, so a change to what it covers can never
#: accept a token minted under the old scheme.
_PREVIEW_TOKEN_DOMAIN: str = "library-mcp-edit-preview-v2"

_UNCHANGED: str = "Omit to leave unchanged."


def _preview_token(document_id: int, changes: dict[str, Any]) -> str:
    """The preview token: SHA-256 over the document id and, for each field
    the edit sets, its current value and the value applying would store
    (``preview_update``'s output, read back from its rolled-back dry run;
    keys sorted). Hashing the stored value, not the input, means the token
    approves exactly what the user was shown.

    Stateless, because MCP gives a server no conversation to remember a
    preview in: apply recomputes the hash from the database and the
    arguments it is given and compares. A mismatch means the arguments are
    not the ones previewed (another value, another field, another
    document) or one of those fields changed since (someone else edited
    it). A change to a field the edit does not touch does not invalidate
    the token; nothing is being overwritten there.

    Deliberately not an HMAC: Library has no server secret, and none is
    needed, because this is not a security control. The caller already has
    edit rights and could mint a matching token itself by previewing; the
    token only makes "preview, show the user, then apply" the path of least
    resistance and catches a concurrent edit.
    """
    canonical = json.dumps(
        {"domain": _PREVIEW_TOKEN_DOMAIN, "document_id": document_id, "changes": changes},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _build_update(provided: dict[str, Any], clear_fields: Sequence[str] | None) -> DocumentUpdate:
    """``DocumentUpdate`` from the parameters that were given plus the
    cleared ones, so ``exclude_unset`` is exactly what the caller asked for."""
    fields = {name: value for name, value in provided.items() if value is not None}
    for name in clear_fields or ():
        if name in fields:
            raise ToolError(f"{name} is both set and listed in clear_fields; pick one")
        fields[name] = None
    if not fields:
        raise ToolError(
            "no changes given: pass at least one field to set, or name fields in clear_fields"
        )
    return DocumentUpdate(**fields)


@mcp.tool(
    title="Update document metadata",
    # Overwrites stored values (destructive); applying the same change again
    # leaves the same state (idempotent).
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
)
async def update_document_metadata(
    document_id: DocumentId,
    title: Annotated[str | None, Field(description=f"New title. {_UNCHANGED}")] = None,
    summary: Annotated[str | None, Field(description=f"New summary. {_UNCHANGED}")] = None,
    document_date: Annotated[
        date | None, Field(description=f"The date on the document, ISO. {_UNCHANGED}")
    ] = None,
    kind_slug: Annotated[
        str | None,
        Field(description=f"An existing kind slug (see list_kinds). {_UNCHANGED}"),
    ] = None,
    sender: Annotated[
        str | None,
        Field(
            description=(
                "Sender name; matched case-insensitively to an existing sender "
                f"(see list_senders), created if new. {_UNCHANGED}"
            )
        ),
    ] = None,
    recipient: Annotated[
        str | None,
        Field(
            description=(
                "Recipient name; matched case-insensitively to an existing "
                f"recipient (see list_recipients), created if new. {_UNCHANGED}"
            )
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(
            description=(
                "The complete new list of tag slugs (replaces the current tags; "
                f"unknown slugs are created). [] removes every tag. {_UNCHANGED}"
            )
        ),
    ] = None,
    projects: Annotated[
        list[str] | None,
        Field(
            description=(
                "The complete new list of project slugs or names (replaces the "
                f"current ones; unknown ones are created). [] removes all. {_UNCHANGED}"
            )
        ),
    ] = None,
    matters: Annotated[
        list[str] | None,
        Field(
            description=(
                "The complete new list of matter slugs or names (replaces the "
                f"current ones; unknown ones are created). [] removes all. {_UNCHANGED}"
            )
        ),
    ] = None,
    language: Annotated[
        DocumentLanguage | None,
        Field(description=f"Document language; cannot be cleared. {_UNCHANGED}"),
    ] = None,
    amount_total: Annotated[
        Decimal | None,
        Field(
            description=(
                "Total amount as a decimal, e.g. '123.45'. Refused when the amount "
                f"is split across spend lines in the web app. {_UNCHANGED}"
            )
        ),
    ] = None,
    currency: Annotated[
        CurrencyCode | None,
        Field(description=f"Three-letter ISO 4217 code, e.g. 'EUR'. {_UNCHANGED}"),
    ] = None,
    due_date: Annotated[
        date | None, Field(description=f"Payment due date, ISO. {_UNCHANGED}")
    ] = None,
    expiry_date: Annotated[
        date | None, Field(description=f"Expiry date (e.g. of a policy), ISO. {_UNCHANGED}")
    ] = None,
    clear_fields: Annotated[
        list[ClearableField] | None,
        Field(
            description=(
                "Fields to empty (set to no value), e.g. ['due_date', 'sender']. A "
                "field cannot be both set and cleared. Clear tags, projects or "
                "matters by passing []."
            )
        ),
    ] = None,
    apply: Annotated[
        bool,
        Field(
            description=(
                "false (default): preview only, nothing is written. true: write the "
                "change; needs the preview_token from a preview of exactly these "
                "arguments."
            )
        ),
    ] = False,
    preview_token: Annotated[
        str | None,
        Field(description="The preview_token a preview returned; required when apply is true."),
    ] = None,
) -> dict[str, Any]:
    """Change a document's details: preview first, then apply after the user agrees.

    Step 1, preview: call without `apply`. Nothing is written. Returns
    {document_id, applied: false, changes: {field: {current, proposed}},
    preview_token}. Show the user the changes and ask whether to apply.

    Step 2, apply: only once the user has agreed, call again with the SAME
    arguments plus `apply=true` and the `preview_token`. Returns
    {document_id, applied: true, updated_fields, current, review_status}.
    The edit is recorded as the connected user, and re-extraction never
    overwrites an edited field.

    Only the fields you pass change. Errors if the document is unknown or
    deleted, a value is invalid (an unknown kind, an amount split across
    spend lines), or, at apply, the token is missing or no longer matches
    (the document changed since the preview, or the arguments differ):
    then preview again and show the user the new changes.
    """
    update = _build_update(
        {
            "title": title,
            "summary": summary,
            "document_date": document_date,
            "kind_slug": kind_slug,
            "sender": sender,
            "recipient": recipient,
            "tags": tags,
            "projects": projects,
            "matters": matters,
            "language": language,
            "amount_total": amount_total,
            "currency": currency,
            "due_date": due_date,
            "expiry_date": expiry_date,
        },
        clear_fields,
    )

    async with _session() as session:
        # Locked on apply, before the dry run, so nobody can change the row
        # between computing the token and the commit.
        document = await _get_document(session, document_id, for_update=apply)
        # The same dry run on preview and on apply, so `expected` is the
        # preview's token when nothing changed in between. It also refuses
        # (with apply's own words) a change apply would refuse, so a preview
        # never shows the user one.
        try:
            changes = await preview_update(session, document, update)
        except (DocumentEditError, AllocationError) as exc:
            raise ToolError(str(exc)) from exc
        expected = _preview_token(document_id, changes)
        if not apply:
            return {
                "document_id": document_id,
                "applied": False,
                "changes": changes,
                "preview_token": expected,
            }
        if preview_token is None:
            raise ToolError(
                "preview first: call update_document_metadata without apply, show "
                "the user the changes, and apply only after they agree"
            )
        if not hmac.compare_digest(preview_token.encode(), expected.encode()):
            raise ToolError(
                "the document changed since the preview, or the fields differ from "
                "the ones previewed: preview again and show the user the new changes"
            )
        try:
            edited = await edit_document(
                session,
                document,
                update,
                settings=get_settings(),
                edited_by="mcp",
                user_id=_current_user_id(),
            )
        except (DocumentEditError, AllocationError) as exc:  # pragma: no cover - race
            # The dry run above already refused both under this row lock, so
            # only a kind deleted between it and this edit reaches here. An
            # AllocationError would follow a rollback that expired `document`:
            # only the message is used.
            raise ToolError(str(exc)) from exc

    # Re-read in a fresh session: `document`'s relationships (sender, kind,
    # ...) still hold their pre-edit objects, since only the ids were set.
    async with _session() as session:
        document = await _get_document(session, document_id)
        current = {name: preview_current(document, name) for name in changes}
        review_status = document.review_status.value
    return {
        "document_id": document_id,
        "applied": True,
        "updated_fields": edited,
        "current": to_jsonable_python(current),
        "review_status": review_status,
    }


@mcp.tool(
    title="Add comment",
    # Only adds; each call adds another comment.
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
)
async def add_comment(
    document_id: DocumentId,
    body: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "The comment text, e.g. 'This is our current house.' Leading and "
                "trailing whitespace is removed."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Attach a dated comment to a document, as the connected user.

    Comments record personal context the document itself does not say
    ("paid in cash", "this policy replaced the 2023 one"). The comment is
    queued for indexing so searches find the document through it. Each
    call adds a new comment. Returns {id, document_id, author_id, body,
    created_at}. Errors if the document is unknown or deleted, or the body
    is blank.
    """
    async with _session() as session:
        try:
            comment = await comments_service.add_comment(
                session, document_id=document_id, author_id=_current_user_id(), body=body
            )
        except comments_service.CommentError as exc:
            raise ToolError(str(exc)) from exc
        return CommentOut.model_validate(comment).model_dump(mode="json")


@mcp.tool(title="List document kinds", annotations=READ_ONLY)
async def list_kinds() -> dict[str, Any]:
    """List all document kinds (categories) with per-kind document counts.

    Use the returned slugs as the `kind` filter of search_documents.
    """
    async with _session() as session:
        kinds = await taxonomy.list_kinds(session)
    return {"kinds": [asdict(kind) for kind in kinds]}


@mcp.tool(title="List senders", annotations=READ_ONLY)
async def list_senders() -> dict[str, Any]:
    """List all known senders (correspondents) with per-sender document counts.

    Returns {senders: [{id, name, document_count, colour}]}, ordered by
    name. Every sender is listed, including those with no (non-deleted)
    documents; `colour` is a chart colour and may be null. Use the names
    with the `sender` filter of search_documents (case-insensitive
    substring match).
    """
    async with _session() as session:
        senders = await taxonomy.list_senders(session)
    return {"senders": [asdict(sender) for sender in senders]}


@mcp.tool(title="List recipients", annotations=READ_ONLY)
async def list_recipients() -> dict[str, Any]:
    """List all known recipients (addressees) with per-recipient document counts.

    Use the returned names with the `recipient` filter of search_documents
    (case-insensitive substring match).
    """
    async with _session() as session:
        recipients = await taxonomy.list_recipients(session)
    return {"recipients": [asdict(recipient) for recipient in recipients]}


@mcp.tool(title="List tags", annotations=READ_ONLY)
async def list_tags() -> dict[str, Any]:
    """List all tags with per-tag document counts.

    Use the returned slugs as the `tag` filter of search_documents.
    """
    async with _session() as session:
        tags = await taxonomy.list_tags(session)
    return {"tags": [asdict(tag) for tag in tags]}


@mcp.tool(title="List projects", annotations=READ_ONLY)
async def list_projects() -> dict[str, Any]:
    """List all projects (document collections) with per-project document counts.

    Use the returned slugs as the `project` filter of search_documents.
    Archived projects are omitted.
    """
    async with _session() as session:
        items = await projects.list_projects(session)
    return {"projects": [asdict(item) for item in items]}


@mcp.tool(title="List matters", annotations=READ_ONLY)
async def list_matters() -> dict[str, Any]:
    """List all business matters (subject categories) with per-matter document counts.

    Use the returned slugs as the `matter` filter of search_documents.
    Archived matters are omitted.
    """
    async with _session() as session:
        items = await matters.list_matters(session)
    return {"matters": [asdict(item) for item in items]}


@mcp.tool(title="Library statistics", annotations=READ_ONLY)
async def library_stats() -> dict[str, Any]:
    """Overview statistics of the archive: sizes, processing state, date range.

    Returns total_documents, by_status (processing lifecycle counts:
    received/ocr/extract/markdown/embed/indexed/failed; a status with no
    documents is omitted), by_kind (counts per kind slug,
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
