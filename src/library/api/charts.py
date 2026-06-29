"""Aggregate charts endpoint: every eligible recurring ``(sender, kind)`` series.

Backs the ``/charts`` view. Enumerates the ``(sender_id, kind_id)`` pairs with
enough amount-bearing documents to summarise, then reuses
``library.series.summarize_series`` per pair so each entry has the same shape as
``GET /api/documents/{id}/series`` (stats + points + cached description). Series
whose dominant currency bucket is too small are skipped.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings, get_settings
from library.db import get_session
from library.models import Document, Kind, Sender, SeriesMetaOverride
from library.search import DocumentFilters
from library.series import (
    SeriesSummary,
    decode_series_id,
    serialise_summary,
    summarize_series,
)

router = APIRouter()


async def _eligible_series(session: AsyncSession, min_documents: int) -> list[tuple[int, int, str]]:
    """``(sender_id, kind_id, kind_slug)`` pairs with enough documents to chart.

    A pair is eligible when it has at least ``min_documents`` non-deleted,
    amount-bearing documents. Ordered by document count so the busiest series
    surface first.
    """
    statement = (
        select(Document.sender_id, Document.kind_id, Kind.slug, func.count().label("n"))
        .join(Kind, Document.kind_id == Kind.id)
        .where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.sender_id.isnot(None),
            Document.kind_id.isnot(None),
        )
        .group_by(Document.sender_id, Document.kind_id, Kind.slug)
        .having(func.count() >= min_documents)
        .order_by(func.count().desc(), Kind.slug)
    )
    rows = (await session.execute(statement)).all()
    return [(sender_id, kind_id, slug) for sender_id, kind_id, slug, _ in rows]


@router.get(
    "/charts",
    summary="Every eligible recurring (sender, kind) series, summarised for charting",
)
async def list_charts(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """List all chartable series. Each entry mirrors the per-document series body
    (``include_points=True``); series too sparse to summarise are omitted."""
    eligible = await _eligible_series(session, settings.series_min_documents)
    series: list[dict[str, object]] = []
    for sender_id, _kind_id, kind_slug in eligible:
        summary = await summarize_series(
            session,
            filters=DocumentFilters(sender_id=sender_id, kind_slug=kind_slug),
            settings=settings,
            reference="latest",
        )
        if summary.status != "ok":
            continue
        series.append(serialise_summary(summary, include_points=True))
    return {"series": series}


def _decode_or_404(series_id: str) -> tuple[int, int, str | None]:
    """Decode a ``{sender}-{kind}-{currency}`` id, 404 on a malformed one."""
    try:
        return decode_series_id(series_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series") from exc


async def _summarize_one(
    session: AsyncSession,
    settings: Settings,
    sender_id: int,
    kind_id: int,
    currency: str | None,
) -> SeriesSummary | None:
    """Summarise the single series for one identity, or ``None`` if the kind is
    unknown or the series is too sparse to chart."""
    kind_slug = (
        await session.execute(select(Kind.slug).where(Kind.id == kind_id))
    ).scalar_one_or_none()
    if kind_slug is None:
        return None
    summary = await summarize_series(
        session,
        filters=DocumentFilters(sender_id=sender_id, kind_slug=kind_slug),
        settings=settings,
        reference="latest",
        reference_currency=currency,
    )
    return summary if summary.status == "ok" else None


@router.get(
    "/charts/{series_id}",
    summary="One recurring series by its stable id (single-chart deep link)",
    responses={404: {"description": "Unknown or unchartable series"}},
)
async def get_chart(
    series_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """The single series for ``series_id`` (``{sender}-{kind}-{currency}``),
    summarised exactly like one ``GET /api/charts`` entry. ``404`` when the id is
    malformed or does not resolve to a chartable series."""
    sender_id, kind_id, currency = _decode_or_404(series_id)
    summary = await _summarize_one(session, settings, sender_id, kind_id, currency)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")
    return serialise_summary(summary, include_points=True)


class SeriesMetaRequest(BaseModel):
    """Title/description override for a series. Only the fields present are
    applied (omit one to leave it unchanged; send ``null`` to clear it)."""

    title: str | None = None
    description: str | None = None


@router.put(
    "/charts/{series_id}/meta",
    summary="Override a series' title and/or description",
    responses={404: {"description": "Unknown series"}},
)
async def update_chart_meta(
    series_id: str,
    payload: SeriesMetaRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Upsert the user title/description override for ``series_id`` and return the
    refreshed single-series body. ``404`` when the id is malformed or the
    sender/kind do not exist."""
    sender_id, kind_id, currency = _decode_or_404(series_id)
    sender = await session.get(Sender, sender_id)
    kind = await session.get(Kind, kind_id)
    if sender is None or kind is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")

    fields = payload.model_dump(exclude_unset=True)
    currency_match = (
        SeriesMetaOverride.currency.is_(None)
        if currency is None
        else SeriesMetaOverride.currency == currency
    )
    existing = (
        await session.execute(
            select(SeriesMetaOverride).where(
                SeriesMetaOverride.sender_id == sender_id,
                SeriesMetaOverride.kind_id == kind_id,
                currency_match,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = SeriesMetaOverride(sender_id=sender_id, kind_id=kind_id, currency=currency)
        session.add(existing)
    if "title" in fields:
        existing.title = fields["title"]
    if "description" in fields:
        existing.description = fields["description"]
    # updated_at refreshes via the model's onupdate on an UPDATE; the
    # server_default covers the INSERT of a new row.
    await session.commit()

    summary = await _summarize_one(session, settings, sender_id, kind_id, currency)
    if summary is None:
        # The override persisted, but the series isn't chartable (too sparse):
        # echo just the stored meta so the client can reflect the edit.
        return {
            "sender_id": sender_id,
            "kind_id": kind_id,
            "currency": currency,
            "title": existing.title,
            "description": existing.description,
        }
    return serialise_summary(summary, include_points=True)
