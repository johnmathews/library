"""FX conversion: pure math (unit) + date-aware DB lookup against seeded rates."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.fx import BASE_CURRENCY, convert, convert_amount, rate_to_base


def test_convert_pure_math() -> None:
    # 100 units worth 1.10 USD each -> USD 110 -> a currency worth 1.375 USD each = 80.
    assert convert(Decimal("100"), Decimal("1.10"), Decimal("1.375")) == Decimal("80")


def test_convert_identity_when_rates_equal() -> None:
    assert convert(Decimal("42.50"), Decimal("1.23"), Decimal("1.23")) == Decimal("42.50")


async def _convert(database_url: str, *args: object) -> object:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            return await convert_amount(session, *args)
    finally:
        await engine.dispose()


async def _rate(database_url: str, currency: str, on_date: date | None) -> Decimal | None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            return await rate_to_base(session, currency, on_date)
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_same_currency_returns_amount_unchanged(migrated_database_url: str) -> None:
    result = _run(_convert(migrated_database_url, Decimal("12.34"), "EUR", "EUR", date(2024, 1, 1)))
    assert result == Decimal("12.34")


@pytest.mark.integration
def test_base_currency_rate_is_one(migrated_database_url: str) -> None:
    assert _run(_rate(migrated_database_url, BASE_CURRENCY, date(2020, 6, 1))) == Decimal(1)


@pytest.mark.integration
def test_eur_to_usd_uses_seeded_rate(migrated_database_url: str) -> None:
    # EUR pinned into a USD series in 2022: rate_to_base[EUR@2022]=1.053, USD=1.
    result = _run(_convert(migrated_database_url, Decimal("100"), "EUR", "USD", date(2022, 6, 1)))
    assert result == Decimal("105.300")


@pytest.mark.integration
def test_date_aware_picks_on_or_before(migrated_database_url: str) -> None:
    # A 2018 date must use the 2018 snapshot (1.181), not a later one.
    rate = _run(_rate(migrated_database_url, "EUR", date(2018, 7, 1)))
    assert rate == Decimal("1.18100000")


@pytest.mark.integration
def test_date_before_earliest_falls_back_to_earliest(migrated_database_url: str) -> None:
    # 2010 predates the snapshot; fall back to the earliest known (2015 = 1.110).
    rate = _run(_rate(migrated_database_url, "EUR", date(2010, 1, 1)))
    assert rate == Decimal("1.11000000")


@pytest.mark.integration
def test_unknown_currency_returns_none(migrated_database_url: str) -> None:
    result = _run(_convert(migrated_database_url, Decimal("10"), "XYZ", "USD", date(2024, 1, 1)))
    assert result is None


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]
