"""The backfill selects the right documents. The model itself is stubbed."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.backfill import documents_needing_labels
from library.facets.vocabulary import create_facet, create_value, set_document_label

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


def test_an_unlabelled_document_is_selected(api_database_url: str, seeded_document_id: int) -> None:
    ids = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    assert seeded_document_id in ids


def test_a_labelled_document_is_skipped_unless_relabelling(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"backfill-{uuid.uuid4().hex[:8]}"

    async def _label(session: AsyncSession) -> None:
        await create_facet(session, key, "Backfill")
        await create_value(session, key, "alpha", "Alpha")
        await set_document_label(session, seeded_document_id, key, "alpha")

    asyncio.run(_run(api_database_url, _label))

    skipped = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    included = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=True, limit=None))
    )
    assert seeded_document_id not in skipped
    assert seeded_document_id in included
