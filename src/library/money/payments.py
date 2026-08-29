"""Which documents describe one payment.

The rules live in the ``payment_edges`` SQL view rather than in Python, so a
later chart query can join payment identity without reimplementing it. This
module is the read API over those views, plus the one write: an override row.

The rules, in the order the view applies them:

  VETO  both documents carry a reference and they differ -> never merge
  R2    same sender, same non-null reference             -> merge at any date gap
  R1    same sender, date, amount, currency              -> merge
  R3    same sender, amount, currency; complementary
        amount_kind (due <-> made); gap <= 60 days       -> merge

R3's complementarity requirement is what makes date-tolerant merging safe: an
invoice and its receipt are never the same kind of amount, while two genuinely
separate purchases of the same value always are. No date tolerance alone can
separate those cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import PaymentOverride

OverrideKind = Literal["MERGE", "SPLIT"]


async def payment_id_for(session: AsyncSession, document_id: int) -> int | None:
    """The payment this document belongs to, or None if it is deleted/absent."""
    result = await session.execute(
        text("SELECT payment_id FROM payments WHERE document_id = :d"), {"d": document_id}
    )
    row = result.one_or_none()
    return int(row[0]) if row is not None else None


async def payment_group(session: AsyncSession, document_id: int) -> list[int]:
    """Every document sharing this one's payment, ascending. Includes itself.

    Returns ``[]`` for a deleted or unknown document.
    """
    result = await session.execute(
        text(
            "SELECT document_id FROM payments "
            "WHERE payment_id = (SELECT payment_id FROM payments WHERE document_id = :d) "
            "ORDER BY document_id"
        ),
        {"d": document_id},
    )
    return [int(row[0]) for row in result.all()]


async def add_override(session: AsyncSession, kind: OverrideKind, doc_a: int, doc_b: int) -> None:
    """Record a human correction. Orders the pair — ``doc_a < doc_b`` is a check
    constraint, so an unordered insert would be rejected."""
    if doc_a == doc_b:
        raise ValueError("an override needs two distinct documents")
    low, high = (doc_a, doc_b) if doc_a < doc_b else (doc_b, doc_a)
    await session.execute(
        pg_insert(PaymentOverride)
        .values(kind=kind, doc_a=low, doc_b=high)
        .on_conflict_do_nothing(constraint="payment_overrides_unique")
    )


async def collapse_counts(session: AsyncSession, document_ids: Sequence[int]) -> tuple[int, int]:
    """``(payments, documents)`` across a set of documents.

    This is the "12 payments from 15 documents" line a chart footer shows, which
    is what makes a residual double-count visible rather than merely suspected.
    """
    if not document_ids:
        return 0, 0
    result = await session.execute(
        text(
            "SELECT count(DISTINCT payment_id), count(*) FROM payments "
            "WHERE document_id = ANY(:ids)"
        ),
        {"ids": list(document_ids)},
    )
    payments, documents = result.one()
    return int(payments), int(documents)
