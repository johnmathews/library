"""Tests for authored-series signature matching: propose-for-review auto-continue.

The odd-one-out *reason* is deterministic and lives in ``library.series`` (tested
in ``test_series.py``); there is no LLM in this module's path.
"""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import (
    AuthoredSeries,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    DocumentSource,
    Kind,
    Sender,
    SuggestionState,
)
from library.series_match import propose_authored_matches

pytestmark = pytest.mark.integration


# --- Fixtures + seeding -----------------------------------------------------


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(AuthoredSeriesSuggestion))
        await session.execute(delete(AuthoredSeriesMember))
        await session.execute(delete(AuthoredSeries))
        await session.execute(delete(Document))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "series_autocontinue_enabled": True,
        "series_autocontinue_min_dominance": 0.6,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _sender(session: AsyncSession, name: str) -> Sender:
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


async def _kind_id(session: AsyncSession, slug: str) -> int:
    return (await session.execute(select(Kind).where(Kind.slug == slug))).scalar_one().id


async def _doc(
    session: AsyncSession,
    *,
    sender_id: int | None,
    kind_id: int | None,
    currency: str | None = "EUR",
    amount: str | None = "100.00",
) -> int:
    document = Document(
        sha256=hashlib.sha256(f"match:{uuid.uuid4()}".encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender_id=sender_id,
        kind_id=kind_id,
        currency=currency,
        amount_total=None if amount is None else Decimal(amount),
        document_date=date(2025, 1, 1),
    )
    session.add(document)
    await session.commit()
    return document.id


async def _series_with_members(session: AsyncSession, sender_id: int, kind_id: int) -> int:
    series = AuthoredSeries(name="Energy", currency="EUR")
    session.add(series)
    await session.commit()
    for _ in range(3):
        did = await _doc(session, sender_id=sender_id, kind_id=kind_id)
        session.add(AuthoredSeriesMember(authored_series_id=series.id, document_id=did))
    await session.commit()
    return series.id


async def _member_count(session: AsyncSession, series_id: int) -> int:
    rows = (
        (
            await session.execute(
                select(AuthoredSeriesMember).where(
                    AuthoredSeriesMember.authored_series_id == series_id
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


# --- propose_authored_matches (PROPOSE-FOR-REVIEW) --------------------------


async def test_propose_creates_pending_and_no_member(session: AsyncSession) -> None:
    sender = await _sender(session, "ProposeCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series_with_members(session, sender.id, kind_id)
    before = await _member_count(session, series_id)
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)

    await propose_authored_matches(session, _settings(), candidate)

    suggestion = (
        await session.execute(
            select(AuthoredSeriesSuggestion).where(
                AuthoredSeriesSuggestion.document_id == candidate
            )
        )
    ).scalar_one()
    assert suggestion.state == SuggestionState.PENDING
    assert suggestion.authored_series_id == series_id
    assert suggestion.signature_sender_id == sender.id
    # Core invariant: proposing NEVER changes membership.
    assert await _member_count(session, series_id) == before


async def test_propose_is_idempotent(session: AsyncSession) -> None:
    sender = await _sender(session, "IdemCo")
    kind_id = await _kind_id(session, "utility-bill")
    await _series_with_members(session, sender.id, kind_id)
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)

    await propose_authored_matches(session, _settings(), candidate)
    await propose_authored_matches(session, _settings(), candidate)

    rows = (
        (
            await session.execute(
                select(AuthoredSeriesSuggestion).where(
                    AuthoredSeriesSuggestion.document_id == candidate
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_propose_skips_existing_member(session: AsyncSession) -> None:
    sender = await _sender(session, "MemberCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series_with_members(session, sender.id, kind_id)
    member = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(AuthoredSeriesMember(authored_series_id=series_id, document_id=member))
    await session.commit()

    await propose_authored_matches(session, _settings(), member)

    rows = (
        (
            await session.execute(
                select(AuthoredSeriesSuggestion).where(
                    AuthoredSeriesSuggestion.document_id == member
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_propose_skips_dismissed_tombstone(session: AsyncSession) -> None:
    sender = await _sender(session, "TombstoneCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series_with_members(session, sender.id, kind_id)
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(
        AuthoredSeriesSuggestion(
            authored_series_id=series_id,
            document_id=candidate,
            state=SuggestionState.DISMISSED,
        )
    )
    await session.commit()

    await propose_authored_matches(session, _settings(), candidate)

    row = (
        await session.execute(
            select(AuthoredSeriesSuggestion).where(
                AuthoredSeriesSuggestion.document_id == candidate
            )
        )
    ).scalar_one()
    assert row.state == SuggestionState.DISMISSED  # unchanged, not re-proposed


async def test_propose_skips_when_disabled(session: AsyncSession) -> None:
    sender = await _sender(session, "DisabledCo")
    kind_id = await _kind_id(session, "utility-bill")
    await _series_with_members(session, sender.id, kind_id)
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)

    await propose_authored_matches(session, _settings(series_autocontinue_enabled=False), candidate)

    assert (await session.execute(select(AuthoredSeriesSuggestion))).scalars().all() == []
