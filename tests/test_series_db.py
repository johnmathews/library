"""Integration tests for summarize_series over seeded documents."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import Document, DocumentSource, Kind, Sender
from library.search import DocumentFilters
from library.series import serialise_summary, summarize_series

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
    sender_name: str,
    kind_slug: str,
    document_date: date,
    amount: str,
    currency: str = "EUR",
) -> int:
    sender = await _sender(session, sender_name)
    kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=document_date,
        amount_total=Decimal(amount),
        currency=currency,
    )
    session.add(document)
    await session.commit()
    return document.id


def _settings() -> Settings:
    return Settings(series_min_documents=3, series_typical_pct=0.10, series_flat_pct=0.05)


async def test_summarize_ok_latest_reference(session: AsyncSession) -> None:
    await seed(
        session,
        "j1",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 1, 3),
        amount="100.00",
    )
    await seed(
        session,
        "f1",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 2, 2),
        amount="100.00",
    )
    await seed(
        session,
        "m1",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 3, 4),
        amount="130.00",
    )

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    assert summary.status == "ok"
    assert summary.sender == "Vattenfall"
    assert summary.count == 3
    assert summary.reference is not None
    assert summary.reference.value == Decimal("130.00")
    assert summary.reference.verdict == "higher"
    assert summary.cadence == "monthly"
    assert summary.currency == "EUR"


async def test_summarize_insufficient(session: AsyncSession) -> None:
    await seed(
        session,
        "only",
        sender_name="Eneco",
        kind_slug="utility-bill",
        document_date=date(2025, 1, 1),
        amount="50.00",
    )
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="eneco"),
        settings=_settings(),
    )
    assert summary.status == "insufficient"
    assert summary.count == 1


async def test_summarize_picks_dominant_currency(session: AsyncSession) -> None:
    for i, amt in enumerate(["100.00", "100.00", "100.00"]):
        await seed(
            session,
            f"eur{i}",
            sender_name="Acme",
            kind_slug="invoice",
            document_date=date(2025, 1, i + 1),
            amount=amt,
            currency="EUR",
        )
    await seed(
        session,
        "usd",
        sender_name="Acme",
        kind_slug="invoice",
        document_date=date(2025, 1, 9),
        amount="999.00",
        currency="USD",
    )
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="invoice", sender_contains="acme"),
        settings=_settings(),
    )
    assert summary.status == "ok"
    assert summary.currency == "EUR"
    assert summary.other_currencies == ["USD"]
    assert summary.count == 3  # USD doc excluded from the EUR bucket


async def test_serialise_summary_shape(session: AsyncSession) -> None:
    oldest_id = await seed(
        session,
        "a",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 1, 3),
        amount="100.00",
    )
    await seed(
        session,
        "b",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 2, 2),
        amount="100.00",
    )
    await seed(
        session,
        "c",
        sender_name="Vattenfall",
        kind_slug="utility-bill",
        document_date=date(2025, 3, 4),
        amount="130.00",
    )
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    body = serialise_summary(summary, include_points=True)
    assert body["status"] == "ok"
    assert body["median"] == "100.00"
    assert body["reference"]["verdict"] == "higher"
    assert isinstance(body["document_ids"], list)
    assert isinstance(body["points"], list)
    assert body["points"][0]["amount"] == "100.00"
    assert isinstance(body["points"][0]["document_id"], int)
    assert body["points"][0]["document_id"] == oldest_id
