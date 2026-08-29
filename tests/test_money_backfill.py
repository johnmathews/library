"""Selection for the amount backfill. The model call itself is not exercised."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import AmountKind, Document, DocumentSource, DocumentStatus
from library.money.backfill import documents_needing_amount_kind

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


def _seed(database_url: str, rows: list[tuple[Decimal | None, AmountKind | None]]) -> list[int]:
    async def _work(session: AsyncSession) -> list[int]:
        ids: list[int] = []
        for amount, kind in rows:
            marker = f"backfill:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                amount_total=amount,
                amount_kind=kind,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def test_only_amount_bearing_documents_without_a_kind_are_selected(
    api_database_url: str,
) -> None:
    needs, has_kind, no_amount = _seed(
        api_database_url,
        [
            (Decimal("10.00"), None),
            (Decimal("20.00"), AmountKind.PAYMENT_MADE),
            (None, None),
        ],
    )
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=None))
    )
    assert needs in selected
    assert has_kind not in selected
    assert no_amount not in selected


def test_the_limit_is_respected(api_database_url: str) -> None:
    _seed(api_database_url, [(Decimal("1.00"), None), (Decimal("2.00"), None)])
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=1))
    )
    assert len(selected) == 1
