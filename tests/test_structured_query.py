"""Tests for structured/analytical queries over extracted metadata."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import Document, DocumentSource, Kind, Sender
from library.search import DocumentFilters
from library.structured_query import (
    distinct_senders,
    list_documents,
    query_documents,
    sum_amount,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Document))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


async def _sender(session: AsyncSession, name: str) -> Sender:
    existing = (
        await session.execute(select(Sender).where(Sender.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


async def seed(
    session: AsyncSession,
    marker: str,
    *,
    sender_name: str | None = None,
    kind_slug: str | None = None,
    document_date: date | None = None,
    amount: str | None = None,
    currency: str | None = None,
) -> int:
    sender = await _sender(session, sender_name) if sender_name else None
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=document_date,
        amount_total=Decimal(amount) if amount is not None else None,
        currency=currency,
    )
    session.add(document)
    await session.commit()
    return document.id


async def test_distinct_senders_ranked_by_document_count(session: AsyncSession) -> None:
    await seed(session, "v1", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "v2", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "e1", sender_name="Eneco", kind_slug="utility-bill")

    groups = await distinct_senders(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert [(group.sender, group.document_count) for group in groups] == [
        ("Vattenfall", 2),
        ("Eneco", 1),
    ]
    assert all(group.document_ids for group in groups)


async def test_distinct_senders_honours_date_window(session: AsyncSession) -> None:
    """'Who was my energy provider last year?' — filter kind + date range."""
    await seed(
        session,
        "old",
        sender_name="OldEnergy",
        kind_slug="utility-bill",
        document_date=date(2024, 6, 1),
    )
    await seed(
        session,
        "new",
        sender_name="NewEnergy",
        kind_slug="utility-bill",
        document_date=date(2025, 6, 1),
    )

    groups = await distinct_senders(
        session,
        filters=DocumentFilters(
            kind_slug="utility-bill", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
        ),
    )

    assert [group.sender for group in groups] == ["NewEnergy"]


async def test_sum_amount_groups_by_currency(session: AsyncSession) -> None:
    await seed(session, "a", kind_slug="invoice", amount="100.00", currency="EUR")
    await seed(session, "b", kind_slug="invoice", amount="50.50", currency="EUR")
    await seed(session, "c", kind_slug="invoice", amount="10.00", currency="USD")

    groups = await sum_amount(session, filters=DocumentFilters(kind_slug="invoice"))

    totals = {(group.currency, group.total) for group in groups}
    assert totals == {("EUR", "150.50"), ("USD", "10.00")}


async def test_sum_amount_grouped_by_sender(session: AsyncSession) -> None:
    await seed(session, "s1", sender_name="Acme", amount="20.00", currency="EUR")
    await seed(session, "s2", sender_name="Acme", amount="30.00", currency="EUR")
    await seed(session, "s3", sender_name="Globex", amount="5.00", currency="EUR")

    groups = await sum_amount(session, filters=DocumentFilters(), group_by="sender")

    by_sender = {group.key: group.total for group in groups}
    assert by_sender == {"Acme": "50.00", "Globex": "5.00"}


async def test_list_documents_newest_first(session: AsyncSession) -> None:
    older = await seed(session, "older", document_date=date(2024, 1, 1))
    newer = await seed(session, "newer", document_date=date(2025, 1, 1))

    refs = await list_documents(session, filters=DocumentFilters())

    assert [ref.id for ref in refs[:2]] == [newer, older]


async def test_query_documents_dispatch_distinct_senders(session: AsyncSession) -> None:
    await seed(session, "q1", sender_name="Vattenfall", kind_slug="utility-bill")

    result = await query_documents(
        session, filters=DocumentFilters(kind_slug="utility-bill"), aggregate="distinct_senders"
    )

    assert result["result_type"] == "distinct_senders"
    assert result["rows"][0]["sender"] == "Vattenfall"
