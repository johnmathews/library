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
from urllib.parse import quote, urlsplit

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan, create_streamable_http_app
from fastmcp.tools import ToolResult
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.routes import build_metadata, build_resource_metadata_url, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import ProtectedResourceMetadata
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
    ToolAnnotations,
)
from pydantic import AnyHttpUrl, AnyUrl, BaseModel, Field, StringConstraints
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

import library
from library import comments_service, matters, notes_service, projects, taxonomy
from library.ask.context import load_archive_context, render_archive_context
from library.auth.oauth_provider import LibraryOAuthProvider
from library.auth.service import validate_api_token
from library.config import Settings, get_settings
from library.db import get_sessionmaker
from library.document_reads import (
    DocumentText,
    DocumentTextRangeError,
    FileVariant,
    TextSource,
    document_file_url,
    document_text,
    document_web_url,
    list_item_fields,
    load_comments,
)
from library.documents_service import (
    DocumentEditError,
    edit_document,
    preview_current,
    preview_update,
)
from library.embedding import EmbeddingError, embed_query
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    DocumentStatus,
    Facet,
    FacetValue,
    IngestionEvent,
    Kind,
    Matter,
    Project,
    ReviewStatus,
    Tag,
    User,
)
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.schemas import (
    CommentOut,
    CurrencyCode,
    DocumentFields,
    DocumentListItem,
    DocumentUpdate,
    TagSlug,
)
from library.search import (
    DEFAULT_DOCUMENT_SORT,
    DEFAULT_SORT_DIRECTION,
    DocumentFilters,
    DocumentSort,
    SearchCoverage,
    SortDirection,
    build_document_query,
    hybrid_search,
)
from library.spend_lines import AllocationError
from library.storage import derived_path, path_for
from library.structured_query import (
    CONCEPT_TO_KIND,
    COVERAGE_GUIDE,
    SUM_AMOUNT_GUIDE,
    Aggregate,
    AmountGroup,
    Coverage,
    DocumentRef,
    GroupBy,
    SenderGroup,
    query_documents,
)

logger = logging.getLogger(__name__)

#: get_document's text window: default, floor and ceiling of ``max_chars``.
DOCUMENT_TEXT_DEFAULT_CHARS: int = 20_000
DOCUMENT_TEXT_MIN_CHARS: int = 1_000
DOCUMENT_TEXT_MAX_CHARS: int = 50_000
#: get_document_file returns a file's bytes only up to this size; above it the
#: result is the metadata and a download link. Base64 adds a third, and a
#: client puts the whole result in the model's context.
MAX_FILE_BYTES: int = 2 * 1024 * 1024
#: Image types LLM clients render from an ``image`` content block. Other
#: images (TIFF, HEIC) travel as a resource, so they cannot make a client
#: reject the whole result.
INLINE_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)
#: The longest note title: the web app's note editor caps it the same.
NOTE_TITLE_MAX_CHARS: int = 200
SEARCH_LIMIT_MAX: int = 50
#: semantic_search returns passage text with every hit, so its result grows
#: with ``limit`` far faster than search_documents'. MCP clients cap a tool
#: result (Claude Code's default is 25k tokens), so both the hit count and
#: the passage characters per call are bounded.
SEMANTIC_LIMIT_MAX: int = 20
SEMANTIC_PASSAGE_BUDGET: int = 24_000
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

Call `get_library_context` first. It names the connected user (so "my"
and "I" mean them, and it lists the addressee names that are theirs) and
the archive's vocabulary in the exact spelling the filters take: kinds,
matters, projects, tags, the facets with their values, and the most
frequent senders.

Two ways to find documents:
- `semantic_search` for what documents say, asked in natural language or
  in another language than the document ("travel allowance clause"
  finds a Dutch "reiskostenvergoeding"). It ranks by meaning and keywords
  together and returns the matching passages. Read its `coverage` before
  saying something is not in the archive: `unembedded` counts matching
  documents that are not indexed for it yet.
- `search_documents` for exact words, names and numbers, and to browse,
  count or page through documents by filters (full-text search with
  Dutch and English stemming; page with `offset`). Use it too when
  semantic search is unavailable.

Then `get_document` for one document's text, the household's comments on
it and whether its details are trusted (`review_status`). Long documents
come in pages: follow `next_start_page`. `list_kinds`, `list_senders`,
`list_recipients`, `list_tags`, `list_projects`, and `list_matters` list
filter values with document counts, and `library_stats` gives an
overview. `get_document_file` returns the file itself (an image you can
see, or the file as a resource) up to 2 MB; for a larger file it returns
a `download_url` to give the user, which opens in their browser, where
they are signed in to Library. Do not fetch that link yourself.

Amounts: `amount_total` is always a magnitude and `amount_kind` says what
it is (paid, due, refunded, a coverage limit, ...). Do not add amounts up
across documents yourself: the sign depends on `amount_kind`, and one
payment often appears on several documents. For any total ("how much did
we spend on ...") use `money_query`: it nets refunds, leaves out cover
limits and balances, counts each payment once, and says in `coverage`
what it left out and why. Pass on what `coverage` says when `excluded`
is not empty or `needs_review` is above 0.

To correct a document's details (title, kind, sender, dates, amount,
tags, ...) use `update_document_metadata` in two steps: call it without
`apply` to get a preview of the changes, show the user that preview,
and only after they agree call it again with `apply=true` and the
`preview_token` the preview returned. `add_comment` attaches a dated
note to a document (for example "this is our current house"); it is
recorded as the connected user and becomes searchable.

To file something new: `create_note` saves text you write (a summary of
a call, what was agreed) as a new note, and `ingest_document` adds a
file. Each `create_note` call makes another note, so call it once per
note and never retry it blindly. Everything these tools change is
recorded as the connected user.
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


# ------------------------------------------------------------- read results
#
# Typed, so FastMCP publishes an ``outputSchema`` for each read tool. The
# fields are ``schemas.DocumentFields``, built by
# ``document_reads.list_item_fields`` — the same values the web app's list
# and detail views show.


class DocumentEvent(BaseModel):
    """One audit-trail entry: what happened to the document, and when."""

    event: str
    created_at: datetime


class DocumentRead(DocumentFields):
    """The result of get_document."""

    source: DocumentSource = Field(description="How the document entered the archive.")
    original_filename: str | None
    sha256: str
    ocr_confidence: float | None
    web_url: str | None = Field(
        description=(
            "The document's page in the Library web app, to give the user; null when "
            "the server has no public URL configured."
        )
    )
    text: str = Field(
        description=(
            "The document's text: its per-page markdown (the text search matches on) "
            "joined with blank lines, or its OCR text when it has no pages yet."
        )
    )
    text_source: TextSource = Field(
        description="'pages' (page markdown), 'ocr_text' (no pages yet) or 'none' (no text)."
    )
    text_page_count: int = Field(description="Pages of text the document has; 0 without pages.")
    text_start_page: int | None = Field(description="First page in `text`; null without pages.")
    text_end_page: int | None = Field(
        description="Last page in `text`, possibly only its start; null without pages."
    )
    text_truncated: bool = Field(description="True when more text follows `text`.")
    text_total_chars: int = Field(description="Length of the whole text, every page.")
    next_start_page: int | None = Field(
        description="The start_page to pass to read on; null when there is no next page."
    )
    continuation: str | None = Field(
        description="How to read the rest when `text_truncated`; null otherwise."
    )
    comments: list[CommentOut] = Field(
        description=(
            "Household members' comments, newest first: personal context the document "
            "does not say. author_id is the Library user who wrote it."
        )
    )
    events: list[DocumentEvent] = Field(description="Audit trail, oldest first.")


class SemanticSearchHit(DocumentFields):
    """One semantic_search result: a search_documents row plus its passages."""

    score: float = Field(
        description=(
            "Relevance (reciprocal rank fusion of meaning and keyword rankings); higher "
            "is better, comparable only within one call."
        )
    )
    passages: list[str] = Field(
        description=(
            "The document's passages nearest the query, best first, shortened to fit "
            "the result. Empty when only the keyword ranking found the document; "
            "coverage.unembedded says whether documents are missing from the meaning "
            "index. Read the full text with get_document."
        )
    )
    passages_truncated: bool = Field(
        description="True when passages were dropped or shortened to fit the result."
    )
    page_number: int | None = Field(
        description="The page of the best passage, when the document has pages."
    )


class SemanticSearchResults(BaseModel):
    """The result of semantic_search."""

    results: list[SemanticSearchHit]
    coverage: SearchCoverage = Field(
        description=(
            "matched: documents your filters let through; returned: how many came "
            "back; unembedded: matched documents not indexed for meaning, which "
            "only keyword matches can reach. matched 0 means the filters excluded "
            "everything; a non-zero unembedded means an answer may be incomplete "
            "for that reason, not because the archive is silent."
        )
    )


class LibraryContext(BaseModel):
    """The result of get_library_context."""

    context: str = Field(
        description=(
            "Who the connected user is (their name, the addressee names that are "
            "theirs, their own notes about the household) and the archive's "
            "vocabulary: kinds, matters, projects, tags, facets with their values "
            "as {facet_key: value_key}, frequent senders."
        )
    )


class SearchResults(BaseModel):
    """The result of search_documents."""

    results: list[DocumentListItem]
    total: int = Field(description="Matching documents, before limit and offset.")
    offset: int
    limit: int
    next_offset: int | None = Field(
        description="The offset of the next page of results; null on the last page."
    )


class MoneyQueryResult(BaseModel):
    """The result of money_query: ``structured_query.query_documents``'s,
    field for field, with its row and coverage types."""

    result_type: Aggregate = Field(description="The aggregate the rows answer.")
    rows: list[AmountGroup | SenderGroup | DocumentRef] = Field(
        description=(
            "sum_amount: one {key, total, currency, document_count, document_ids} per "
            "currency (and per sender or kind with group_by; key is null without it), "
            "total a signed decimal string. distinct_senders: {sender, document_count, "
            "document_ids}. list: document rows. document_ids cite at most 25."
        )
    )
    coverage: Coverage = Field(
        description=(
            "matched: documents your filters let through; included: those the rows "
            "account for; excluded: reason -> documents dropped for it (empty when "
            "the rows are the whole story); needs_review: included documents whose "
            "extracted details validation flagged."
        )
    )


class DocumentFile(BaseModel):
    """get_document_file's metadata part. The bytes are not in it: they come
    as an ``image`` or embedded ``resource`` content block beside it."""

    document_id: int
    variant: FileVariant
    filename: str
    mime_type: str
    size_bytes: int
    content_included: bool = Field(
        description=(
            "True when the file itself is in this result, as an image or a resource "
            "content block; false when it is over the 2 MB limit."
        )
    )
    download_url: str | None = Field(
        description=(
            "The file's download link in the Library web app. It opens in the user's "
            "browser, where they are signed in to Library: give it to the user, do not "
            "fetch it. Null when the server has no public URL configured."
        )
    )
    note: str | None = Field(
        description="Why the file is not included and what to do instead; null when it is."
    )


class CreatedNote(BaseModel):
    """The result of create_note: the new note document."""

    id: int = Field(description="The new note's document id.")
    title: str
    status: DocumentStatus = Field(
        description="Processing state; the text is readable and searchable already."
    )
    page_count: int | None
    web_url: str | None = Field(
        description=(
            "The note's page in the Library web app, to give the user; null when the "
            "server has no public URL configured."
        )
    )


def _continuation(window: DocumentText, max_chars: int) -> str | None:
    """How to read past ``window``, worded for the model; ``None`` when the
    window reaches the end of the text."""
    if not window.truncated:
        return None
    if window.source != "pages":
        return (
            f"The text is {window.total_chars} characters and stops at {max_chars}. "
            "This document has no page text to page through: call get_document again "
            f"with a larger max_chars (up to {DOCUMENT_TEXT_MAX_CHARS}), or use "
            "get_document_file for the original file."
        )
    end, count, following = window.end_page, window.page_count, window.next_start_page
    if window.end_page_complete:
        return (
            f"The text stops after page {end} of {count}. Call get_document again with "
            f"start_page={following} to continue."
        )
    if following == end:
        return (
            f"The text stops part-way through page {end} of {count}. Call get_document "
            f"again with start_page={following} to continue (page {end} is repeated whole)."
        )
    hint = (
        f"Page {end} is longer than max_chars={max_chars} and was cut. Call get_document "
        f"again with start_page={end} and a larger max_chars (up to "
        f"{DOCUMENT_TEXT_MAX_CHARS}) to read it whole, or use get_document_file."
    )
    if following is not None:
        hint += f" The next page is start_page={following}."
    return hint


# ------------------------------------------------------- shared parameters
#
# The filters search_documents, semantic_search and money_query take, defined
# once so the tools describe and validate each filter the same way. Each maps
# onto one field of ``search.DocumentFilters``.
#
# Every text value is stripped and must then be non-empty. A blank value is a
# filter that matches no document (a blank sender is ``ilike '%%'``, which
# matches every one), so it would answer with a plausible empty or unfiltered
# result instead of an error. Refused by the schema (``minLength``) rather than
# dropped, so the model learns its argument was wrong; Ask's tool loop has no
# schema validation and drops them instead (``ask.engine._text_arg``).

FilterText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

KindFilter = Annotated[
    FilterText | None,
    Field(description="Filter by document kind slug (see list_kinds), e.g. 'invoice'."),
]
SenderFilter = Annotated[
    FilterText | None,
    Field(
        description=(
            "Filter by sender name, case-insensitive substring match "
            "('eneco' matches 'Eneco BV'). See list_senders."
        )
    ),
]
RecipientFilter = Annotated[
    FilterText | None,
    Field(
        description=(
            "Filter by recipient name, case-insensitive substring match "
            "('john' matches 'John Smith'). See list_recipients."
        )
    ),
]
TagsFilter = Annotated[
    list[FilterText] | None,
    Field(description="Tag slugs (see list_tags); a document must have ALL of them."),
]
ProjectsFilter = Annotated[
    list[FilterText] | None,
    Field(description="Project slugs (see list_projects); a document in ANY of them matches."),
]
MattersFilter = Annotated[
    list[FilterText] | None,
    Field(description="Matter slugs (see list_matters); a document in ANY of them matches."),
]
FacetsFilter = Annotated[
    dict[FilterText, FilterText] | None,
    Field(
        description=(
            "Facet labels as {facet_key: value_key}, e.g. {'category': 'energy'}; a "
            "document must carry ALL of them (it holds one value per facet). "
            "get_library_context lists the facets and their values."
        )
    ),
]
DateFromFilter = Annotated[
    date | None,
    Field(description="Only documents dated on or after this ISO date (document_date)."),
]
DateToFilter = Annotated[
    date | None,
    Field(description="Only documents dated on or before this ISO date (document_date)."),
]


# -------------------------------------------------------------------- tools


@mcp.tool(title="Search documents", annotations=READ_ONLY)
async def search_documents(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Full-text search over title, summary, and document text, stemmed in "
                "both Dutch and English (searching 'rekening' finds 'rekeningen'). "
                "Websearch syntax: quoted phrases, OR, -exclusion. Omit to browse "
                "by filters only."
            )
        ),
    ] = None,
    kind: KindFilter = None,
    sender: SenderFilter = None,
    recipient: RecipientFilter = None,
    tags: TagsFilter = None,
    projects: ProjectsFilter = None,
    matters: MattersFilter = None,
    facets: FacetsFilter = None,
    language: Annotated[
        DocumentLanguage | None,
        Field(description="Filter by detected document language."),
    ] = None,
    status: Annotated[
        DocumentStatus | None,
        Field(
            description=(
                "Filter by processing state; 'indexed' is fully processed, 'failed' "
                "means processing stopped."
            )
        ),
    ] = None,
    review_status: Annotated[
        ReviewStatus | None,
        Field(
            description=(
                "Filter by whether the extracted details are trusted: 'verified' "
                "(checked by a person), 'needs_review' (validation flagged them), "
                "'unreviewed'."
            )
        ),
    ] = None,
    source: Annotated[
        DocumentSource | None,
        Field(description="Filter by how the document entered the archive."),
    ] = None,
    date_from: DateFromFilter = None,
    date_to: DateToFilter = None,
    sort: Annotated[
        DocumentSort,
        Field(
            description=(
                "Order without `query`: 'document_date' (undated last) or "
                "'added_date'. Ignored with `query`, which orders by relevance."
            )
        ),
    ] = DEFAULT_DOCUMENT_SORT,
    direction: Annotated[
        SortDirection,
        Field(description="'desc' (newest first, the default) or 'asc'. Ignored with `query`."),
    ] = DEFAULT_SORT_DIRECTION,
    limit: Annotated[
        int,
        Field(ge=1, le=SEARCH_LIMIT_MAX, description="Maximum number of results."),
    ] = 10,
    offset: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_DOCUMENT_ID,
            description="Results to skip, to page past `limit` (pass next_offset).",
        ),
    ] = 0,
) -> SearchResults:
    """Search and filter the document archive; the primary way to find documents.

    Returns {results, total, offset, limit, next_offset}. Each result has the
    document's id, title, summary, kind, sender, recipient, tags, projects,
    matters, dates, amount_total, currency, amount_kind, review_status and
    processing status. With `query`, results are ranked by relevance and
    each carries a `snippet` (matching text fragments, matches wrapped in
    <b>...</b>) and a `rank` score; without it, newest documents come first.
    Different filters AND-combine with each other and with `query`. Within
    one filter, `tags` and `facets` need ALL their values, while `projects`
    and `matters` match a document in ANY of theirs. Page with `offset`
    (next_offset is the next page's offset, null on the last). Follow up with
    get_document for a result's full text. amount_total is always a
    magnitude: amount_kind says whether it is money paid, due or refunded,
    so do not add amounts up across results yourself; money_query totals
    them correctly.
    """
    document_query = build_document_query(
        query,
        DocumentFilters(
            kind_slug=kind,
            sender_contains=sender,
            recipient_contains=recipient,
            tag_slugs=tuple(tags or ()),
            project_slugs=tuple(projects or ()),
            matter_slugs=tuple(matters or ()),
            facets=dict(facets or {}),
            language=language,
            status=status,
            review_status=review_status,
            source=source,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            direction=direction,
        ),
    )
    async with _session() as session:
        total = (await session.execute(document_query.count)).scalar_one()
        result = await session.execute(document_query.statement.limit(limit).offset(offset))
        if document_query.has_rank:
            results = [
                DocumentListItem(**list_item_fields(document), snippet=snippet, rank=rank)
                for document, rank, snippet in result.all()
            ]
        else:
            results = [
                DocumentListItem(**list_item_fields(document))
                for document in result.scalars().all()
            ]
    following = offset + len(results)
    return SearchResults(
        results=results,
        total=total,
        offset=offset,
        limit=limit,
        next_offset=following if following < total and results else None,
    )


@mcp.tool(title="Semantic search", annotations=READ_ONLY)
async def semantic_search(
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
        Field(
            description=(
                "What to find, in natural language and in any language: 'the travel "
                "allowance clause in my contract' also finds a Dutch "
                "'reiskostenvergoeding'."
            )
        ),
    ],
    kind: KindFilter = None,
    sender: SenderFilter = None,
    recipient: RecipientFilter = None,
    tags: TagsFilter = None,
    projects: ProjectsFilter = None,
    matters: MattersFilter = None,
    facets: FacetsFilter = None,
    date_from: DateFromFilter = None,
    date_to: DateToFilter = None,
    limit: Annotated[
        int,
        Field(ge=1, le=SEMANTIC_LIMIT_MAX, description="Maximum number of documents."),
    ] = 10,
) -> SemanticSearchResults:
    """Find documents by what they say: meaning and keywords together.

    For questions about a document's content asked in natural language or
    in another language than the document. Ranks by both the meaning of
    the query (vector search over document passages) and its words
    (full-text search), fused. Returns {results, coverage}. Each result is
    a search_documents row (id, title, kind, sender, dates, amount_total,
    amount_kind, review_status, ...) plus `passages` (the document's text
    nearest the query, best first), `page_number` and `score`. The filters
    mean what they mean in search_documents and AND-combine; scope the
    search when the question names a sender, a kind or a period.

    Read `coverage` before concluding anything is absent: `matched` is how
    many documents passed the filters (0 means widen them), `returned` how
    many came back, and `unembedded` how many matched documents are not
    indexed for meaning yet; say so when it is not 0. For exact words,
    names or numbers, browsing, counting or paging, use search_documents.
    Errors when the embedding service is down: use search_documents then.
    """
    settings = get_settings()
    try:
        embedding = await embed_query(query, settings=settings)
    except EmbeddingError as exc:
        logger.warning("mcp semantic_search: embedding failed: %s", exc)
        raise ToolError(
            "Semantic search is unavailable right now (the embedding service did not "
            "answer). Use search_documents, keyword search with Dutch and English "
            "stemming, instead."
        ) from exc
    filters = DocumentFilters(
        kind_slug=kind,
        sender_contains=sender,
        recipient_contains=recipient,
        tag_slugs=tuple(tags or ()),
        project_slugs=tuple(projects or ()),
        matter_slugs=tuple(matters or ()),
        facets=dict(facets or {}),
        date_from=date_from,
        date_to=date_to,
    )
    async with _session() as session:
        search = await hybrid_search(
            session,
            query=query,
            query_embedding=embedding,
            filters=filters,
            top_k=limit,
            chunks_per_doc=settings.retrieve_chunks_per_doc,
        )
        per_hit = SEMANTIC_PASSAGE_BUDGET // max(1, len(search.hits))
        results = []
        for hit in search.hits:
            passages, truncated = _fit_passages(hit.chunk_texts, per_hit)
            results.append(
                SemanticSearchHit(
                    **list_item_fields(hit.document),
                    score=hit.score,
                    passages=passages,
                    passages_truncated=truncated,
                    page_number=hit.page_number,
                )
            )
    return SemanticSearchResults(results=results, coverage=search.coverage)


def _fit_passages(passages: Sequence[str], budget: int) -> tuple[list[str], bool]:
    """``passages`` best first, cut to at most ``budget`` characters in total.

    Whole passages are kept while they fit; the first one that does not is
    shortened to the space left (ending in an ellipsis) and the rest are
    dropped, so the best passage always keeps its start.
    """
    kept: list[str] = []
    used = 0
    for passage in passages:
        room = budget - used
        if len(passage) <= room:
            kept.append(passage)
            used += len(passage)
            continue
        if room > 1:
            kept.append(passage[: room - 1] + "…")
        return kept, True
    return kept, False


@mcp.tool(title="Get library context", annotations=READ_ONLY)
async def get_library_context() -> LibraryContext:
    """Who the connected user is, and the archive's vocabulary. Call this first.

    Returns {context}: a short text naming the user (so "my", "me" and "I"
    in a question mean them), the addressee names that are theirs, their
    own notes about the household when they wrote any (authoritative
    personal context), and the exact slugs and names the search filters
    take: kinds, matters, projects, tags, the facets with their values
    (search_documents' `facets` filter), and the most frequent senders.
    The same block the web app's Ask feature gives its model.
    """
    user_id = _current_user_id()
    async with _session() as session:
        user = await session.get(User, user_id) if user_id is not None else None
        if user is None:
            raise ToolError("No Library user is connected to this session; reconnect the app.")
        context = render_archive_context(await load_archive_context(session, user))
    return LibraryContext(context=context)


@mcp.tool(title="Get document", annotations=READ_ONLY)
async def get_document(
    document_id: DocumentId,
    start_page: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_DOCUMENT_ID,
            description=(
                "The page of text to start at (default 1); pass next_start_page to "
                "read on through a long document."
            ),
        ),
    ] = 1,
    max_chars: Annotated[
        int,
        Field(
            ge=DOCUMENT_TEXT_MIN_CHARS,
            le=DOCUMENT_TEXT_MAX_CHARS,
            description="The most characters of text to return (default 20000).",
        ),
    ] = DOCUMENT_TEXT_DEFAULT_CHARS,
) -> DocumentRead:
    """Fetch one document: its metadata, its text, the household's comments on it.

    Returns everything search_documents shows plus provenance (source,
    original_filename, sha256, ocr_confidence), `web_url` (its page in the
    Library web app, when configured), the comments (personal context the
    document itself does not say; newest first), the audit-trail events,
    and `text`: the document's page markdown, which is what search matches
    on, or its OCR text when it has no pages yet. `review_status` says
    whether the extracted details are trusted ('needs_review' comes with
    `review_findings`).

    Text is paged: at most `max_chars` characters (default 20000) from
    `start_page` on. When `text_truncated` is true, `continuation` says how
    to read on, usually by calling again with `start_page=next_start_page`.

    Money: amount_total is always a magnitude and amount_kind says what it
    is (payment_due, payment_made, refund = money back, coverage_limit,
    ...). Do not add up amount_total across documents yourself: the sign
    depends on amount_kind and one payment often appears on several
    documents (an invoice and its receipt). money_query totals amounts
    correctly.

    Errors if the document id is unknown or deleted, or `start_page` is past
    the document's last page of text.
    """
    async with _session() as session:
        document = await _get_document(session, document_id)
        try:
            window = await document_text(
                session, document, max_chars=max_chars, start_page=start_page
            )
        except DocumentTextRangeError as exc:
            raise ToolError(str(exc)) from exc
        comments = await load_comments(session, document_id, newest_first=True)
        return DocumentRead(
            **list_item_fields(document),
            source=document.source,
            original_filename=document.original_filename,
            sha256=document.sha256,
            ocr_confidence=document.ocr_confidence,
            web_url=document_web_url(get_settings().public_base_url, document_id),
            text=window.text,
            text_source=window.source,
            text_page_count=window.page_count,
            text_start_page=window.start_page,
            text_end_page=window.end_page,
            text_truncated=window.truncated,
            text_total_chars=window.total_chars,
            next_start_page=window.next_start_page,
            continuation=_continuation(window, max_chars),
            comments=[CommentOut.model_validate(comment) for comment in comments],
            events=[
                DocumentEvent(event=event.event, created_at=event.created_at)
                for event in sorted(document.events, key=lambda event: event.id)
            ],
        )


#: money_query's description. The sign/dedup and coverage sentences are
#: ``structured_query``'s, the same ones Ask's ``query_documents`` tool quotes.
MONEY_QUERY_DESCRIPTION: str = (
    "Add up, count or list documents' money: the only correct way to total "
    "amounts across documents. Returns {result_type, rows, coverage}. "
    + SUM_AMOUNT_GUIDE
    + " Totals are per currency; group_by (sum_amount only) also splits them by "
    "sender or kind. distinct_senders: the senders among matching documents, "
    "with document counts. list: matching documents, newest first, up to 50. "
    "The filters mean what they mean in search_documents. "
    + COVERAGE_GUIDE
    + " When `excluded` is not empty, say in your answer what was left out, with "
    "the reason and the count; when `needs_review` is above 0, say that many "
    "included documents have flagged details. Never present a partial total as "
    "complete. review_status filters by trust, and what it removes is reported "
    "as `filtered_review_status`: prefer reporting needs_review over filtering it "
    "out of a total."
)


async def _unknown_filter_values(session: AsyncSession, filters: DocumentFilters) -> list[str]:
    """One phrase per tag, project or matter slug and facet key/value pair in
    ``filters`` that the archive does not have, in argument order.

    Only money_query asks: such a filter matches nothing, and a total over
    nothing is a 0 that reads like an answer. The search tools return an empty
    result whose counts already say nothing matched.
    """
    unknown: list[str] = []
    for label, model, slugs in (
        ("tag", Tag, filters.tag_slugs),
        ("project", Project, filters.project_slugs),
        ("matter", Matter, filters.matter_slugs),
    ):
        if not slugs:
            continue
        known = set((await session.scalars(select(model.slug).where(model.slug.in_(slugs)))).all())
        unknown.extend(f"unknown {label} '{slug}'" for slug in slugs if slug not in known)
    if filters.facets:
        pairs = (
            await session.execute(
                select(Facet.key, FacetValue.key)
                .outerjoin(FacetValue, FacetValue.facet_id == Facet.id)
                .where(Facet.key.in_(list(filters.facets)))
            )
        ).all()
        facets = {facet for facet, _ in pairs}
        values = set(pairs)
        for facet, value in filters.facets.items():
            if facet not in facets:
                unknown.append(f"unknown facet '{facet}'")
            elif (facet, value) not in values:
                unknown.append(f"unknown facet value {facet}='{value}'")
    return unknown


@mcp.tool(title="Money query", annotations=READ_ONLY, description=MONEY_QUERY_DESCRIPTION)
async def money_query(
    aggregate: Annotated[
        Aggregate,
        Field(
            description=(
                "'sum_amount' (default) totals the money; 'distinct_senders' lists the "
                "senders; 'list' lists the documents."
            )
        ),
    ] = "sum_amount",
    group_by: Annotated[
        GroupBy | None,
        Field(description="Split a sum_amount total by 'sender' or 'kind'."),
    ] = None,
    kind: KindFilter = None,
    sender: SenderFilter = None,
    recipient: RecipientFilter = None,
    tags: TagsFilter = None,
    projects: ProjectsFilter = None,
    matters: MattersFilter = None,
    facets: FacetsFilter = None,
    date_from: DateFromFilter = None,
    date_to: DateToFilter = None,
    review_status: Annotated[
        ReviewStatus | None,
        Field(
            description=(
                "Only documents whose extracted details are 'verified', 'needs_review' "
                "or 'unreviewed'. Omit (usually right) to include everything."
            )
        ),
    ] = None,
) -> MoneyQueryResult:
    if group_by is not None and aggregate != "sum_amount":
        raise ToolError(
            "group_by applies only to aggregate='sum_amount'; omit it, or use sum_amount."
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ToolError(f"date_from {date_from} is after date_to {date_to}.")
    filters = DocumentFilters(
        kind_slug=kind,
        sender_contains=sender,
        recipient_contains=recipient,
        tag_slugs=tuple(tags or ()),
        project_slugs=tuple(projects or ()),
        matter_slugs=tuple(matters or ()),
        facets=dict(facets or {}),
        date_from=date_from,
        date_to=date_to,
        review_status=review_status,
    )
    async with _session() as session:
        if kind is not None:
            known = await session.scalar(select(Kind.id).where(Kind.slug == kind))
            if known is None:
                # A kind that matches nothing makes a total of 0 that reads
                # like a real answer; a concept word is the usual cause.
                suggestion = CONCEPT_TO_KIND.get(kind.strip().lower())
                hint = f"; did you mean kind='{suggestion}'?" if suggestion else "."
                raise ToolError(f"unknown kind '{kind}'{hint} list_kinds lists the kind slugs.")
        unknown = await _unknown_filter_values(session, filters)
        if unknown:
            raise ToolError(
                "; ".join(unknown) + ". get_library_context lists the tags, projects, "
                "matters and facet values the filters take; list_tags, list_projects and "
                "list_matters list them with document counts."
            )
        result = await query_documents(
            session, filters=filters, aggregate=aggregate, group_by=group_by
        )
    return MoneyQueryResult.model_validate(result)


def _file_block(data: bytes, mime_type: str, uri: AnyUrl) -> ImageContent | EmbeddedResource:
    """The content block that carries a file: an image block for the types
    clients render, a text resource for text, a blob resource otherwise."""
    if mime_type in INLINE_IMAGE_MIME_TYPES:
        return ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType=mime_type)
    resource: TextResourceContents | BlobResourceContents
    if mime_type.startswith("text/"):
        resource = TextResourceContents(
            uri=uri, mimeType=mime_type, text=data.decode("utf-8", errors="replace")
        )
    else:
        resource = BlobResourceContents(
            uri=uri, mimeType=mime_type, blob=base64.b64encode(data).decode()
        )
    return EmbeddedResource(type="resource", resource=resource)


@mcp.tool(
    title="Get document file",
    annotations=READ_ONLY,
    # The return is a ToolResult (content blocks plus structured content), so
    # FastMCP cannot derive the schema; it describes the metadata part.
    output_schema=DocumentFile.model_json_schema(),
)
async def get_document_file(
    document_id: DocumentId,
    variant: Annotated[
        FileVariant,
        Field(
            description=(
                "'original' (default) for the stored file as uploaded; "
                "'searchable_pdf' for the OCR-produced PDF with a text layer "
                "(only exists when the document's has_searchable_pdf is true)."
            )
        ),
    ] = "original",
) -> ToolResult:
    """Retrieve a document's file itself, to look at it or hand it on.

    For a document's text, use get_document instead: this returns the
    file. A JPEG, PNG, GIF or WebP image comes back as an image you can
    see; a PDF or any other file as an embedded resource (text files as
    text). Beside it is the metadata: filename, mime_type, size_bytes,
    content_included and download_url.

    Files over 2 MB are not returned: the result is the metadata alone,
    with content_included false and a note saying why. That is not an
    error. download_url is the file's link in the Library web app (null
    when the server has no public URL). It opens in the user's browser,
    where they are signed in to Library, so give the link to the user
    rather than fetching it yourself. Errors if the document id is
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
    included = size <= MAX_FILE_BYTES
    download_url = document_file_url(get_settings().public_base_url, document_id, variant)
    note: str | None = None
    if not included:
        where = (
            "Give the user download_url: it opens in their browser, where they are "
            "signed in to Library."
            if download_url
            else f"Ask the user to open document {document_id} in the Library web app "
            "to download it."
        )
        note = (
            f"The file is {size} bytes, over the 2 MB limit for returning a file here, "
            f"so it is not included. {where} For its text, use get_document."
        )
    meta = DocumentFile(
        document_id=document_id,
        variant=variant,
        filename=filename,
        mime_type=mime,
        size_bytes=size,
        content_included=included,
        download_url=download_url,
        note=note,
    )
    # The metadata as JSON text first: clients that read only text content
    # still see it (and the structured content repeats it for the rest).
    content: list[ContentBlock] = [TextContent(type="text", text=meta.model_dump_json())]
    if included:
        uri = AnyUrl(f"library://documents/{document_id}/{variant}/{quote(filename)}")
        content.append(_file_block(path.read_bytes(), mime, uri))
    return ToolResult(content=content, structured_content=meta.model_dump(mode="json"))


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
    document. To file text you wrote yourself, use create_note instead.
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
        list[TagSlug] | None,
        Field(
            description=(
                "The complete new list of tag slugs, at most 64 characters each "
                "(replaces the current tags; unknown slugs are created). [] removes "
                f"every tag. {_UNCHANGED}"
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


@mcp.tool(
    title="Create note",
    # Only adds; notes are never deduplicated, so each call files another one.
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
)
async def create_note(
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_TITLE_MAX_CHARS),
        Field(description="The note's title, e.g. 'Call with the insurer, 4 March'."),
    ],
    body_markdown: Annotated[
        str,
        Field(min_length=1, description="The note's text, as Markdown. Must not be blank."),
    ],
) -> CreatedNote:
    """Write a new note into the archive, as the connected user.

    A note is a document whose text you write: a summary of a phone
    call, what was agreed with a contractor, where a paper original is
    kept. It is the web app's New note: readable through get_document and
    findable by search_documents as soon as this returns, and its kind,
    summary and tags are filled in afterwards from the text.

    Each call creates a new note, even with the same title and text, so
    do not call it again for the same note (not to retry either: if a
    call may have gone through, look for the title with search_documents
    first). To add context to an existing document use add_comment.
    Returns {id, title, status, page_count, web_url}.
    """
    if not body_markdown.strip():
        raise ToolError("body_markdown must not be blank")
    async with _session() as session:
        document = await notes_service.create_note(
            session, title=title, body_markdown=body_markdown, uploader_id=_current_user_id()
        )
        return CreatedNote(
            id=document.id,
            title=title,
            status=document.status,
            page_count=document.page_count,
            web_url=document_web_url(get_settings().public_base_url, document.id),
        )


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

    Use the returned slugs in the `tags` filter of search_documents and
    semantic_search.
    """
    async with _session() as session:
        tags = await taxonomy.list_tags(session)
    return {"tags": [asdict(tag) for tag in tags]}


@mcp.tool(title="List projects", annotations=READ_ONLY)
async def list_projects() -> dict[str, Any]:
    """List all projects (document collections) with per-project document counts.

    Use the returned slugs in the `projects` filter of search_documents and
    semantic_search.
    Archived projects are omitted.
    """
    async with _session() as session:
        items = await projects.list_projects(session)
    return {"projects": [asdict(item) for item in items]}


@mcp.tool(title="List matters", annotations=READ_ONLY)
async def list_matters() -> dict[str, Any]:
    """List all business matters (subject categories) with per-matter document counts.

    Use the returned slugs in the `matters` filter of search_documents and
    semantic_search.
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
