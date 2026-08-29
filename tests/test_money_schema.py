"""The money-facts columns and the overrides table."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import (
    AmountKind,
    Document,
    DocumentSource,
    DocumentStatus,
    PaymentOverride,
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


def _new_document(**kwargs: object) -> Document:
    marker = f"money:{uuid.uuid4()}"
    return Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        status=DocumentStatus.INDEXED,
        title=marker,
        **kwargs,
    )


def test_amount_kind_and_reference_round_trip(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> int:
        doc = _new_document(amount_kind=AmountKind.PAYMENT_MADE, reference="ABC-123")
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document).where(Document.id == document_id)),
        )
    ).scalar_one()
    assert stored.amount_kind is AmountKind.PAYMENT_MADE
    assert stored.reference == "ABC-123"


def test_amount_kind_defaults_to_null_not_to_a_payment(api_database_url: str) -> None:
    """NULL means 'not yet decided'. Task 4 treats it as not summable, so an
    un-backfilled archive under-reports rather than over-reports."""

    async def _work(session: AsyncSession) -> int:
        doc = _new_document()
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document).where(Document.id == document_id)),
        )
    ).scalar_one()
    assert stored.amount_kind is None


def test_an_override_kind_outside_merge_or_split_is_rejected(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="NONSENSE", doc_a=1, doc_b=2))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))
