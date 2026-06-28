"""Manual series-membership editing: pin/exclude documents in a recurring series.

Series are computed on the fly (``library.series``); these endpoints persist
durable *overrides* to that computation. Adding and removing are idempotent
toggles between three states for a ``(series, document)`` pair:

- ``pinned``   — a ``pin`` override exists (force-include this document).
- ``excluded`` — an ``exclude`` override exists (force-remove it).
- ``cleared``  — no override; the document follows the natural grouping.

Adding clears an existing exclude (else pins); removing clears an existing pin
(else excludes). See ``docs/api.md`` §1.15 and ``SeriesMembershipOverride``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.models import Document, Kind, OverrideAction, Sender, SeriesMembershipOverride

router = APIRouter(tags=["series"])

CurrencyParam = Annotated[
    str | None,
    Query(max_length=3, description="The series currency bucket (omit for the NULL bucket)."),
]


class SeriesMemberRequest(BaseModel):
    """Body of a membership add: the document to pin into the series."""

    document_id: int


async def _require_series(session: AsyncSession, sender_id: int, kind_id: int) -> None:
    """404 unless both the sender and the kind exist."""
    sender = await session.get(Sender, sender_id)
    kind = await session.get(Kind, kind_id)
    if sender is None or kind is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown series")


async def _require_document(session: AsyncSession, document_id: int) -> None:
    if await session.get(Document, document_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document")


async def _find_override(
    session: AsyncSession,
    sender_id: int,
    kind_id: int,
    currency: str | None,
    document_id: int,
) -> SeriesMembershipOverride | None:
    currency_match = (
        SeriesMembershipOverride.currency.is_(None)
        if currency is None
        else SeriesMembershipOverride.currency == currency
    )
    statement = select(SeriesMembershipOverride).where(
        SeriesMembershipOverride.sender_id == sender_id,
        SeriesMembershipOverride.kind_id == kind_id,
        currency_match,
        SeriesMembershipOverride.document_id == document_id,
    )
    return (await session.execute(statement)).scalar_one_or_none()


def _result(
    sender_id: int, kind_id: int, currency: str | None, document_id: int, state: str
) -> dict[str, object]:
    return {
        "state": state,
        "sender_id": sender_id,
        "kind_id": kind_id,
        "currency": currency,
        "document_id": document_id,
    }


@router.post(
    "/series/{sender_id}/{kind_id}/members",
    summary="Add a document to a series (pin, or clear an existing exclude)",
    responses={404: {"description": "Unknown series or document"}},
)
async def add_series_member(
    sender_id: int,
    kind_id: int,
    payload: SeriesMemberRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    currency: CurrencyParam = None,
) -> dict[str, object]:
    """Force-include a document. Clears an existing ``exclude`` if present;
    otherwise records a ``pin``. Idempotent when already pinned."""
    await _require_series(session, sender_id, kind_id)
    await _require_document(session, payload.document_id)
    existing = await _find_override(session, sender_id, kind_id, currency, payload.document_id)
    if existing is not None and existing.action == OverrideAction.EXCLUDE:
        await session.delete(existing)
        await session.commit()
        return _result(sender_id, kind_id, currency, payload.document_id, "cleared")
    if existing is None:
        session.add(
            SeriesMembershipOverride(
                sender_id=sender_id,
                kind_id=kind_id,
                currency=currency,
                document_id=payload.document_id,
                action=OverrideAction.PIN,
            )
        )
        await session.commit()
    return _result(sender_id, kind_id, currency, payload.document_id, "pinned")


@router.delete(
    "/series/{sender_id}/{kind_id}/members/{document_id}",
    summary="Remove a document from a series (exclude, or clear an existing pin)",
    responses={404: {"description": "Unknown series or document"}},
)
async def remove_series_member(
    sender_id: int,
    kind_id: int,
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    currency: CurrencyParam = None,
) -> dict[str, object]:
    """Force-remove a document. Clears an existing ``pin`` if present; otherwise
    records an ``exclude``. Idempotent when already excluded."""
    await _require_series(session, sender_id, kind_id)
    await _require_document(session, document_id)
    existing = await _find_override(session, sender_id, kind_id, currency, document_id)
    if existing is not None and existing.action == OverrideAction.PIN:
        await session.delete(existing)
        await session.commit()
        return _result(sender_id, kind_id, currency, document_id, "cleared")
    if existing is None:
        session.add(
            SeriesMembershipOverride(
                sender_id=sender_id,
                kind_id=kind_id,
                currency=currency,
                document_id=document_id,
                action=OverrideAction.EXCLUDE,
            )
        )
        await session.commit()
    return _result(sender_id, kind_id, currency, document_id, "excluded")
