"""Vocabulary reads and writes. No LLM in this module, by design."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    document_labels,
    load_vocabulary,
    set_document_label,
)
from library.models import Facet, FacetValue, FacetValueAlias

pytestmark = pytest.mark.integration


async def _seed_vocab(database_url: str, facet_key: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            facet = Facet(key=facet_key, label="Scope")
            session.add(facet)
            await session.flush()
            business = FacetValue(facet_id=facet.id, key="business", label="Business")
            personal = FacetValue(facet_id=facet.id, key="personal", label="Personal")
            session.add_all([business, personal])
            await session.flush()
            session.add(FacetValueAlias(facet_value_id=business.id, alias="work"))
            await session.commit()
    finally:
        await engine.dispose()


async def _with_session[T](database_url: str, work) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def test_load_vocabulary_carries_values_and_aliases(api_database_url: str) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))
    facets = asyncio.run(_with_session(api_database_url, load_vocabulary))
    facet = next(f for f in facets if f.key == key)
    assert {v.key for v in facet.values} == {"business", "personal"}
    assert facet.value("business").aliases == ("work",)
    assert facet.value("nope") is None


def test_set_and_read_a_label(api_database_url: str, seeded_document_id: int) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")

    asyncio.run(_with_session(api_database_url, _set))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert labels[key] == "business"


def test_setting_a_second_value_replaces_rather_than_duplicates(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set_twice(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")
        await set_document_label(session, seeded_document_id, key, "personal")

    asyncio.run(_with_session(api_database_url, _set_twice))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert labels[key] == "personal"


def test_clearing_a_label_removes_it(api_database_url: str, seeded_document_id: int) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _set_then_clear(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "business")
        await set_document_label(session, seeded_document_id, key, None)

    asyncio.run(_with_session(api_database_url, _set_then_clear))
    labels = asyncio.run(
        _with_session(api_database_url, lambda s: document_labels(s, seeded_document_id))
    )
    assert key not in labels


def test_unknown_facet_and_unknown_value_raise(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"scope-{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_vocab(api_database_url, key))

    async def _bad_facet(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, "no-such-facet", "business")

    async def _bad_value(session: AsyncSession) -> None:
        await set_document_label(session, seeded_document_id, key, "no-such-value")

    with pytest.raises(UnknownFacetError):
        asyncio.run(_with_session(api_database_url, _bad_facet))
    with pytest.raises(UnknownValueError):
        asyncio.run(_with_session(api_database_url, _bad_value))
