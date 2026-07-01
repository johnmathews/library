"""Model/migration + DB-function tests for ``authored_series_suggestions``."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
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
from library.series import load_authored_signature, suggest_signature_matches

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
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
        "series_autocontinue_min_dominance": 0.6,
        "series_suggestion_limit": 20,
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
    document_date: date | None = date(2025, 1, 1),
) -> int:
    marker = f"suggestion:{uuid.uuid4()}"
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender_id=sender_id,
        kind_id=kind_id,
        currency=currency,
        amount_total=None if amount is None else Decimal(amount),
        document_date=document_date,
    )
    session.add(document)
    await session.commit()
    return document.id


async def _series(session: AsyncSession, name: str = "Series", currency: str | None = "EUR") -> int:
    series = AuthoredSeries(name=name, currency=currency)
    session.add(series)
    await session.commit()
    return series.id


async def _add_member(session: AsyncSession, series_id: int, document_id: int) -> None:
    session.add(AuthoredSeriesMember(authored_series_id=series_id, document_id=document_id))
    await session.commit()


# --- Table shape / constraints ---------------------------------------------


async def test_insert_and_roundtrip(session: AsyncSession) -> None:
    sender = await _sender(session, "Vattenfall")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(
        AuthoredSeriesSuggestion(
            authored_series_id=series_id,
            document_id=doc_id,
            state=SuggestionState.PENDING,
            signature_sender_id=sender.id,
            signature_kind_id=kind_id,
            signature_currency="EUR",
        )
    )
    await session.commit()

    row = (await session.execute(select(AuthoredSeriesSuggestion))).scalar_one()
    assert row.state == SuggestionState.PENDING
    assert row.signature_sender_id == sender.id
    assert row.signature_currency == "EUR"
    assert row.reason is None
    assert row.created_at is not None


async def test_unique_constraint_series_document(session: AsyncSession) -> None:
    sender = await _sender(session, "Eneco")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(AuthoredSeriesSuggestion(authored_series_id=series_id, document_id=doc_id))
    await session.commit()

    session.add(AuthoredSeriesSuggestion(authored_series_id=series_id, document_id=doc_id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_cascade_on_series_delete(session: AsyncSession) -> None:
    sender = await _sender(session, "Gemeente")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(AuthoredSeriesSuggestion(authored_series_id=series_id, document_id=doc_id))
    await session.commit()

    series = await session.get(AuthoredSeries, series_id)
    assert series is not None
    await session.delete(series)
    await session.commit()

    assert (await session.execute(select(AuthoredSeriesSuggestion))).scalars().all() == []


async def test_cascade_on_document_delete(session: AsyncSession) -> None:
    sender = await _sender(session, "KPN")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(AuthoredSeriesSuggestion(authored_series_id=series_id, document_id=doc_id))
    await session.commit()

    document = await session.get(Document, doc_id)
    assert document is not None
    await session.delete(document)
    await session.commit()

    assert (await session.execute(select(AuthoredSeriesSuggestion))).scalars().all() == []


async def test_state_check_rejects_unknown_value(session: AsyncSession) -> None:
    """The non-native enum is a VARCHAR + CHECK: only pending/dismissed are allowed."""
    sender = await _sender(session, "Ziggo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            text(
                "INSERT INTO authored_series_suggestions "
                "(authored_series_id, document_id, state) VALUES (:s, :d, 'bogus')"
            ),
            {"s": series_id, "d": doc_id},
        )
        await session.commit()
    await session.rollback()


# --- load_authored_signature -----------------------------------------------


async def test_load_authored_signature(session: AsyncSession) -> None:
    sender = await _sender(session, "SignatureCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    for _ in range(3):
        doc_id = await _doc(session, sender_id=sender.id, kind_id=kind_id)
        await _add_member(session, series_id, doc_id)

    signature = await load_authored_signature(session, series_id)
    assert signature is not None
    assert (signature.sender_id, signature.kind_id, signature.currency) == (
        sender.id,
        kind_id,
        "EUR",
    )
    assert signature.dominance == 1.0
    assert signature.member_count == 3


async def test_load_authored_signature_empty_is_none(session: AsyncSession) -> None:
    series_id = await _series(session)
    assert await load_authored_signature(session, series_id) is None


# --- suggest_signature_matches ---------------------------------------------


async def test_suggest_matches_returns_non_members_only(session: AsyncSession) -> None:
    sender = await _sender(session, "MatchCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    member_ids = [await _doc(session, sender_id=sender.id, kind_id=kind_id) for _ in range(3)]
    for did in member_ids:
        await _add_member(session, series_id, did)
    # Two matching non-members and one non-matching (different sender).
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    other_sender = await _sender(session, "Unrelated")
    await _doc(session, sender_id=other_sender.id, kind_id=kind_id)

    matches = await suggest_signature_matches(session, series_id, _settings())
    match_ids = {m.document_id for m in matches}
    assert candidate in match_ids
    assert not (match_ids & set(member_ids))  # members excluded
    assert len(match_ids) == 1


async def test_suggest_matches_excludes_dismissed(session: AsyncSession) -> None:
    sender = await _sender(session, "DismissCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    for _ in range(3):
        await _add_member(
            session, series_id, await _doc(session, sender_id=sender.id, kind_id=kind_id)
        )
    candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id)
    session.add(
        AuthoredSeriesSuggestion(
            authored_series_id=series_id,
            document_id=candidate,
            state=SuggestionState.DISMISSED,
        )
    )
    await session.commit()

    matches = await suggest_signature_matches(session, series_id, _settings())
    assert candidate not in {m.document_id for m in matches}


async def test_suggest_matches_null_currency_bucket(session: AsyncSession) -> None:
    sender = await _sender(session, "NullCurCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session, currency=None)
    for _ in range(3):
        await _add_member(
            session,
            series_id,
            await _doc(session, sender_id=sender.id, kind_id=kind_id, currency=None),
        )
    null_candidate = await _doc(session, sender_id=sender.id, kind_id=kind_id, currency=None)
    # Same sender/kind but a concrete currency: different bucket, not a match.
    await _doc(session, sender_id=sender.id, kind_id=kind_id, currency="EUR")

    matches = await suggest_signature_matches(session, series_id, _settings())
    match_ids = {m.document_id for m in matches}
    assert match_ids == {null_candidate}


async def test_suggest_matches_respects_limit(session: AsyncSession) -> None:
    sender = await _sender(session, "LimitCo")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    for _ in range(3):
        await _add_member(
            session, series_id, await _doc(session, sender_id=sender.id, kind_id=kind_id)
        )
    for _ in range(5):
        await _doc(session, sender_id=sender.id, kind_id=kind_id)

    matches = await suggest_signature_matches(
        session, series_id, _settings(series_suggestion_limit=2)
    )
    assert len(matches) == 2


async def test_suggest_matches_dominance_gate(session: AsyncSession) -> None:
    """A mixed membership (dominance below the threshold) yields no suggestions."""
    sender_a = await _sender(session, "GateA")
    sender_b = await _sender(session, "GateB")
    kind_id = await _kind_id(session, "utility-bill")
    series_id = await _series(session)
    # 2 of A, 2 of B => dominance 0.5 < 0.6 threshold.
    for sid in (sender_a.id, sender_a.id, sender_b.id, sender_b.id):
        await _add_member(session, series_id, await _doc(session, sender_id=sid, kind_id=kind_id))
    await _doc(session, sender_id=sender_a.id, kind_id=kind_id)  # would-be candidate

    matches = await suggest_signature_matches(session, series_id, _settings())
    assert matches == []
