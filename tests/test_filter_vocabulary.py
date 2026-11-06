"""``unknown_filter_values`` against a real archive: every filter kind."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.filter_vocabulary import unknown_filter_values
from library.search import DocumentFilters
from tests.test_documents_api import _seed_document
from tests.test_mcp import _label

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _open_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_filter_values_covers_every_filter_kind(api_database_url: str) -> None:
    t = f"fv-{secrets.token_hex(4)}"
    document = await _seed_document(
        api_database_url,
        t,
        kind_slug="receipt",
        tag_slugs=[t],
        project_slugs=[f"{t}-p"],
        matter_slugs=[f"{t}-m"],
    )
    await _label(api_database_url, document, f"{t}-f", "known")

    async with _open_session(api_database_url) as session:
        clean = await unknown_filter_values(
            session,
            DocumentFilters(
                kind_slug="receipt",
                tag_slugs=(t,),
                project_slugs=(f"{t}-p",),
                matter_slugs=(f"{t}-m",),
                facets={f"{t}-f": "known"},
            ),
        )
        nothing = await unknown_filter_values(session, DocumentFilters())
        concept = await unknown_filter_values(session, DocumentFilters(kind_slug=" Energy"))
        plain = await unknown_filter_values(session, DocumentFilters(kind_slug="nonsense"))
        everything = await unknown_filter_values(
            session,
            DocumentFilters(
                tag_slugs=(t, f"{t}-t2"),
                project_slugs=(f"{t}-p2",),
                matter_slugs=(f"{t}-m2",),
                facets={f"{t}-f": "other", f"{t}-g": "known"},
            ),
        )

    assert not clean and not nothing
    assert concept.kind == " Energy" and concept.kind_suggestion == "utility-bill"
    assert plain.kind == "nonsense" and plain.kind_suggestion is None
    assert everything.kind is None
    assert everything.others == [
        f"unknown tag '{t}-t2'",
        f"unknown project '{t}-p2'",
        f"unknown matter '{t}-m2'",
        f"unknown facet value {t}-f='other'",
        f"unknown facet '{t}-g'",
    ]
