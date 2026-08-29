"""Payment identity endpoints.

`/api/payments/duplicates` is the review surface this layer exists for: it makes
the archive's double-counted documents visible and correctable before any chart
is built on top of them. Authentication is enforced at include level in
app.py, like every other router.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.models import Document
from library.money.payments import add_override, payment_group, payment_id_for

router: APIRouter = APIRouter(tags=["payments"])


class OverrideRequest(BaseModel):
    doc_a: int
    doc_b: int

    @model_validator(mode="after")
    def _distinct(self) -> "OverrideRequest":
        if self.doc_a == self.doc_b:
            raise ValueError("doc_a and doc_b must be different documents")
        return self


class PaymentDocument(BaseModel):
    id: int
    title: str | None
    document_date: str | None
    amount_kind: str | None
    reference: str | None


class PaymentOut(BaseModel):
    payment_id: int
    documents: list[PaymentDocument]


class DuplicateGroup(BaseModel):
    payment_id: int
    document_ids: list[int]
    count: int


class DuplicatesOut(BaseModel):
    groups: list[DuplicateGroup]


async def _require_both_exist(session: AsyncSession, doc_a: int, doc_b: int) -> None:
    """Both ends of an override must be real, live documents.

    Without this, a typo'd id reaches ``add_override``'s FK as an uncaught
    ``IntegrityError`` (a 500). These endpoints are the only way to correct
    payment identity, so a routine operator mistake must get a 404, not a
    crash. A soft-deleted document is treated as absent, same as everywhere
    else in this API.
    """
    found = (
        (
            await session.execute(
                select(Document.id).where(
                    Document.id.in_((doc_a, doc_b)), Document.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    missing = {doc_a, doc_b} - set(found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown document(s): {sorted(missing)}",
        )


async def _payment_body(session: AsyncSession, document_id: int) -> PaymentOut:
    payment_id = await payment_id_for(session, document_id)
    if payment_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    ids = await payment_group(session, document_id)
    rows = (
        await session.execute(
            select(
                Document.id,
                Document.title,
                Document.document_date,
                Document.amount_kind,
                Document.reference,
            )
            .where(Document.id.in_(ids))
            .order_by(Document.id)
        )
    ).all()
    return PaymentOut(
        payment_id=payment_id,
        documents=[
            PaymentDocument(
                id=row.id,
                title=row.title,
                document_date=row.document_date.isoformat() if row.document_date else None,
                amount_kind=row.amount_kind.value if row.amount_kind else None,
                reference=row.reference,
            )
            for row in rows
        ],
    )


@router.get("/documents/{document_id}/payment", summary="The payment this document belongs to")
async def get_payment(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    return await _payment_body(session, document_id)


@router.post("/payments/merge", summary="Record that two documents are one payment")
async def merge_payment(
    body: OverrideRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    await _require_both_exist(session, body.doc_a, body.doc_b)
    await add_override(session, "MERGE", body.doc_a, body.doc_b)
    await session.commit()
    return await _payment_body(session, body.doc_a)


@router.post("/payments/split", summary="Record that two documents are separate payments")
async def split_payment(
    body: OverrideRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> PaymentOut:
    await _require_both_exist(session, body.doc_a, body.doc_b)
    await add_override(session, "SPLIT", body.doc_a, body.doc_b)
    await session.commit()
    return await _payment_body(session, body.doc_a)


@router.get("/payments/duplicates", summary="Documents that describe one payment")
async def list_duplicates(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DuplicatesOut:
    result = await session.execute(
        text(
            "SELECT payment_id, array_agg(document_id ORDER BY document_id) AS ids, "
            "count(*) AS n FROM payments GROUP BY payment_id HAVING count(*) > 1 "
            "ORDER BY n DESC, payment_id LIMIT 100"
        )
    )
    return DuplicatesOut(
        groups=[
            DuplicateGroup(
                payment_id=int(row.payment_id), document_ids=list(row.ids), count=int(row.n)
            )
            for row in result.all()
        ]
    )
