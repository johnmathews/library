"""FastAPI application factory for the Library backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

import library
from library.api import documents, jobs
from library.jobs import job_app

API_DESCRIPTION = """\
Self-hosted document archive. Upload scans and files, let OCR and Claude
extraction make them searchable, then find them again with full-text search
(Dutch and English stemming), filters, and downloads.

The REST API is a first-class product surface — everything the web app can
do is available here. See `docs/api.md` in the repository for the narrative
documentation.

**No authentication yet** (arrives in W8): do not expose beyond a trusted
network.
"""

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "documents",
        "description": (
            "Upload, search, read, edit, and soft-delete documents; download "
            "originals, searchable PDFs, and thumbnails."
        ),
    },
    {
        "name": "jobs",
        "description": "Visibility into the background processing queue (OCR, "
        "extraction, thumbnails).",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the Procrastinate connection for the app's lifetime so defer() works."""
    async with job_app.open_async():
        yield


def create_app() -> FastAPI:
    """Build and return the Library FastAPI application."""
    app = FastAPI(
        title="Library",
        version=library.__version__,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    api_router = APIRouter(prefix="/api")
    api_router.include_router(documents.router)
    api_router.include_router(jobs.router)
    app.include_router(api_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Container healthcheck: no auth, no database access."""
        return {"status": "ok", "version": library.__version__}

    return app
