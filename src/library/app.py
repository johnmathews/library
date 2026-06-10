"""FastAPI application factory for the Library backend."""

from fastapi import APIRouter, FastAPI

import library

# All application endpoints live under /api; later work units add routes here.
api_router: APIRouter = APIRouter(prefix="/api")


def create_app() -> FastAPI:
    """Build and return the Library FastAPI application."""
    app = FastAPI(title="Library", version=library.__version__)
    app.include_router(api_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Container healthcheck: no auth, no database access."""
        return {"status": "ok", "version": library.__version__}

    return app
