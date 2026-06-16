"""Shared test fixtures for the Library backend."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from procrastinate.testing import InMemoryConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from library.app import create_app
from library.auth.deps import CSRF_COOKIE, CSRF_HEADER
from library.auth.passwords import hash_password
from library.config import get_settings
from library.db import get_session
from library.jobs import job_app, procrastinate_conninfo
from library.models import User

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _embedding_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the suite hermetic: embeddings off unless a test opts in.

    The embedding stage reaches for a network sidecar; defaulting it off means
    pipeline tests never make real HTTP calls. Tests exercising embedding set
    ``LIBRARY_EMBEDDING_ENABLED=true`` and monkeypatch the embed call.
    """
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass(frozen=True)
class AuthUser:
    """Credentials of a user created directly in the test database."""

    id: int
    username: str
    password: str


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """A fresh application instance (job queue swapped for an in-memory one)."""
    with job_app.replace_connector(InMemoryConnector()):
        yield create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """An HTTP test client bound to the app fixture."""
    with TestClient(app) as test_client:
        yield test_client


def alembic_config(database_url: str) -> Config:
    """An Alembic Config pointed at this repo's migrations and the given database."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def _create_database(admin_url: str, name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def create_database(admin_url: str, name: str) -> str:
    """Create (or recreate) a database in the test Postgres; return its asyncpg URL."""
    asyncio.run(_create_database(admin_url, name))
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """A real ephemeral Postgres 17 (with pgvector) for integration tests.

    Pinned to ``C.UTF-8`` so text ordering is byte-wise — matching both the
    existing production cluster (C collation) and Python's ``sorted``. The
    Debian-based pgvector image would otherwise default to a glibc linguistic
    collation and silently reorder taxonomy/sender listings. See docs/deployment.md.
    """
    container = PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg").with_env(
        "POSTGRES_INITDB_ARGS", "--locale=C.UTF-8"
    )
    with container:
        yield container


@pytest.fixture(scope="session")
def admin_database_url(postgres_container: PostgresContainer) -> str:
    """asyncpg URL for the container's default database (used to create others)."""
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database_url(admin_database_url: str) -> str:
    """A dedicated database migrated to head, shared by model-level tests."""
    url = create_database(admin_database_url, "library_models")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def api_database_url(admin_database_url: str) -> str:
    """A dedicated database migrated to head for API-level integration tests."""
    url = create_database(admin_database_url, "library_api")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def api_app(
    api_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """An app wired to the test database, with data_dir pointed at tmp_path.

    The session dependency is overridden with a NullPool engine created
    lazily inside the app's event loop (TestClient runs the app in its own
    loop, and asyncpg connections are loop-bound).
    """
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    # TestClient speaks plain http://testserver; Secure cookies would never
    # be sent back, so tests run with the dev override.
    monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    application = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session
        finally:
            await engine.dispose()

    application.dependency_overrides[get_session] = override_session
    yield application
    get_settings.cache_clear()


@pytest.fixture
async def job_connector() -> AsyncIterator[InMemoryConnector]:
    """An open in-memory Procrastinate connector.

    Needed by tests that drive pipeline code directly (``advance_pipeline``
    defers the thumbnail job after OCR); deferred jobs can be inspected via
    ``connector.jobs``.
    """
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        async with job_app.open_async():
            yield connector


async def _insert_user(
    database_url: str, username: str, password: str, *, is_active: bool = True
) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(
                username=username, password_hash=hash_password(password), is_active=is_active
            )
            session.add(user)
            await session.commit()
            return user.id
    finally:
        await engine.dispose()


def create_user(
    database_url: str,
    username: str | None = None,
    password: str = "correct horse battery staple",
    *,
    is_active: bool = True,
) -> AuthUser:
    """Insert a user (random unique username by default) from sync test code."""
    name = username or f"user-{uuid.uuid4().hex[:12]}"
    user_id = asyncio.run(_insert_user(database_url, name, password, is_active=is_active))
    return AuthUser(id=user_id, username=name, password=password)


@pytest.fixture
def auth_user(api_database_url: str) -> AuthUser:
    """A fresh active user in the API test database."""
    return create_user(api_database_url)


def login(client: TestClient, user: AuthUser) -> None:
    """Log the client in as the given user and arm it for CSRF checks.

    Sets the ``X-CSRF-Token`` default header from the ``library_csrftoken``
    cookie so state-changing requests pass the double-submit check without
    per-test ceremony.
    """
    response = client.post(
        "/api/auth/login", json={"username": user.username, "password": user.password}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]


@pytest.fixture
def api_client(
    api_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> Iterator[TestClient]:
    """Authenticated HTTP client against api_app with a real Procrastinate connector.

    Logged in as ``auth_user`` via the session cookie, with the CSRF header
    pre-set. Deferred jobs land in the test database's procrastinate_jobs
    table, so tests can assert on real queue rows.
    """
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as test_client:
        login(test_client, auth_user)
        yield test_client


@pytest.fixture
def anon_client(api_app: FastAPI, api_database_url: str) -> Iterator[TestClient]:
    """Unauthenticated HTTP client against api_app (same wiring as api_client)."""
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as test_client:
        yield test_client


async def _fetch_all(database_url: str, query: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query), params)
            return [tuple(row) for row in result.all()]
    finally:
        await engine.dispose()


def fetch_all(database_url: str, query: str, **params: object) -> list[tuple[object, ...]]:
    """Run a query against the given database from sync test code."""
    return asyncio.run(_fetch_all(database_url, query, **params))
