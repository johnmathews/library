"""Admin-only FX rate seeding + status reporting.

The currency-normalise flow (``currencies``) flags ``fx_rate_missing`` but never
mutates ``fx_rates``; this module is the affordance that fills the gap. It reports
which in-use currencies have a seeded rate and seeds new ones — either by fetching
a live rate (``fx_api``) or from a manually-typed value.

A single ``fx_rates`` row per currency is enough: ``fx.rate_to_base`` falls back
to the nearest endpoint for any date. Seeding upserts on the ``(currency, as_of)``
unique constraint, which is atomic in Postgres, so no advisory lock is needed.

USD is the implicit base (``rate_to_base`` 1.0) and is never stored: it is
reported as ``is_base`` and refused for seeding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.currencies import list_currencies_in_use, normalize_currency_code
from library.fx import BASE_CURRENCY
from library.fx_api import fetch_rate_to_base
from library.models import FxRate


@dataclass(frozen=True)
class FxStatus:
    """One in-use currency and its FX-rate seeding status.

    ``is_base`` marks USD (always convertible, rate 1.0, not seedable). For a
    seeded currency ``rate_to_base``/``as_of`` carry the latest known row;
    otherwise both are ``None`` and ``has_rate`` is ``False``.
    """

    code: str
    document_count: int
    is_base: bool
    has_rate: bool
    rate_to_base: Decimal | None = None
    as_of: date | None = None


@dataclass(frozen=True)
class SeedResult:
    """Outcome of a seed attempt.

    - ``done`` — a row was upserted; ``currency``/``as_of``/``rate_to_base`` echo it.
    - ``invalid_code`` — the code was not ``^[A-Z]{3}$``.
    - ``is_base`` — the code was USD (the implicit base is never seeded).
    - ``unsupported`` — a live fetch ran but the provider does not list the code.
    """

    status: Literal["done", "invalid_code", "is_base", "unsupported"]
    currency: str = ""
    as_of: date | None = None
    rate_to_base: Decimal | None = None


async def list_fx_status(session: AsyncSession) -> list[FxStatus]:
    """FX-rate status for every currency currently on a non-deleted document.

    Reuses :func:`list_currencies_in_use` for the code list + counts, then joins
    the latest ``fx_rates`` row (greatest ``as_of``) for each non-base code.
    """
    in_use = await list_currencies_in_use(session)
    codes = [c.code for c in in_use if c.code != BASE_CURRENCY]

    latest: dict[str, tuple[date, Decimal]] = {}
    if codes:
        rows = (
            await session.execute(
                select(FxRate.currency, FxRate.as_of, FxRate.rate_to_base)
                .where(FxRate.currency.in_(codes))
                .order_by(FxRate.currency, FxRate.as_of.desc())
            )
        ).all()
        # Rows are ordered as_of-descending per currency, so the first wins.
        for currency, as_of, rate in rows:
            latest.setdefault(currency, (as_of, rate))

    statuses: list[FxStatus] = []
    for entry in in_use:
        if entry.code == BASE_CURRENCY:
            statuses.append(
                FxStatus(
                    code=entry.code,
                    document_count=entry.document_count,
                    is_base=True,
                    has_rate=True,
                    rate_to_base=Decimal(1),
                )
            )
            continue
        found = latest.get(entry.code)
        statuses.append(
            FxStatus(
                code=entry.code,
                document_count=entry.document_count,
                is_base=False,
                has_rate=found is not None,
                rate_to_base=found[1] if found else None,
                as_of=found[0] if found else None,
            )
        )
    return statuses


async def seed_fx_rate(
    session: AsyncSession,
    currency_raw: str,
    rate_to_base: Decimal,
    as_of: date | None = None,
) -> SeedResult:
    """Upsert a single ``fx_rates`` row for ``currency`` (validated, non-USD).

    ``as_of`` defaults to today. Upserts on the ``(currency, as_of)`` unique
    constraint (re-seeding the same day updates the rate). Commits.
    """
    currency = normalize_currency_code(currency_raw)
    if currency is None:
        return SeedResult(status="invalid_code")
    if currency == BASE_CURRENCY:
        return SeedResult(status="is_base", currency=currency)

    when = as_of or date.today()
    stmt = (
        pg_insert(FxRate)
        .values(currency=currency, as_of=when, rate_to_base=rate_to_base)
        .on_conflict_do_update(
            constraint="fx_rates_currency_as_of",
            set_={"rate_to_base": rate_to_base},
        )
    )
    await session.execute(stmt)
    await session.commit()
    return SeedResult(status="done", currency=currency, as_of=when, rate_to_base=rate_to_base)


async def seed_fx_rate_live(
    session: AsyncSession,
    currency_raw: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> SeedResult:
    """Fetch the live USD-per-unit rate for ``currency`` and seed it (``as_of`` today).

    Validates + rejects USD before any network call. Returns ``unsupported`` when
    the provider doesn't list the code; raises ``FxApiError`` (from ``fx_api``) on
    a transport/payload failure.
    """
    currency = normalize_currency_code(currency_raw)
    if currency is None:
        return SeedResult(status="invalid_code")
    if currency == BASE_CURRENCY:
        return SeedResult(status="is_base", currency=currency)

    rate = await fetch_rate_to_base(currency, settings=settings, client=client)
    if rate is None:
        return SeedResult(status="unsupported", currency=currency)
    return await seed_fx_rate(session, currency, rate)
