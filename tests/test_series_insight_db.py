"""Model/migration tests for the ``series_insights`` table."""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import Kind, Sender, SeriesInsight

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(SeriesInsight))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


async def _sender(session: AsyncSession, name: str) -> Sender:
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


async def _kind_id(session: AsyncSession, slug: str) -> int:
    return (await session.execute(select(Kind).where(Kind.slug == slug))).scalar_one().id


async def test_insert_and_roundtrip(session: AsyncSession) -> None:
    sender = await _sender(session, "Vattenfall")
    kind_id = await _kind_id(session, "utility-bill")
    session.add(
        SeriesInsight(
            sender_id=sender.id,
            kind_id=kind_id,
            currency="EUR",
            description="Bills have crept up about 12% over the past year.",
            model="claude-haiku-4-5",
            member_count=6,
            input_tokens=400,
            output_tokens=80,
            cost_usd=0.0008,
        )
    )
    await session.commit()

    row = (await session.execute(select(SeriesInsight))).scalar_one()
    assert row.currency == "EUR"
    assert row.member_count == 6
    assert "crept up" in row.description
    assert row.created_at is not None


async def test_unique_key_treats_null_currency_as_one_bucket(session: AsyncSession) -> None:
    """A NULL-currency series may only have a single cached row (NULLS NOT DISTINCT)."""
    sender = await _sender(session, "Gemeente")
    kind_id = await _kind_id(session, "other")
    session.add(
        SeriesInsight(
            sender_id=sender.id,
            kind_id=kind_id,
            currency=None,
            description="First.",
            model="claude-haiku-4-5",
        )
    )
    await session.commit()

    session.add(
        SeriesInsight(
            sender_id=sender.id,
            kind_id=kind_id,
            currency=None,
            description="Duplicate — should be rejected.",
            model="claude-haiku-4-5",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_nulls_not_distinct_declared_on_constraint(engine: AsyncEngine) -> None:
    """The unique index is declared with NULLS NOT DISTINCT in Postgres."""
    async with engine.connect() as connection:
        indnullsnotdistinct = (
            await connection.execute(
                sa.text(
                    "SELECT i.indnullsnotdistinct FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = 'series_insights_sender_kind_currency'"
                )
            )
        ).scalar_one()
    assert indnullsnotdistinct is True
