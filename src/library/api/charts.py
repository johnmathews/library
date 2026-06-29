"""Aggregate charts endpoint: every eligible recurring ``(sender, kind)`` series.

Backs the ``/charts`` view. Enumerates the ``(sender_id, kind_id)`` pairs with
enough amount-bearing documents to summarise, then reuses
``library.series.summarize_series`` per pair so each entry has the same shape as
``GET /api/documents/{id}/series`` (stats + points + cached description). Series
whose dominant currency bucket is too small are skipped.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.config import Settings, get_settings
from library.db import get_session
from library.models import (
    AuthoredSeries,
    AuthoredSeriesMember,
    Document,
    Kind,
    Sender,
    SeriesMetaOverride,
    User,
)
from library.search import DocumentFilters
from library.series import (
    SeriesSummary,
    decode_authored_series_id,
    decode_series_id,
    serialise_summary,
    summarize_authored_series,
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

    # Authored (user-curated) series live alongside the emergent ones, newest
    # first. They have no minimum-document gate, so every authored series shows.
    #
    # Intentionally NOT scoped to the current user. Library is a single shared
    # family archive (architecture.md §1.5): every authenticated member sees and
    # edits the same documents, senders, kinds and emergent charts. Authored
    # series follow that model — `owner_id` records who created one (provenance),
    # not an access boundary — so the list is unscoped, exactly like the emergent
    # charts above. Endpoints still require authentication (router dependency).
    authored_ids = (
        (
            await session.execute(
                select(AuthoredSeries.id).order_by(AuthoredSeries.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for authored_id in authored_ids:
        summary = await summarize_authored_series(session, authored_id, settings)
        if summary is None:
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
    """The single series for ``series_id``, summarised exactly like one
    ``GET /api/charts`` entry. ``series_id`` is either an emergent
    ``{sender}-{kind}-{currency}`` id or an authored ``a-{id}`` id. ``404`` when
    the id is malformed or does not resolve to a chartable series."""
    authored_id = decode_authored_series_id(series_id)
    if authored_id is not None:
        summary = await summarize_authored_series(session, authored_id, settings)
        if summary is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")
        return serialise_summary(summary, include_points=True)
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


# --- Authored (user-curated) series (W14) -----------------------------------


class AuthoredSeriesCreate(BaseModel):
    """Body of ``POST /api/charts/authored``."""

    name: str = Field(min_length=1, max_length=255)
    currency: str | None = Field(default=None, max_length=3)
    description: str | None = None
    # Optional initial membership; unknown/deleted ids are silently ignored.
    document_ids: list[int] = Field(default_factory=list)


class AuthoredSeriesUpdate(BaseModel):
    """Body of ``PATCH /api/charts/authored/{id}`` (omit a field to leave it)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class AuthoredMemberRequest(BaseModel):
    """Body of ``POST /api/charts/authored/{id}/members``."""

    document_id: int


async def _get_authored_or_404(session: AsyncSession, authored_id: int) -> AuthoredSeries:
    # Deliberately no owner check: Library is a single shared family archive, so
    # any authenticated member may view and edit any authored series, just as
    # they can any shared document or emergent chart. `owner_id` is provenance,
    # not an access boundary. See the note in list_charts.
    authored = await session.get(AuthoredSeries, authored_id)
    if authored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")
    return authored


async def _existing_document_ids(session: AsyncSession, ids: list[int]) -> list[int]:
    """The subset of ``ids`` that are real, non-deleted documents (order-preserving)."""
    if not ids:
        return []
    rows = (
        await session.execute(
            select(Document.id).where(Document.id.in_(ids), Document.deleted_at.is_(None))
        )
    ).scalars()
    present = set(rows)
    seen: set[int] = set()
    ordered: list[int] = []
    for did in ids:
        if did in present and did not in seen:
            seen.add(did)
            ordered.append(did)
    return ordered


async def _authored_member_ids(session: AsyncSession, authored_id: int) -> set[int]:
    rows = (
        await session.execute(
            select(AuthoredSeriesMember.document_id).where(
                AuthoredSeriesMember.authored_series_id == authored_id
            )
        )
    ).scalars()
    return set(rows)


async def _authored_body(
    session: AsyncSession, settings: Settings, authored_id: int
) -> dict[str, object]:
    """The refreshed single-series body for an authored series."""
    summary = await summarize_authored_series(session, authored_id, settings)
    if summary is None:  # pragma: no cover - the caller just created/loaded it
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")
    return serialise_summary(summary, include_points=True)


@router.post(
    "/charts/authored",
    status_code=status.HTTP_201_CREATED,
    summary="Create an authored (manual) series",
)
async def create_authored_series(
    payload: AuthoredSeriesCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Create a user-curated series and (optionally) seed its membership. Returns
    the series summarised exactly like one ``GET /api/charts`` entry."""
    authored = AuthoredSeries(
        name=payload.name.strip(),
        description=payload.description,
        currency=payload.currency,
        owner_id=user.id,
    )
    session.add(authored)
    await session.flush()
    for document_id in await _existing_document_ids(session, payload.document_ids):
        session.add(AuthoredSeriesMember(authored_series_id=authored.id, document_id=document_id))
    await session.commit()
    return await _authored_body(session, settings, authored.id)


@router.patch(
    "/charts/authored/{authored_id}",
    summary="Rename / re-describe an authored series",
    responses={404: {"description": "Unknown series"}},
)
async def update_authored_series(
    authored_id: int,
    payload: AuthoredSeriesUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Update an authored series' name and/or description; returns the refreshed body."""
    authored = await _get_authored_or_404(session, authored_id)
    fields = payload.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        authored.name = fields["name"].strip()
    if "description" in fields:
        authored.description = fields["description"]
    await session.commit()
    return await _authored_body(session, settings, authored_id)


@router.delete(
    "/charts/authored/{authored_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an authored series",
    responses={404: {"description": "Unknown series"}},
)
async def delete_authored_series(
    authored_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete an authored series (its membership rows cascade)."""
    authored = await _get_authored_or_404(session, authored_id)
    await session.delete(authored)
    await session.commit()


@router.post(
    "/charts/authored/{authored_id}/members",
    summary="Add a document to an authored series",
    responses={404: {"description": "Unknown series or document"}},
)
async def add_authored_member(
    authored_id: int,
    payload: AuthoredMemberRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Add a document to an authored series (idempotent); returns the refreshed body."""
    await _get_authored_or_404(session, authored_id)
    document = await session.get(Document, payload.document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document")
    if payload.document_id not in await _authored_member_ids(session, authored_id):
        session.add(
            AuthoredSeriesMember(authored_series_id=authored_id, document_id=payload.document_id)
        )
        await session.commit()
    return await _authored_body(session, settings, authored_id)


@router.delete(
    "/charts/authored/{authored_id}/members/{document_id}",
    summary="Remove a document from an authored series",
    responses={404: {"description": "Unknown series"}},
)
async def remove_authored_member(
    authored_id: int,
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Remove a document from an authored series (idempotent); returns the refreshed body."""
    await _get_authored_or_404(session, authored_id)
    member = (
        await session.execute(
            select(AuthoredSeriesMember).where(
                AuthoredSeriesMember.authored_series_id == authored_id,
                AuthoredSeriesMember.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        await session.delete(member)
        await session.commit()
    return await _authored_body(session, settings, authored_id)
