"""Reference foreign-exchange conversion, base = USD.

Backs cross-currency series-membership pins (W8): a document pinned into a
series whose currency differs is converted, *date-aware*, into the series
currency using the seeded ``fx_rates`` snapshot. The rate for a date is the row
with the greatest ``as_of`` on-or-before it, falling back to the earliest known
rate (or, when no date is given, the latest). Returns ``None`` when a currency
has no rate so the caller can degrade gracefully (treat the pin as
display-only).

``rate_to_base`` is the value of one unit of a currency in USD; USD is 1.0 by
definition and is not stored.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import FxRate

BASE_CURRENCY = "USD"


def convert(amount: Decimal, rate_from: Decimal, rate_to: Decimal) -> Decimal:
    """Convert ``amount`` between currencies given their USD-per-unit rates.

    ``rate_from``/``rate_to`` are each the value of one unit in USD, so
    ``amount * rate_from`` is USD and dividing by ``rate_to`` reaches the
    target currency. ``rate_to`` must be non-zero.
    """
    return amount * rate_from / rate_to


async def rate_to_base(
    session: AsyncSession, currency: str, on_date: date | None
) -> Decimal | None:
    """The USD-per-unit rate for ``currency`` near ``on_date`` (None ⇒ latest).

    Picks the row with the greatest ``as_of`` on-or-before ``on_date``; if none
    qualifies (date predates the snapshot) returns the earliest known rate.
    ``None`` when the currency is absent from ``fx_rates``.
    """
    if currency == BASE_CURRENCY:
        return Decimal(1)
    if on_date is not None:
        on_or_before = (
            select(FxRate.rate_to_base)
            .where(FxRate.currency == currency, FxRate.as_of <= on_date)
            .order_by(FxRate.as_of.desc())
            .limit(1)
        )
        rate = (await session.execute(on_or_before)).scalar_one_or_none()
        if rate is not None:
            return rate
    # No date, or a date before the earliest snapshot: take the nearest endpoint.
    direction = FxRate.as_of.desc() if on_date is None else FxRate.as_of.asc()
    fallback = (
        select(FxRate.rate_to_base).where(FxRate.currency == currency).order_by(direction).limit(1)
    )
    return (await session.execute(fallback)).scalar_one_or_none()


async def convert_amount(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    on_date: date | None,
) -> Decimal | None:
    """Convert ``amount`` from one currency to another at ``on_date``.

    Returns ``amount`` unchanged when the currencies match, and ``None`` when
    either currency has no usable rate (caller degrades gracefully).
    """
    if from_currency == to_currency:
        return amount
    rate_from = await rate_to_base(session, from_currency, on_date)
    rate_to = await rate_to_base(session, to_currency, on_date)
    if rate_from is None or rate_to is None or rate_to == 0:
        return None
    return convert(amount, rate_from, rate_to)
