"""Held-emails REST API: list, inspect, ingest-anyway, and dismiss holds.

Authentication is enforced at include level in app.py (session cookie or
bearer token); see docs/api.md §1.9.

An email the poller judged not library-worthy sits in ``held_emails`` (with
its original message in the IMAP Held folder) until a human resolves it:
"ingest anyway" defers the ``library.jobs.ingest_held_email`` override task,
"dismiss" flips the row without touching IMAP — the bytes stay recoverable
forever. See ``library.email_ingest`` for the hold/override semantics.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.email_ingest import dismiss_held_email
from library.jobs import ingest_held_email
from library.models import HeldEmail, HeldEmailStatus, User
from library.schemas import (
    HeldEmailDetail,
    HeldEmailIngestQueuedResponse,
    HeldEmailItem,
    HeldEmailListResponse,
)

router: APIRouter = APIRouter(tags=["held-emails"])

#: ?status= filter values; "all" disables the predicate. Defaults to the open
#: queue (held), which is what the review UI polls.
StatusParam = Annotated[
    Literal["held", "ingested", "dismissed", "all"],
    Query(
        alias="status",
        description=(
            "Lifecycle filter: `held` (default, the open queue), a resolved state, or `all`."
        ),
    ),
]


def _owner_label(owner: User | None) -> str | None:
    """The owner's human label: display name, falling back to username."""
    if owner is None:
        return None
    return owner.display_name.strip() or owner.username


def _item_fields(row: HeldEmail, owner: User | None) -> dict[str, object]:
    return {
        "id": row.id,
        "message_id": row.message_id,
        "sender": row.sender,
        "subject": row.subject,
        "received_at": row.received_at,
        "created_at": row.created_at,
        "verdict": row.verdict,
        "reason": row.reason,
        "status": row.status,
        "owner_id": row.owner_id,
        "owner": _owner_label(owner),
        "resolved_at": row.resolved_at,
        "document_ids": list(row.document_ids or []),
        "last_error": row.last_error,
    }


def _detail(row: HeldEmail, owner: User | None) -> HeldEmailDetail:
    return HeldEmailDetail(**_item_fields(row, owner), trace=dict(row.trace or {}))  # type: ignore[arg-type]


async def _get_row_or_404(session: AsyncSession, held_email_id: int) -> HeldEmail:
    row = await session.get(HeldEmail, held_email_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="held email not found")
    return row


async def _owner_of(session: AsyncSession, row: HeldEmail) -> User | None:
    if row.owner_id is None:
        return None
    return await session.get(User, row.owner_id)


@router.get(
    "/held-emails",
    response_model=HeldEmailListResponse,
    summary="List held emails",
)
async def list_held_emails(
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: StatusParam = "held",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HeldEmailListResponse:
    """The hold-for-review queue, newest-held first.

    Defaults to the open (``held``) rows; resolved rows stay queryable as an
    audit trail via ``?status=ingested|dismissed|all``.
    """
    query = select(HeldEmail)
    count_query = select(func.count()).select_from(HeldEmail)
    if status_filter != "all":
        condition = HeldEmail.status == HeldEmailStatus(status_filter)
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = (await session.execute(count_query)).scalar_one()
    result = await session.execute(
        query.outerjoin(User, User.id == HeldEmail.owner_id)
        .add_columns(User)
        .order_by(HeldEmail.created_at.desc(), HeldEmail.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [HeldEmailItem(**_item_fields(row, owner)) for row, owner in result.all()]  # type: ignore[arg-type]
    return HeldEmailListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/held-emails/{held_email_id}",
    response_model=HeldEmailDetail,
    summary="Held email detail (with the full decision trace)",
    responses={404: {"description": "Unknown held email"}},
)
async def get_held_email(
    held_email_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HeldEmailDetail:
    """Everything the poller recorded about one hold, including the per-item trace."""
    row = await _get_row_or_404(session, held_email_id)
    return _detail(row, await _owner_of(session, row))


@router.post(
    "/held-emails/{held_email_id}/ingest",
    response_model=HeldEmailIngestQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a held email anyway (queue the override task)",
    responses={
        404: {"description": "Unknown held email"},
        409: {"description": "Already resolved (not currently held)"},
    },
)
async def queue_held_email_ingest(
    held_email_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> HeldEmailIngestQueuedResponse:
    """Defer ``library.jobs.ingest_held_email`` for this row and return immediately.

    The task re-fetches the message by Message-ID and ingests it with override
    semantics; track the outcome via the row's ``status``/``document_ids``/
    ``last_error`` (GET detail) or GET /api/jobs. 409 when the row is already
    resolved — the task itself also no-ops on a non-held row, so a race here
    can never double-ingest.
    """
    row = await _get_row_or_404(session, held_email_id)
    if row.status is not HeldEmailStatus.HELD:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"held email {held_email_id} is already {row.status.value}",
        )
    job_id = await ingest_held_email.defer_async(held_email_id=row.id, resolved_by_id=user.id)
    return HeldEmailIngestQueuedResponse(queued=True, job_id=job_id)


@router.post(
    "/held-emails/{held_email_id}/dismiss",
    response_model=HeldEmailDetail,
    summary="Dismiss a held email (DB-only; the message stays in the Held folder)",
    responses={
        404: {"description": "Unknown held email"},
        409: {"description": "Already resolved (not currently held)"},
    },
)
async def dismiss_held_email_endpoint(
    held_email_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> HeldEmailDetail:
    """Flip the row to ``dismissed`` — instant, no IMAP round-trip.

    The original message stays in the Held folder, so a wrong dismiss never
    loses the bytes.
    """
    await _get_row_or_404(session, held_email_id)
    try:
        row = await dismiss_held_email(session, held_email_id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _detail(row, await _owner_of(session, row))
