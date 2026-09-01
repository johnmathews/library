"""The seed vocabulary: what ships, and that seeding twice changes nothing."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.seed import SEED_VOCABULARY, seed_vocabulary
from library.facets.vocabulary import load_vocabulary

pytestmark = pytest.mark.integration


async def _seed_twice(database_url: str) -> tuple[int, int]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            first = await seed_vocabulary(session)
            await session.commit()
            second = await seed_vocabulary(session)
            await session.commit()
            return first, second
    finally:
        await engine.dispose()


async def _load(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await load_vocabulary(session)
    finally:
        await engine.dispose()


def test_seeding_is_idempotent(api_database_url: str) -> None:
    first, second = asyncio.run(_seed_twice(api_database_url))
    assert first > 0
    assert second == 0


def test_seeded_facets_and_values_are_present(api_database_url: str) -> None:
    asyncio.run(_seed_twice(api_database_url))
    facets = {f.key: f for f in asyncio.run(_load(api_database_url))}
    assert {"category", "scope", "cost_type", "vehicle", "property", "person"} <= facets.keys()
    assert {"business", "personal"} == {v.key for v in facets["scope"].values}
    assert "accountancy" in {v.key for v in facets["category"].values}
    assert "accounting" in facets["category"].value("accountancy").aliases


def test_personal_facets_ship_with_no_values(api_database_url: str) -> None:
    """vehicle/property/person values name real people and things; they are
    created at runtime, never committed to a public repository."""
    asyncio.run(_seed_twice(api_database_url))
    facets = {f.key: f for f in asyncio.run(_load(api_database_url))}
    for key in ("vehicle", "property", "person"):
        assert facets[key].values == ()


def test_no_seed_value_key_repeats_within_a_facet() -> None:
    for facet in SEED_VOCABULARY:
        keys = [value.key for value in facet.values]
        assert len(keys) == len(set(keys)), f"duplicate value key in {facet.key}"
