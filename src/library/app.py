"""FastAPI application factory for the Library backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

import library
from library.api import auth, documents, jobs, taxonomy
from library.auth.deps import csrf_protect, current_user
from library.jobs import job_app
from library.mcp_server import create_mcp_http_app

API_DESCRIPTION = """\
Self-hosted document archive. Upload scans and files, let OCR and Claude
extraction make them searchable, then find them again with full-text search
(Dutch and English stemming), filters, and downloads.

The REST API is a first-class product surface — everything the web app can
do is available here. See `docs/api.md` in the repository for the narrative
documentation.

Every `/api` endpoint except `POST /api/auth/login` requires authentication:
a session cookie (browsers) or an `Authorization: Bearer library_…` API
token (scripts, MCP). Cookie-authenticated state changes also need the
`X-CSRF-Token` header. See `docs/api.md` §1.9.
"""

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "auth",
        "description": (
            "Login/logout (cookie sessions), the current user, and API-token "
            "management. Accounts are created with the `library user` CLI."
        ),
    },
    {
        "name": "documents",
        "description": (
            "Upload, search, read, edit, and soft-delete documents; download "
            "originals, searchable PDFs, and thumbnails."
        ),
    },
    {
        "name": "taxonomy",
        "description": (
            "Kinds, senders, and tags with document counts — the valid values "
            "for the document list filters and metadata edits."
        ),
    },
    {
        "name": "jobs",
        "description": "Visibility into the background processing queue (OCR, "
        "extraction, thumbnails).",
    },
]


def create_app() -> FastAPI:
    """Build and return the Library FastAPI application."""
    # Built per app instance: the MCP ASGI app's session manager is created
    # and torn down by its lifespan, which we run inside our own so the
    # mounted /mcp transport shares the application's lifetime.
    mcp_http = create_mcp_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the Procrastinate connection (so defer() works) and run the
        mounted MCP app's lifespan (its Streamable HTTP session manager)."""
        async with job_app.open_async(), mcp_http.lifespan(mcp_http):
            yield

    app = FastAPI(
        title="Library",
        version=library.__version__,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    # Auth gate + CSRF for the whole /api surface, attached at include level
    # so future routers are protected by default. current_user runs first:
    # anonymous requests get 401, not a confusing CSRF 403.
    api_router = APIRouter(
        prefix="/api", dependencies=[Depends(current_user), Depends(csrf_protect)]
    )
    api_router.include_router(documents.router)
    api_router.include_router(taxonomy.router)
    api_router.include_router(jobs.router)
    api_router.include_router(auth.router)
    app.include_router(api_router)
    # Login is the only unauthenticated /api route (and is CSRF-exempt: the
    # session doesn't exist yet, and the password itself proves intent).
    app.include_router(auth.login_router, prefix="/api")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Container healthcheck: no auth, no database access."""
        return {"status": "ok", "version": library.__version__}

    # MCP server (W13): bearer-token-authenticated tools at /mcp/ — see
    # docs/mcp.md. Auth is enforced inside the mounted app (FastMCP bearer
    # middleware running our token verifier), not by the /api dependencies.
    app.mount("/mcp", mcp_http)

    return app
