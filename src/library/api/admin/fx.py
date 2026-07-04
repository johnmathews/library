"""Currency normalisation and FX-rate seeding endpoints."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.api.admin._base import _CURRENCY_MUTATION_LOCK_KEY, router
from library.config import Settings, get_settings
from library.currencies import list_currencies_in_use, normalize_currency
from library.db import get_session
from library.fx_admin import list_fx_status, seed_fx_rate, seed_fx_rate_live
from library.fx_api import FxApiError

# ----------------------------------------------------------- currency management


class CurrencyInUse(BaseModel):
    """One currency code with the number of (non-deleted) documents using it."""

    code: str
    document_count: int


class CurrencyNormalizeIn(BaseModel):
    """Body of POST /api/admin/currencies/normalize."""

    from_code: Annotated[str, StringConstraints(max_length=8)]
    to_code: Annotated[str, StringConstraints(max_length=8)]


class CurrencyNormalizeOut(BaseModel):
    """Result of a successful currency normalisation."""

    from_code: str
    to_code: str
    counts: dict[str, int]
    # True when ``to_code`` has no fx_rates row, so FX conversion for it is
    # unavailable until a rate is seeded (fx_rates is never mutated by a rename).
    fx_rate_missing: bool


class CurrencyConflictItem(BaseModel):
    """One user-authored override that blocks a currency rename."""

    table: str
    sender_id: int | None
    kind_id: int | None


class CurrencyOverrideConflict(BaseModel):
    """409 body when a rename would collide with user-authored series overrides.

    The rename is refused and nothing is changed; the admin resolves the listed
    overrides first (no user data is dropped).
    """

    detail: str
    conflicts: list[CurrencyConflictItem]


@router.get(
    "/currencies",
    response_model=list[CurrencyInUse],
    summary="List the distinct currency codes in use",
)
async def list_currencies_route(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CurrencyInUse]:
    """Distinct currency codes across non-deleted documents, with counts."""
    rows = await list_currencies_in_use(session)
    return [CurrencyInUse(code=row.code, document_count=row.document_count) for row in rows]


@router.post(
    "/currencies/normalize",
    response_model=CurrencyNormalizeOut,
    summary="Rename/normalise a currency code across the whole store (series-aware)",
    responses={
        400: {"description": "Source and target are the same code"},
        409: {
            "model": CurrencyOverrideConflict,
            "description": "Refused: would collide with user-authored series overrides",
        },
        422: {"description": "A code is not a 3-letter currency code"},
    },
)
async def normalize_currency_route(
    payload: CurrencyNormalizeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrencyNormalizeOut | JSONResponse:
    """Rename currency ``from_code`` to ``to_code`` everywhere it appears.

    Rewrites documents, authored series and suggestions, merges/cleans the
    series-insight cache, and updates the series override tables — but refuses
    (409) if that would collide with a user-authored override, and never touches
    ``fx_rates`` (a missing target rate is reported in ``fx_rate_missing``). See
    docs/api.md and the currencies module for the full policy.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CURRENCY_MUTATION_LOCK_KEY}
    )
    result = await normalize_currency(session, payload.from_code, payload.to_code)
    if result.status in ("invalid_source", "invalid_target"):
        field = "from_code" if result.status == "invalid_source" else "to_code"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} must be a 3-letter currency code",
        )
    if result.status == "same_code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_code and to_code are the same",
        )
    if result.status == "override_conflict":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"renaming {result.from_code} to {result.to_code} would collide with "
                    f"{len(result.conflicts)} user-authored series override(s); resolve them first"
                ),
                "conflicts": [
                    {"table": c.table, "sender_id": c.sender_id, "kind_id": c.kind_id}
                    for c in result.conflicts
                ],
            },
        )
    return CurrencyNormalizeOut(
        from_code=result.from_code,
        to_code=result.to_code,
        counts=result.counts,
        fx_rate_missing=result.fx_rate_missing,
    )


# ------------------------------------------------------------------- FX rates


class FxRateStatus(BaseModel):
    """One in-use currency and whether it has a seeded FX rate.

    ``is_base`` is USD (rate 1.0, always convertible, never seeded). ``rate_to_base``
    / ``as_of`` carry the latest seeded row when ``has_rate`` is true.
    """

    code: str
    document_count: int
    is_base: bool
    has_rate: bool
    rate_to_base: Decimal | None = None
    as_of: date | None = None


class FxRateSeedIn(BaseModel):
    """Body of POST /api/admin/fx-rates.

    ``source="live"`` fetches the current USD-per-unit rate from the provider;
    ``source="manual"`` requires ``rate_to_base`` (USD per one unit). ``as_of``
    defaults to today.
    """

    currency: Annotated[str, StringConstraints(max_length=8)]
    source: Literal["live", "manual"] = "live"
    rate_to_base: Decimal | None = Field(default=None, gt=0)
    as_of: date | None = None


class FxRateSeedOut(BaseModel):
    """The seeded FX row."""

    currency: str
    as_of: date
    rate_to_base: Decimal


@router.get(
    "/fx-rates",
    response_model=list[FxRateStatus],
    summary="Report the FX-rate seeding status of every in-use currency",
)
async def list_fx_rates_route(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[FxRateStatus]:
    """Per in-use currency: document count and whether an FX rate is seeded."""
    rows = await list_fx_status(session)
    return [
        FxRateStatus(
            code=row.code,
            document_count=row.document_count,
            is_base=row.is_base,
            has_rate=row.has_rate,
            rate_to_base=row.rate_to_base,
            as_of=row.as_of,
        )
        for row in rows
    ]


@router.post(
    "/fx-rates",
    response_model=FxRateSeedOut,
    summary="Seed an FX rate for a currency (live fetch or manual entry)",
    responses={
        422: {"description": "Not a 3-letter code, USD (the base), or manual with no rate"},
        502: {"description": "The live FX provider failed or does not list the currency"},
    },
)
async def seed_fx_rate_route(
    payload: FxRateSeedIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FxRateSeedOut:
    """Seed (upsert) one ``fx_rates`` row so conversion for ``currency`` resolves.

    Live source fetches the USD-per-unit rate; manual source uses ``rate_to_base``.
    USD is refused (the implicit base). See docs/admin.md and the fx modules.
    """
    if payload.source == "manual":
        if payload.rate_to_base is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="rate_to_base is required when source is manual",
            )
        result = await seed_fx_rate(session, payload.currency, payload.rate_to_base, payload.as_of)
    else:
        try:
            result = await seed_fx_rate_live(session, payload.currency, settings=settings)
        except FxApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"live FX lookup failed: {exc}",
            ) from exc

    if result.status == "invalid_code":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="currency must be a 3-letter currency code",
        )
    if result.status == "is_base":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="USD is the base currency (rate 1.0) and needs no seeded rate",
        )
    if result.status == "unsupported":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"the live FX provider does not list {result.currency}; enter a rate manually",
        )
    assert result.as_of is not None and result.rate_to_base is not None
    return FxRateSeedOut(
        currency=result.currency, as_of=result.as_of, rate_to_base=result.rate_to_base
    )
