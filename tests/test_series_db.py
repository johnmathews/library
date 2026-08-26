"""Integration tests for summarize_series over seeded documents."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import (
    Document,
    DocumentSource,
    Kind,
    OverrideAction,
    ReviewStatus,
    Sender,
    SeriesMembershipOverride,
)
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
    """The partition invariant, across every way a series narrows.

    The three excluded counts (3 amountless, 2 other-group, 1 other-currency)
    are deliberately made from DIFFERENT numbers of documents, mirroring the
    fix applied to ``test_load_members_reports_amountless_and_non_dominant_drops``
    in commit a136944: three equal 1s would pass even if ``_coverage`` assigned
    a count to the wrong reason key. See the Fix 2 permutation experiment in
    the task report for direct proof this test catches that swap.
    """
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
    # Two documents in a non-dominant (sender, kind) group.
    for index, amount in enumerate(["200.00", "210.00"]):
        await seed(
            session,
            f"sc-beta{index}",
            sender_name=beta.name,
            kind_slug="utility-bill",
            document_date=date(2025, 1, 10 + index),
            amount=amount,
            currency="EUR",
        )
    # seed() requires an amount; amountless docs must be built inline
    # (the same pattern Task 1 used in test_load_members_reports_amountless_and_non_dominant_drops).
    # Three of them, so no_amount/other_group/other_currency are 3/2/1 — all distinct.
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    for index in range(3):
        session.add(
            Document(
                sha256=hashlib.sha256(f"sc-none{index}".encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                sender=alpha,
                kind=kind,
                document_date=date(2025, 1, 20 + index),
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
    assert summary.coverage.included == 3
    assert summary.coverage.excluded == {
        "no_amount": 3,
        "other_series_group": 2,
        "other_currency": 1,
    }
    assert summary.coverage.matched == 9
    assert (
        summary.coverage.included + sum(summary.coverage.excluded.values())
        == summary.coverage.matched
    )


async def test_summarize_series_coverage_flags_untrusted_members(
    session: AsyncSession,
) -> None:
    """A distribution built partly on amounts the validator could not ground.

    Also flags a document that ends up EXCLUDED (a non-dominant-currency USD
    doc): if ``needs_review`` were counted over the pre-narrowing set instead
    of the final bucket, it would come back 2, not 1.
    """
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
    excluded_id = await seed(
        session,
        "cf-usd",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 9),
        amount="90.00",
        currency="USD",
    )

    included_flagged = await session.get(Document, ids[0])
    assert included_flagged is not None
    included_flagged.review_status = ReviewStatus.NEEDS_REVIEW

    excluded_flagged = await session.get(Document, excluded_id)
    assert excluded_flagged is not None
    excluded_flagged.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        settings=_settings(),
    )

    assert summary.coverage is not None
    assert summary.coverage.included == 3
    assert summary.coverage.excluded == {"other_currency": 1}
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


async def test_summarize_series_coverage_partitions_correctly_across_overrides(
    session: AsyncSession,
) -> None:
    """The partition invariant must hold once real PIN/EXCLUDE overrides run.

    Overrides act on the resolved (sender, kind, currency) identity, not on
    ``filters`` — unlike the three narrowings ``summarize_series`` does on
    its own, an EXCLUDE can remove a document that would otherwise have been
    included, and a PIN can add one back: either from a different currency in
    the SAME (sender, kind) group (reclassified out of "other_currency"), or
    from entirely outside ``filters`` (grows ``matched`` itself, since a PIN
    "regardless of its own sender/kind" can reach documents the filters never
    touched — see ``_load_pinned_members``).
    """
    alpha = await _sender(session, "AlphaEnergy")
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()

    eur_ids = []
    for index, amount in enumerate(["100.00", "110.00", "120.00", "130.00"]):
        eur_ids.append(
            await seed(
                session,
                f"ov-eur{index}",
                sender_name=alpha.name,
                kind_slug="utility-bill",
                document_date=date(2025, 1, index + 1),
                amount=amount,
                currency="EUR",
            )
        )
    # Same (sender, kind) group, a different currency: a genuine series member
    # that _load_members counts as "other_currency" until pinned in.
    gbp_id = await seed(
        session,
        "ov-gbp",
        sender_name=alpha.name,
        kind_slug="utility-bill",
        document_date=date(2022, 5, 1),  # a date the seeded fx_rates table covers
        amount="100.00",
        currency="GBP",
    )
    # A different KIND entirely: DocumentFilters(kind_slug="utility-bill") never
    # matches it, so it starts outside `matched` altogether — until pinned.
    outsider_id = await seed(
        session,
        "ov-outsider",
        sender_name=alpha.name,
        kind_slug="invoice",
        document_date=date(2025, 1, 5),
        amount="500.00",
        currency="EUR",
    )

    session.add_all(
        [
            SeriesMembershipOverride(
                sender_id=alpha.id,
                kind_id=kind.id,
                currency="EUR",
                document_id=eur_ids[0],
                action=OverrideAction.EXCLUDE,
            ),
            SeriesMembershipOverride(
                sender_id=alpha.id,
                kind_id=kind.id,
                currency="EUR",
                document_id=gbp_id,
                action=OverrideAction.PIN,
            ),
            SeriesMembershipOverride(
                sender_id=alpha.id,
                kind_id=kind.id,
                currency="EUR",
                document_id=outsider_id,
                action=OverrideAction.PIN,
            ),
        ]
    )
    await session.commit()

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        settings=_settings(),
    )

    assert summary.status == "ok"
    assert eur_ids[0] not in summary.document_ids
    assert gbp_id in summary.document_ids
    assert outsider_id in summary.document_ids

    assert summary.coverage is not None
    # 3 remaining EUR + the pinned GBP doc + the pinned outsider.
    assert summary.coverage.included == 5
    # 5 filter-matched (4 EUR + 1 GBP), plus the outsider pinned in from
    # outside the filters entirely.
    assert summary.coverage.matched == 6
    # The GBP doc's "other_currency" count and the outsider's "never matched
    # the filters at all" both net to zero: each was fully absorbed by its
    # pin, leaving only the EXCLUDE's manually_excluded behind.
    assert summary.coverage.excluded == {"manually_excluded": 1}
    assert (
        summary.coverage.included + sum(summary.coverage.excluded.values())
        == summary.coverage.matched
    )
