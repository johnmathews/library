"""Manual allocation of a document's amount across spend lines.

Replace-whole rather than patch-one: a document's allocation is only ever
valid as a complete set, so a partial write has no meaning. Every mutation
goes through one transaction and the deferred constraint trigger checks the
sum once, at commit, rather than row by row.

Everything that can be refused by name is refused before the first row is
written — the amount scale, the sum, and every facet label — because the
trigger is a backstop, not an error message: it fires at COMMIT and arrives as
a bare ``DBAPIError``, which is a 500 where the caller deserves a 400.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Facet, FacetValue, LineLabel, SpendLine

#: Decimal places `spend_lines.amount` can hold. A line carrying more is
#: rejected rather than quantized: rounding the owner's numbers without saying
#: so is exactly the silence this feature exists to remove, and three lines of
#: `33.333` sum to `100.000` in Python while the stored rows sum to `99.99`.
AMOUNT_SCALE = 2

#: SQLSTATE of a plpgsql ``RAISE`` — and, in this schema, of nothing else:
#: migration 0035's pair of sum triggers hold the only ``RAISE EXCEPTION`` in
#: any migration. **Observed, not assumed**: under asyncpg the trigger arrives
#: as ``sqlalchemy.exc.DBAPIError`` with ``exc.orig.sqlstate == "P0001"``, while
#: a deferred unique violation at the same commit arrives as ``IntegrityError``
#: with ``"23505"`` — which is exactly the error a broad ``except DBAPIError``
#: would have mislabelled as an unbalanced allocation.
ALLOCATION_TRIGGER_SQLSTATE = "P0001"

#: The refusal handed to `commit_allocation` by the two writers that edit a
#: document's `amount_total` in place — `PATCH /api/documents/{id}` and Ask's
#: document-edit tool. It lived twice, typed out identically in both, with
#: `docs/charts.md` §10.1 telling the reader they were "the same wording" and
#: nothing enforcing it. The other three writers of `amount_total` answer
#: differently on purpose (the allocation routes name the sum, re-extraction
#: skips the field, the importer never reaches the case), so this is shared by
#: the two that really do give the same advice — not by all of them.
AMOUNT_ALLOCATED_REFUSAL = (
    "this document's amount is allocated across spend lines that sum to "
    "the old amount; clear or replace its spend lines before changing it"
)


class AllocationError(ValueError):
    """The proposed lines do not form a valid allocation."""


async def commit_allocation(session: AsyncSession, *, refusal: str) -> None:
    """Commit, turning the sum triggers' refusal into ``AllocationError``.

    0035's triggers are ``DEFERRABLE INITIALLY DEFERRED`` and fire at COMMIT,
    so they surface here rather than at the statement that broke the invariant
    — and under asyncpg they arrive as a bare ``DBAPIError`` rather than an
    ``IntegrityError``. Uncaught, that is a 500 on a refusal the caller can
    both understand and act on.

    Shared, because the triggers are a *pair*: one on ``spend_lines`` and one
    on ``documents``, so the allocation invariant is broken from the amount
    side as easily as from the line side. Every writer of ``amount_total``
    that commits has to translate this, and only the allocation routes used to.

    **Only that one refusal.** ``DBAPIError`` is also every deadlock, lock
    timeout, dropped connection and foreign-key violation; reporting those as
    an allocation problem would give the caller a wrong diagnosis and hide a
    real defect behind a plausible message, never reaching a 5xx. So the
    SQLSTATE is checked and anything else is re-raised untouched.

    ``refusal`` is the caller's own wording, because only the caller knows
    which side of the invariant it was writing — "the lines do not sum to the
    total" and "the amount no longer matches the lines" are the same trigger
    and different advice. Postgres' raw text is never echoed into it: the
    diagnosis is ours, the trigger's payload is not the client's business.
    """
    try:
        await session.commit()
    except DBAPIError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) != ALLOCATION_TRIGGER_SQLSTATE:
            raise
        raise AllocationError(refusal) from exc


class LineInput(BaseModel):
    amount: Decimal
    note: str | None = None
    #: facet key -> facet value key. Only facets that DIFFER from the
    #: document's own labels need to appear; the rest are inherited by
    #: `spend_facts`, not copied here.
    labels: dict[str, str] = {}


async def replace_lines(
    session: AsyncSession, document_id: int, lines: Sequence[LineInput]
) -> list[SpendLine]:
    """Replace a document's whole allocation. Raises ``AllocationError`` unless
    the lines sum exactly to ``amount_total`` in whole cents."""
    row = (
        await session.execute(
            select(Document.id, Document.amount_total).where(Document.id == document_id)
        )
    ).one_or_none()
    if row is None:
        raise AllocationError(f"no document with id {document_id}")
    total = row[1]
    if total is None:
        raise AllocationError("a document with no amount cannot be allocated")
    for line in lines:
        _check_scale(line.amount)
    proposed = sum((line.amount for line in lines), Decimal("0"))
    if proposed != total:
        raise AllocationError(f"lines sum to {proposed} but the document total is {total}")

    # Every label is resolved before anything is deleted or inserted. Resolving
    # inline would leave a caller that catches AllocationError holding a session
    # whose old allocation is already gone and whose new one is half written —
    # an unbalanced set that only the trigger would catch, as a 500.
    resolved: list[list[tuple[int, int]]] = [
        [
            await _resolve(session, facet_key, value_key)
            for facet_key, value_key in line.labels.items()
        ]
        for line in lines
    ]

    await clear_lines(session, document_id)
    created: list[SpendLine] = []
    for line, line_labels in zip(lines, resolved, strict=True):
        line_row = SpendLine(document_id=document_id, amount=line.amount, note=line.note)
        session.add(line_row)
        await session.flush()
        for facet_id, facet_value_id in line_labels:
            session.add(
                LineLabel(line_id=line_row.id, facet_id=facet_id, facet_value_id=facet_value_id)
            )
        created.append(line_row)
    await session.flush()
    return created


async def clear_lines(session: AsyncSession, document_id: int) -> None:
    """Remove a document's whole allocation. `line_labels` cascades."""
    await session.execute(delete(SpendLine).where(SpendLine.document_id == document_id))
    await session.flush()


def _check_scale(amount: Decimal) -> None:
    """Refuse an amount the ``Numeric(14, 2)`` column cannot hold exactly."""
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -AMOUNT_SCALE:
        raise AllocationError(f"line amount {amount} has more than {AMOUNT_SCALE} decimal places")


async def _resolve(session: AsyncSession, facet_key: str, value_key: str) -> tuple[int, int]:
    """Facet key + value key -> (facet_id, facet_value_id).

    Resolved here rather than accepting raw ids so a caller cannot pair a
    facet with another facet's value; the composite foreign key would catch
    it, but a 500 is a worse answer than a named error.
    """
    row = (
        await session.execute(
            select(FacetValue.facet_id, FacetValue.id)
            .join(Facet, Facet.id == FacetValue.facet_id)
            .where(Facet.key == facet_key, FacetValue.key == value_key)
        )
    ).one_or_none()
    if row is None:
        raise AllocationError(f"no value '{value_key}' in facet '{facet_key}'")
    return int(row[0]), int(row[1])
