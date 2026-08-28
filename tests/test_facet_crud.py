"""Vocabulary edits. Renaming is free; merging must survive a label collision."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.vocabulary import (
    ValueInUseError,
    add_alias,
    create_facet,
    create_value,
    delete_value,
    document_labels,
    load_vocabulary,
    merge_values,
    rename_value,
    set_document_label,
)

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _facet_key() -> str:
    return f"crud-{uuid.uuid4().hex[:8]}"


def test_rename_changes_the_label_and_keeps_the_key(api_database_url: str) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await rename_value(session, key, "alpha", "Renamed")

    asyncio.run(_run(api_database_url, _work))
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert facets[key].value("alpha").label == "Renamed"


def test_merge_repoints_labels_and_keeps_the_old_key_as_an_alias(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> int:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await create_value(session, key, "beta", "Beta")
        await set_document_label(session, seeded_document_id, key, "alpha")
        return await merge_values(session, key, "alpha", "beta")

    moved = asyncio.run(_run(api_database_url, _work))
    assert moved == 1
    labels = asyncio.run(_run(api_database_url, lambda s: document_labels(s, seeded_document_id)))
    assert labels[key] == "beta"
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert facets[key].value("alpha") is None
    assert "alpha" in facets[key].value("beta").aliases


def test_deleting_a_value_in_use_is_refused(api_database_url: str, seeded_document_id: int) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await set_document_label(session, seeded_document_id, key, "alpha")

    asyncio.run(_run(api_database_url, _work))
    with pytest.raises(ValueInUseError):
        asyncio.run(_run(api_database_url, lambda s: delete_value(s, key, "alpha")))


def test_an_alias_is_visible_on_the_value(api_database_url: str) -> None:
    key = _facet_key()

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await add_alias(session, key, "alpha", "a-plate-or-misspelling")

    asyncio.run(_run(api_database_url, _work))
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert "a-plate-or-misspelling" in facets[key].value("alpha").aliases


def test_merge_survives_two_values_that_share_an_alias(api_database_url: str) -> None:
    """facet_value_aliases' PK is (facet_value_id, alias), and a merge changes
    facet_value_id — so a shared alias collides. Reachable whenever the two
    values are similar enough to be worth merging."""
    key = _facet_key()

    async def _work(session: AsyncSession) -> int:
        await create_facet(session, key, "Crud")
        await create_value(session, key, "alpha", "Alpha")
        await create_value(session, key, "beta", "Beta")
        await add_alias(session, key, "alpha", "shared-term")
        await add_alias(session, key, "beta", "shared-term")
        return await merge_values(session, key, "alpha", "beta")

    asyncio.run(_run(api_database_url, _work))
    facets = {f.key: f for f in asyncio.run(_run(api_database_url, load_vocabulary))}
    assert facets[key].value("alpha") is None
    assert "shared-term" in facets[key].value("beta").aliases
