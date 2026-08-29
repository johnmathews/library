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


def _two_document_ids(api_database_url: str) -> tuple[int, int]:
    """Two real, distinct documents.id values, ordered (lo, hi)."""

    async def _work(session: AsyncSession) -> tuple[int, int]:
        doc_1 = _new_document()
        doc_2 = _new_document()
        session.add_all([doc_1, doc_2])
        await session.flush()
        return (doc_1.id, doc_2.id)

    first, second = asyncio.run(_run(api_database_url, _work))
    return (first, second) if first < second else (second, first)


def test_an_override_with_doc_a_after_doc_b_is_rejected(api_database_url: str) -> None:
    """doc_a < doc_b is what makes an override pair canonical; a caller that
    hands the pair in the wrong order must be rejected, not silently accepted
    as a second, un-deduplicated row."""
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=hi, doc_b=lo))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))


def test_an_override_pair_cannot_be_recorded_twice_for_the_same_kind(api_database_url: str) -> None:
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))


def test_the_same_pair_is_allowed_under_a_different_kind(api_database_url: str) -> None:
    """The uniqueness is scoped to the (kind, doc_a, doc_b) triple, not just
    the pair: a MERGE and a SPLIT can both exist for the same two documents
    (e.g. a MERGE recorded in error, then a SPLIT undoing it) without one
    blocking the other."""
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        session.add(PaymentOverride(kind="SPLIT", doc_a=lo, doc_b=hi))
        await session.flush()

    asyncio.run(_run(api_database_url, _work))
