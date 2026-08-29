"""Which documents describe one payment.

The rules live in the ``payment_edges`` SQL view rather than in Python, so a
later chart query can join payment identity without reimplementing it. This
module is the read API over those views, plus the one write: an override row.

The rules, in the order the view applies them:

  VETO  both documents carry a reference and they differ -> never merge
  R2    same sender, same non-null reference             -> merge at any date gap
  R1    same sender, date, amount, currency              -> merge
  R3    same sender, amount, currency; complementary
        amount_kind (due <-> made); gap <= 60 days; and
        each is the other's NEAREST such partner         -> merge

R3's complementarity requirement is what makes date-tolerant merging safe: an
invoice and its receipt are never the same kind of amount, while two genuinely
separate purchases of the same value always are. No date tolerance alone can
separate those cases.

Mutual-nearest is what keeps R3 from chaining. A recurring charge documented
as invoice-then-receipt puts every cycle's receipt within 60 days of the NEXT
cycle's invoice, and those two are complementary too — so an R3 that fired on
every complementary pair inside the window would link cycle to cycle and the
recursive closure would collapse a whole subscription history into one
payment.

"Nearest" is DIRECTIONAL, because the domain is: a payment follows the thing
it pays. Every candidate receipt dated on or after its invoice outranks every
candidate dated before it, and distance decides only within those two groups.
Unsigned distance is not enough on its own — a 1st/16th cadence ties at 15
days each way, and a short February makes the next cycle's invoice the closer
one — and a receipt whose reference contradicts an invoice's is left out of
the ranking entirely, so it cannot hold a slot it could never use. The cost is
one shape: a systematically reversed cadence (charged, then invoiced days
later) pairs off by one cycle. See docs/money-facts.md §5.

Human corrections beat the rules, and the LATEST correction on a pair wins:
a SPLIT suppresses the rule-derived edge outright, and a MERGE recorded after
it re-adds an explicit one (see ``add_override``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import func, text
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
    constraint, so an unordered insert would be rejected.

    Re-recording a correction that already exists refreshes its ``created_at``
    rather than doing nothing. A pair can carry both a MERGE and a SPLIT row
    (the unique constraint is on the ``(kind, doc_a, doc_b)`` triple), and the
    ``payment_edges`` view resolves that by timestamp — the later correction
    wins. Leaving the timestamp untouched would make the *third* correction on
    a pair a silent no-op: merge, split, merge again would keep the pair split
    because the second MERGE would still carry the first one's timestamp. The
    outcome of repeating a correction with no opposite one in between is
    unchanged, which is the sense in which it stays idempotent.
    """
    if doc_a == doc_b:
        raise ValueError("an override needs two distinct documents")
    low, high = (doc_a, doc_b) if doc_a < doc_b else (doc_b, doc_a)
    await session.execute(
        pg_insert(PaymentOverride)
        .values(kind=kind, doc_a=low, doc_b=high)
        .on_conflict_do_update(
            constraint="payment_overrides_unique", set_={"created_at": func.now()}
        )
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
