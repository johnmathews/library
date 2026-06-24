"""Aggregate charts endpoint: every eligible recurring ``(sender, kind)`` series.

Backs the ``/charts`` view. Enumerates the ``(sender_id, kind_id)`` pairs with
enough amount-bearing documents to summarise, then reuses
``library.series.summarize_series`` per pair so each entry has the same shape as
``GET /api/documents/{id}/series`` (stats + points + cached description). Series
whose dominant currency bucket is too small are skipped.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings, get_settings
from library.db import get_session
from library.models import Document, Kind
from library.search import DocumentFilters
from library.series import serialise_summary, summarize_series

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
