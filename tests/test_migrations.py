"""Migration round-trip tests against a real ephemeral Postgres 17."""

import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import alembic_config, create_database, fetch_all

pytestmark = pytest.mark.integration

EXPECTED_TABLES: set[str] = {
    "users",
    "sessions",
    "api_tokens",
    "kinds",
    "senders",
    "tags",
    "document_tags",
    "documents",
    "document_chunks",
    "document_pages",
    "ingestion_events",
    "ask_threads",
    "ask_turns",
}

PROCRASTINATE_TABLES: set[str] = {
    "procrastinate_jobs",
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
}

EXPECTED_KIND_SLUGS: set[str] = {
    "invoice",
    "receipt",
    "certificate",
    "utility-bill",
    "parking-ticket",
    "warranty",
    "manual",
    "reference",
    "research",
    "note",
    "letter",
    "contract",
    "ticket",
    "other",
}


async def _fetch_scalars(database_url: str, query: str) -> list[object]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query))
            return list(result.scalars())
    finally:
        await engine.dispose()


def _table_names(database_url: str) -> set[str]:
    rows = asyncio.run(
        _fetch_scalars(
            database_url,
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()",
        )
    )
    return {str(row) for row in rows}


def test_upgrade_downgrade_upgrade_cycle(admin_database_url: str) -> None:
    """upgrade head -> downgrade base -> upgrade head runs clean on an empty database."""
    url = create_database(admin_database_url, "library_migrations")
    config = alembic_config(url)

    command.upgrade(config, "head")
    tables = _table_names(url)
    assert tables >= EXPECTED_TABLES
    assert tables >= PROCRASTINATE_TABLES

    command.downgrade(config, "base")
    leftover = _table_names(url) - {"alembic_version"}
    assert leftover == set(), f"downgrade base left tables behind: {leftover}"

    command.upgrade(config, "head")
    assert _table_names(url) >= EXPECTED_TABLES | PROCRASTINATE_TABLES


def test_kinds_seeded(migrated_database_url: str) -> None:
    slugs = asyncio.run(_fetch_scalars(migrated_database_url, "SELECT slug FROM kinds"))
    assert len(slugs) == 14
    assert {str(slug) for slug in slugs} == EXPECTED_KIND_SLUGS


def test_new_kinds_seeded(migrated_database_url: str) -> None:
    slugs = asyncio.run(_fetch_scalars(migrated_database_url, "SELECT slug FROM kinds"))
    assert {"reference", "research", "note"} <= {str(slug) for slug in slugs}


def test_fts_indexes_exist(migrated_database_url: str) -> None:
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'documents'",
        )
    )
    names = {str(name) for name in indexes}
    assert "ix_documents_search_vector_nl" in names
    assert "ix_documents_search_vector_en" in names


def test_vector_extension_enabled(migrated_database_url: str) -> None:
    extensions = asyncio.run(
        _fetch_scalars(migrated_database_url, "SELECT extname FROM pg_extension")
    )
    assert "vector" in {str(name) for name in extensions}


def test_document_chunks_indexes_exist(migrated_database_url: str) -> None:
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'document_chunks'",
        )
    )
    names = {str(name) for name in indexes}
    assert "ix_document_chunks_embedding" in names
    assert "ix_document_chunks_document_id" in names


def test_users_have_preferences_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'preferences'
        """,
    )
    assert rows == [("preferences", "jsonb", "NO")]


def test_document_chunks_have_page_number_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'page_number'
        """,
    )
    assert rows == [("page_number", "integer", "YES")]


def test_documents_have_topics_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'topics'
        """,
    )
    assert rows == [("topics", "jsonb", "YES")]


def test_ask_turns_has_messages_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'ask_turns' AND column_name = 'messages'
        """,
    )
    assert rows == [("messages", "jsonb", "NO")]


def test_ask_logs_table_is_gone(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        "SELECT tablename FROM pg_tables WHERE tablename = 'ask_logs'",
    )
    assert rows == []
