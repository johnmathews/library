"""Shared test fixtures for the Library backend."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from library.app import create_app

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@pytest.fixture
def app() -> FastAPI:
    """A fresh application instance for each test."""
    return create_app()


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
    """A real ephemeral Postgres 17 for integration tests."""
    with PostgresContainer("postgres:17-alpine", driver="asyncpg") as container:
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
