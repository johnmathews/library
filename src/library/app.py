"""FastAPI application factory for the Library backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import library
from library.api import (
    admin,
    ask,
    auth,
    charts,
    comments,
    documents,
    events,
    facets,
    held_emails,
    jobs,
    matters,
    notes,
    payments,
    projects,
    saved_views,
    series,
    settings,
    taxonomy,
)
from library.auth.deps import csrf_protect, current_user, require_admin
from library.config import Settings, get_settings
from library.events_broker import EventsBroker
from library.jobs import job_app, procrastinate_conninfo
from library.mcp_server import create_mcp_http_app

logger = logging.getLogger(__name__)

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
        "name": "notes",
        "description": (
            "Author Markdown notes inside Library, edit them in place, and "
            "browse or restore their version history."
        ),
    },
    {
        "name": "comments",
        "description": (
            "Free-text, dated comments attached to an existing document; each "
            "edit re-queues embedding so /ask can find the document through it."
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
        "name": "projects",
        "description": (
            "Projects/collections grouping documents (many-to-many, "
            "soft-archive) with document counts — the values for the "
            "document `?project=` filter and membership edits."
        ),
    },
    {
        "name": "matters",
        "description": (
            "Business-matter categories grouping documents by subject "
            "(many-to-many, soft-archive) with document counts — the values "
            "for the document `?matter=` filter and membership edits."
        ),
    },
    {
        "name": "jobs",
        "description": "Visibility into the background processing queue (OCR, "
        "extraction, thumbnails).",
    },
    {
        "name": "held-emails",
        "description": (
            "Emails the mailbox poller held for review instead of auto-filing: "
            "list them, inspect the decision trace, ingest anyway, or dismiss."
        ),
    },
    {"name": "settings", "description": "Per-user display preferences."},
    {
        "name": "ask",
        "description": (
            "Natural-language question answering over the archive: semantic "
            "retrieval + structured aggregation, answered with citations."
        ),
    },
]


# Path heads that belong to the backend, never the SPA. A request for an
# unknown path under these gets the normal JSON 404, not index.html.
_BACKEND_PREFIXES = frozenset(
    # "metrics" belongs here for the same reason as "healthz": without it the
    # SPA catch-all below answers a Prometheus scrape with index.html, which
    # is a 200 — so the scrape looks healthy while collecting nothing.
    {"api", "mcp", "healthz", "metrics", "docs", "redoc", "openapi.json"}
)


def warn_if_no_public_base_url(public_base_url: str | None) -> None:
    """Log a startup warning when document deep-links are disabled.

    Pushover notifications only link back to a document when
    LIBRARY_PUBLIC_BASE_URL is set (see notifications.py). When it is unset
    the feature silently no-ops, which looks like a bug ("my notifications
    have no link") — so we say so loudly, once, at startup.
    """
    if not public_base_url:
        logger.warning(
            "LIBRARY_PUBLIC_BASE_URL is unset — Pushover notifications will not "
            "include a link to the document. Set it to the web app's public URL "
            "(e.g. https://library.example.com) to enable deep-links."
        )


class HashedStaticFiles(StaticFiles):
    """Vite's content-hashed bundles under /assets never change: cache forever."""

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built Vue SPA (production mode — see docs/deployment.md §1.3).

    /assets (content-hashed) is served immutable; every other non-backend
    path serves the real file when one exists (manifest, icons, favicon)
    and falls back to index.html (no-cache, so deploys take effect) for
    client-side routes like /documents/42.
    """
    dist = dist.resolve()
    index_file = dist / "index.html"
    app.mount("/assets", HashedStaticFiles(directory=dist / "assets"), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        head = path.split("/", 1)[0]
        if head in _BACKEND_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / path).resolve() if path else None
        if (
            candidate is not None
            and candidate.is_relative_to(dist)  # no traversal out of dist
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


def _configure_telemetry_from_settings(settings: Settings) -> None:
    """Install the OTel MeterProvider from configuration, at startup.

    Called from the lifespan rather than at import so that tests and CLI entry
    points that merely import the app do not install a global provider. It is
    idempotent, so a second app instance in the same process is harmless.
    """
    from library.telemetry import configure_telemetry

    configure_telemetry(
        service_name="library",
        service_version=library.__version__,
        prometheus_enabled=settings.otel_metrics_enabled,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        otlp_headers=settings.otel_exporter_otlp_headers,
        export_interval_ms=settings.otel_metric_export_interval_ms,
    )


def create_app() -> FastAPI:
    """Build and return the Library FastAPI application."""
    # Built per app instance: the MCP ASGI app's session manager is created
    # and torn down by its lifespan, which we run inside our own so the
    # mounted /mcp transport shares the application's lifetime.
    mcp_http = create_mcp_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the Procrastinate connection (so defer() works), start the
        process-wide SSE events broker (one shared Postgres LISTEN connection
        fanned out to all clients), and run the mounted MCP app's lifespan."""
        warn_if_no_public_base_url(get_settings().public_base_url)
        _configure_telemetry_from_settings(get_settings())
        broker = EventsBroker(procrastinate_conninfo(get_settings().database_url))
        await broker.start()
        app.state.events_broker = broker
        try:
            async with job_app.open_async(), mcp_http.lifespan(mcp_http):
                yield
        finally:
            await broker.stop()

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
    #
    # NOTE (deliberate authorization model): authentication is the ONLY gate on
    # the library. There is intentionally no per-user resource ownership — beyond
    # the admin/non-admin split, any authenticated user has full read+write access
    # to the entire library (every document, note, comment, tag, project, series).
    # `uploader_id`/`author_id` are provenance, not authz. This is a single-family
    # shared library by design; endpoints do NOT check the caller against a
    # resource's creator, and that is not a bug. See docs/architecture.md §1.5.1.
    api_router = APIRouter(
        prefix="/api", dependencies=[Depends(current_user), Depends(csrf_protect)]
    )
    api_router.include_router(documents.router)
    api_router.include_router(notes.router)
    api_router.include_router(payments.router)
    api_router.include_router(comments.router)
    api_router.include_router(charts.router)
    api_router.include_router(series.router)
    api_router.include_router(facets.router)
    api_router.include_router(taxonomy.router)
    api_router.include_router(projects.router)
    api_router.include_router(matters.router)
    api_router.include_router(saved_views.router)
    api_router.include_router(jobs.router)
    api_router.include_router(held_emails.router)
    api_router.include_router(events.router)
    api_router.include_router(settings.router)
    api_router.include_router(ask.router)
    api_router.include_router(auth.router)
    # Admin surface: additionally gated by require_admin (which layers on the
    # already-attached current_user, so anon → 401, non-admin → 403).
    api_router.include_router(admin.router, dependencies=[Depends(require_admin)])
    app.include_router(api_router)
    # Login is the only unauthenticated /api route (and is CSRF-exempt: the
    # session doesn't exist yet, and the password itself proves intent).
    app.include_router(auth.login_router, prefix="/api")

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        """Prometheus scrape target. Unauthenticated, like /healthz.

        Returns 404 when metrics are disabled rather than an empty 200: an
        empty exposition is indistinguishable from "the app is running and
        recording nothing", and a scrape that silently collects zero series is
        the failure this endpoint exists to make visible.

        Deliberately carries no per-user or per-document labels — see the
        cardinality and privacy notes in `library.telemetry`.
        """
        if not get_settings().otel_metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics are not enabled")
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    def healthz() -> dict[str, str | None]:
        """Container healthcheck: no auth, no database access.

        ``git_sha`` is the commit baked into the image at build time (CI's
        ``--build-arg GIT_SHA``); ``None`` in dev / unbuilt contexts. Exposed
        here, unauthenticated, so a deploy can be verified with a single curl:
        compare it to the commit CI promoted (see docs/deployment.md).
        """
        settings = get_settings()
        payload: dict[str, str | None] = {
            "status": "ok",
            "version": library.__version__,
            "git_sha": settings.git_sha,
        }
        # Only meaningful where subscription credentials are actually in play. A
        # dead refresh token cannot self-heal — it needs a human — and without
        # this the symptom is every question failing while /healthz says "ok".
        #
        # Keyed on the credentials *existing*, not on the configured backend.
        # The live backend is an instance setting resolved from the database
        # (library.llm.backends), and this endpoint is deliberately DB-free — so
        # reading `settings.ask_llm_backend` here reported on the environment
        # default while the toggle said otherwise, and the alarm stayed silent
        # for exactly the deployments that enabled the backend through the UI.
        # Presence is the right trigger anyway: credentials on disk are there to
        # be used, and the write-time guard means a surface cannot be switched
        # to `subscription` without them.
        from library.llm.oauth import credentials_path, token_health

        if credentials_path(settings.claude_config_dir).exists() or "subscription" in (
            settings.ask_llm_backend,
            settings.series_insight_llm_backend,
        ):
            status, detail = token_health(settings.claude_config_dir)
            payload["claude_credentials"] = status
            payload["claude_credentials_detail"] = detail
            if status != "healthy":
                payload["status"] = "degraded"
        # The photo OCR weights (GH #109). They ship in the image under
        # models/ocr/, but a file read at run time from inside the image needs
        # a Dockerfile COPY to actually be there, and this is the class of
        # absence that otherwise stays invisible: photo.get_engine() is lazy
        # and lru_cached, so a deploy with no weights passes every healthcheck
        # and only fails on the first JPEG/PNG/HEIC someone uploads, hours
        # later, one document at a time. Report it at deploy time instead.
        #
        # Existence only, never checksums — ~13 MB of hashing does not belong
        # on an unauthenticated endpoint polled every 10 seconds by the
        # container healthcheck. `scripts/fetch_ocr_models.py --check` is the
        # checksum-verifying counterpart, and compose-smoke runs it.
        from library.ocr.weights import missing_models

        missing = missing_models()
        payload["ocr_models"] = "missing" if missing else "ok"
        if missing:
            payload["ocr_models_detail"] = "not found: " + ", ".join(
                str(model.path) for model in missing
            )
            payload["status"] = "degraded"
        return payload

    # MCP server (W13): bearer-token-authenticated tools at /mcp/ — see
    # docs/mcp.md. Auth is enforced inside the mounted app (FastMCP bearer
    # middleware running our token verifier), not by the /api dependencies.
    app.mount("/mcp", mcp_http)

    # Production frontend (W17): when a built SPA is present (the Docker
    # image bakes it into /app/frontend/dist), serve it from this process.
    # Registered last, so every /api, /mcp, /healthz and /docs route above
    # wins; without a build (dev: Vite proxies /api) nothing is mounted.
    dist = get_settings().frontend_dist
    if (dist / "index.html").is_file():
        _mount_spa(app, dist)

    return app
