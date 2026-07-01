"""Tests for authored-series signature matching: reasons + propose-for-review."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from library.series import SeriesSignature, _Member
from library.series_match import generate_reason, propose_authored_matches

pytestmark = pytest.mark.integration


# --- Fake Anthropic client (mirrors test_series_insight) --------------------


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Response:
    content: list[_Block]
    usage: _Usage


class FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        return _Response(content=[_Block(self._text)], usage=_Usage(120, 18))


class FakeAnthropic:
    def __init__(self, text: str = "This bill is from a different provider.") -> None:
        self.messages = FakeMessages(text)


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


# --- generate_reason --------------------------------------------------------


async def test_generate_reason_returns_text_and_tokens() -> None:
    signature = SeriesSignature(
        sender_id=1, kind_id=2, currency="EUR", member_count=4, dominant_count=3, dominance=0.75
    )
    candidate = _Member(
        document_id=9,
        sender="OtherCo",
        kind="invoice",
        document_date=date(2025, 5, 1),
        amount=Decimal("200.00"),
        currency="EUR",
        sender_id=9,
        kind_id=2,
        title="Stray invoice",
    )
    client = FakeAnthropic("Different sender than the rest of the series.")
    text, in_tok, out_tok = await generate_reason(
        client,
        "claude-haiku-4-5",
        signature=signature,
        candidate=candidate,
        mechanical_axis="sender",
    )
    assert text == "Different sender than the rest of the series."
    assert (in_tok, out_tok) == (120, 18)
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"


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
