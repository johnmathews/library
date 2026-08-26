"""Integration tests for summarize_series over seeded documents."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import Document, DocumentSource, Kind, ReviewStatus, Sender
from library.search import DocumentFilters
from library.series import _load_members, serialise_summary, summarize_series

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


async def test_load_members_reports_amountless_and_non_dominant_drops(
    session: AsyncSession,
) -> None:
    """_load_members silently discarded two sets of documents. It now counts them."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    await seed(
        session,
        "lm1",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 1),
        amount="100.00",
    )
    await seed(
        session,
        "lm2",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2025, 2, 1),
        amount="110.00",
    )
    await seed(
        session,
        "lm3",
        sender_name=beta.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 1),
        amount="200.00",
    )
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    # Two amountless docs (both in the dominant Alpha group) vs. one
    # non-dominant-group doc (Beta): the counts land unequal (2 vs. 1) on
    # purpose, so a positional swap of the two return values is caught by
    # this test rather than passing silently.
    for marker in ("lm4", "lm5"):
        session.add(
            Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                sender=alpha,
                kind=kind,
                document_date=date(2025, 3, 1),
                amount_total=None,
                currency=None,
            )
        )
    await session.commit()

    members, no_amount, other_group = await _load_members(
        session, DocumentFilters(kind_slug="utility-bill")
    )

    assert [m.amount for m in members] == [Decimal("100.00"), Decimal("110.00")]
    assert no_amount == 2
    assert other_group == 1


async def test_summarize_series_reports_all_three_drops(session: AsyncSession) -> None:
    """The partition invariant, across every way a series narrows."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    for index, amount in enumerate(["100.00", "110.00", "120.00"]):
        await seed(
            session,
            f"sc{index}",
            sender_name=alpha.name,
            kind_slug="utility-bill",
            document_date=date(2025, 1, index + 1),
            amount=amount,
            currency="EUR",
        )
    await seed(
        session,
        "sc-usd",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 9),
        amount="90.00",
        currency="USD",
    )
    await seed(
        session,
        "sc-beta",
        sender_name=beta.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 10),
        amount="200.00",
        currency="EUR",
    )
    # seed() requires an amount; an amountless doc must be built inline
    # (the same pattern Task 1 used in test_load_members_reports_amountless_and_non_dominant_drops).
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    session.add(
        Document(
            sha256=hashlib.sha256(b"sc-none").hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            sender=alpha,
            kind=kind,
            document_date=date(2025, 1, 11),
            amount_total=None,
            currency=None,
        )
    )
    await session.commit()

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        settings=_settings(),
    )

    assert summary.coverage is not None
    assert summary.coverage.matched == 6
    assert summary.coverage.included == 3
    assert summary.coverage.excluded == {
        "no_amount": 1,
        "other_series_group": 1,
        "other_currency": 1,
    }
    assert (
        summary.coverage.included + sum(summary.coverage.excluded.values())
        == summary.coverage.matched
    )


async def test_summarize_series_coverage_flags_untrusted_members(
    session: AsyncSession,
) -> None:
    """A distribution built partly on amounts the validator could not ground."""
    alpha = await _sender(session, "AlphaEnergy")
    ids = []
    for index, amount in enumerate(["100.00", "110.00", "120.00"]):
        ids.append(
            await seed(
                session,
                f"cf{index}",
                sender_name=alpha.name,
                kind_slug="utility-bill",
                document_date=date(2025, 1, index + 1),
                amount=amount,
                currency="EUR",
            )
        )
    document = await session.get(Document, ids[0])
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        settings=_settings(),
    )

    assert summary.coverage is not None
    assert summary.coverage.needs_review == 1


async def test_insufficient_series_still_reports_coverage(session: AsyncSession) -> None:
    """The case where coverage matters MOST: too few documents to summarise, but
    the caller cannot tell whether that is because the archive is thin or
    because the series narrowed away most of what matched."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    await seed(
        session,
        "ins1",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 1),
        amount="100.00",
        currency="EUR",
    )
    await seed(
        session,
        "ins2",
        sender_name=beta.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 2),
        amount="200.00",
        currency="EUR",
    )

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        settings=_settings(),
    )

    assert summary.status == "insufficient"
    assert summary.coverage is not None
    assert summary.coverage.matched == 2
