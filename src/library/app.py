"""FastAPI application factory for the Library backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

import library
from library.api import documents, jobs
from library.jobs import job_app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the Procrastinate connection for the app's lifetime so defer() works."""
    async with job_app.open_async():
        yield


def create_app() -> FastAPI:
    """Build and return the Library FastAPI application."""
    app = FastAPI(title="Library", version=library.__version__, lifespan=lifespan)

    api_router = APIRouter(prefix="/api")
    api_router.include_router(documents.router)
    api_router.include_router(jobs.router)
    app.include_router(api_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Container healthcheck: no auth, no database access."""
        return {"status": "ok", "version": library.__version__}

    return app
