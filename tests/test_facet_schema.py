"""The facet schema's guarantees, asserted by trying to violate them.

Every constraint here exists because breaking it corrupts a GROUP BY silently
rather than loudly: a document with two values of one facet double-counts, and
a label pointing at another facet's value groups under the wrong heading.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Facet, FacetValue

pytestmark = pytest.mark.integration


async def _seed_two_facets(database_url: str, tag: str) -> tuple[int, int, int]:
    """Create two facets, one value each. Returns (facet_a, value_a, value_b)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            fa = Facet(key=f"a-{tag}", label="A")
            fb = Facet(key=f"b-{tag}", label="B")
            session.add_all([fa, fb])
            await session.flush()
            va = FacetValue(facet_id=fa.id, key="one", label="One")
            vb = FacetValue(facet_id=fb.id, key="two", label="Two")
            session.add_all([va, vb])
            await session.flush()
            await session.commit()
            return fa.id, va.id, vb.id
    finally:
        await engine.dispose()


async def _insert_label(database_url: str, document_id: int, facet_id: int, value_id: int) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.execute(
                text(
                    "INSERT INTO document_labels (document_id, facet_id, facet_value_id) "
                    "VALUES (:d, :f, :v)"
                ),
                {"d": document_id, "f": facet_id, "v": value_id},
            )
            await session.commit()
    finally:
        await engine.dispose()


def test_a_label_cannot_point_at_another_facets_value(
    api_database_url: str, seeded_document_id: int
) -> None:
    tag = uuid.uuid4().hex[:8]
    facet_a, _value_a, value_b = asyncio.run(_seed_two_facets(api_database_url, tag))
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_b))


def test_a_document_cannot_hold_two_values_of_one_facet(
    api_database_url: str, seeded_document_id: int
) -> None:
    tag = uuid.uuid4().hex[:8]
    facet_a, value_a, _value_b = asyncio.run(_seed_two_facets(api_database_url, tag))
    asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_a))
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_label(api_database_url, seeded_document_id, facet_a, value_a))
