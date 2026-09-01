"""The allocation write path.

Every case here is shaped so that a plausible-but-wrong implementation goes
red: the sum check is exercised from both directions, the replace is run
A -> B -> A because a one-way run proves nothing, and the deferred trigger is
proved deferred by inserting a set whose first row alone does not balance.

The last two cases are the ones the invariant would otherwise be half-kept
without: the sum is enforced from the `documents` side too, and a line label
cannot claim one facet while pointing at another facet's value. Both assert the
exception class an executed prototype against Postgres 17 actually produced.
Under **asyncpg** a plpgsql `RAISE EXCEPTION` arrives as a bare
`sqlalchemy.exc.DBAPIError` wrapping the adapter's generic
`sqlalchemy.dialects.postgresql.asyncpg.Error` — not the `ProgrammingError`
psycopg produces for the same raise. The composite-foreign-key violation, by
contrast, does map, and arrives as `IntegrityError`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import LineLabel, SpendLine
from library.spend_lines import AllocationError, LineInput, clear_lines, replace_lines
from tests.conftest import DocumentFactory

pytestmark = pytest.mark.integration


async def _line_count(session: AsyncSession, document_id: int) -> int:
    query = select(func.count()).select_from(SpendLine).where(SpendLine.document_id == document_id)
    return int((await session.execute(query)).scalar_one())


@pytest.mark.asyncio
async def test_lines_that_sum_to_the_total_are_accepted(
    session: AsyncSession, document: DocumentFactory
) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    lines = await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    assert [line.amount for line in lines] == [Decimal("60.00"), Decimal("40.00")]


@pytest.mark.asyncio
async def test_lines_that_undershoot_are_rejected(
    session: AsyncSession, document: DocumentFactory
) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError):
        await replace_lines(session, doc.id, [LineInput(amount=Decimal("60.00"))])


@pytest.mark.asyncio
async def test_lines_that_overshoot_are_rejected(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The other direction. A check written as `sum <= total` passes the
    undershoot test and fails only here."""
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError):
        await replace_lines(
            session,
            doc.id,
            [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("60.00"))],
        )


@pytest.mark.asyncio
async def test_the_sum_constraint_is_deferred_within_the_transaction(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """A two-line split must insert as one transaction.

    An IMMEDIATE constraint fails on the first row, because 60 != 100 at that
    instant. This is the case the DEFERRABLE INITIALLY DEFERRED trigger
    exists for, and it is invisible in any single-line test.
    """
    doc = await document(amount_total=Decimal("100.00"))
    lines = await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    assert len(lines) == 2
    # Commit, or the deferred trigger never runs and this asserts nothing about
    # deferral at all.
    await session.commit()
    assert await _line_count(session, doc.id) == 2


@pytest.mark.asyncio
async def test_replacing_an_allocation_and_restoring_it_returns_the_original(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """A -> B -> A. Running a reversible operation one way proves nothing."""
    doc = await document(amount_total=Decimal("100.00"))
    original = [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))]
    await replace_lines(session, doc.id, original)
    await replace_lines(
        session,
        doc.id,
        [
            LineInput(amount=Decimal("25.00")),
            LineInput(amount=Decimal("25.00")),
            LineInput(amount=Decimal("50.00")),
        ],
    )
    restored = await replace_lines(session, doc.id, original)
    assert [line.amount for line in restored] == [Decimal("60.00"), Decimal("40.00")]
    assert len(restored) == 2, "the three-line allocation must be gone, not appended to"
    await session.commit()
    assert await _line_count(session, doc.id) == 2


@pytest.mark.asyncio
async def test_clearing_lines_leaves_the_document_unsplit(
    session: AsyncSession, document: DocumentFactory
) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await clear_lines(session, doc.id)
    assert await _line_count(session, doc.id) == 0
    # The trigger's `EXISTS (SELECT 1 FROM spend_lines ...)` escape hatch is what
    # makes this legal at commit; without it the cleared allocation is read as
    # "0 != 100" and the commit raises.
    await session.commit()
    assert await _line_count(session, doc.id) == 0


@pytest.mark.asyncio
async def test_a_line_label_must_belong_to_the_facet_it_claims(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """`services` is a `category` value, not a `scope` one.

    Refused by name before it reaches the database: the composite foreign key
    would also catch it (see the test below), but a 500 is a worse answer than
    a named error.
    """
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError):
        await replace_lines(
            session,
            doc.id,
            [LineInput(amount=Decimal("100.00"), labels={"scope": "services"})],
        )


@pytest.mark.asyncio
async def test_the_database_refuses_a_cross_facet_line_label(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """The composite foreign key, not a convention.

    Written against the table rather than through `replace_lines`, because the
    write path's own check would otherwise be the only thing under test. Without
    the constraint a line can claim facet `scope` while pointing at a `category`
    value, and the GROUP BY invariant silently breaks.
    """
    doc = await document(amount_total=Decimal("100.00"))
    lines = await replace_lines(session, doc.id, [LineInput(amount=Decimal("100.00"))])
    scope_id = (
        await session.execute(text("SELECT id FROM facets WHERE key = 'scope'"))
    ).scalar_one()
    services_id = (
        await session.execute(
            text(
                "SELECT v.id FROM facet_values v JOIN facets f ON f.id = v.facet_id"
                " WHERE f.key = 'category' AND v.key = 'services'"
            )
        )
    ).scalar_one()

    session.add(LineLabel(line_id=lines[0].id, facet_id=scope_id, facet_value_id=services_id))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_the_document_total_cannot_be_edited_out_from_under_an_allocation(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The invariant holds from the `documents` side too.

    `amount_total` is writable from three live paths (the documents PATCH,
    re-extraction, the importer). Without the mirror trigger, correcting a
    total after allocating leaves the lines summing to the old figure and every
    chart total for that document quietly wrong, with nothing in the footer to
    say so.

    The trigger is DEFERRABLE INITIALLY DEFERRED, so the UPDATE statement itself
    succeeds and only the COMMIT raises — a test that merely flushed would pass
    against no trigger at all.
    """
    doc = await document(amount_total=Decimal("100.00"))
    # Held as a plain int: the rollback below expires every ORM attribute, and
    # re-reading `doc.id` afterwards would go to the database from a sync
    # attribute access and raise MissingGreenlet instead of asserting anything.
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    await session.execute(
        text("UPDATE documents SET amount_total = CAST('120.00' AS numeric) WHERE id = :d"),
        {"d": document_id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="spend lines for document"):
        await session.commit()
    await session.rollback()

    still = await session.scalar(
        text("SELECT amount_total FROM documents WHERE id = :d"), {"d": document_id}
    )
    assert still == Decimal("100.00"), "the rejected edit must not have landed"


@pytest.mark.asyncio
async def test_a_document_with_no_lines_can_have_its_total_corrected(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The common case must stay writable — the mirror trigger only bites when
    an allocation exists."""
    doc = await document(amount_total=Decimal("100.00"))
    await session.execute(
        text("UPDATE documents SET amount_total = CAST('120.00' AS numeric) WHERE id = :d"),
        {"d": doc.id},
    )
    await session.commit()
    corrected = await session.scalar(
        text("SELECT amount_total FROM documents WHERE id = :d"), {"d": doc.id}
    )
    assert corrected == Decimal("120.00")


@pytest.mark.asyncio
async def test_deleting_an_allocated_document_takes_its_lines_with_it(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The cascade fires the line trigger for a document that no longer exists.

    At commit it has no lines left, so the `EXISTS` escape hatch lets it
    through. Getting this wrong makes an allocated document undeletable.
    """
    doc = await document(amount_total=Decimal("100.00"))
    await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    await session.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc.id})
    await session.commit()
    assert await _line_count(session, doc.id) == 0


@pytest.mark.asyncio
async def test_a_line_carries_its_facet_label(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """The split the whole feature exists for: one document, two scopes.

    Only the facet that *differs* is stored on the line; `spend_facts` inherits
    the rest from the document.
    """
    doc = await document(
        amount_total=Decimal("400.00"),
        labels={"category": "accountancy", "scope": "business"},
    )
    lines = await replace_lines(
        session,
        doc.id,
        [
            LineInput(amount=Decimal("240.00")),
            LineInput(amount=Decimal("160.00"), labels={"scope": "personal"}),
        ],
    )
    await session.commit()

    stored = (
        (
            await session.execute(
                select(LineLabel.line_id).where(LineLabel.line_id.in_([line.id for line in lines]))
            )
        )
        .scalars()
        .all()
    )
    assert stored == [lines[1].id], "only the overriding line carries a label"


@pytest.mark.asyncio
async def test_an_unallocatable_document_is_named_not_crashed(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """A document with no amount has nothing to divide."""
    doc = await document(amount_total=None)
    with pytest.raises(AllocationError):
        await replace_lines(session, doc.id, [LineInput(amount=Decimal("10.00"))])


# --- The database half of §8.4 ----------------------------------------------
#
# Everything above this line is caught by `replace_lines`'s own checks before a
# row reaches Postgres, so all of it passes against a build with no trigger on
# `spend_lines` at all. These four write past the write path — the way a future
# API handler, a bulk script or someone in `psql` would — and are the only
# committed guard on each trigger binding.


@pytest.mark.asyncio
async def test_the_database_refuses_an_unbalanced_set_written_past_the_write_path(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Guards `AFTER INSERT` on `spend_lines`.

    Delete that binding and every other test in this file still passes: the
    Python pre-check catches them first. This one does not go through it.
    """
    doc = await document(amount_total=Decimal("100.00"))
    await session.execute(
        text(
            "INSERT INTO spend_lines (document_id, amount, origin)"
            " VALUES (:d, CAST('60.00' AS numeric), 'manual')"
        ),
        {"d": doc.id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="spend lines for document"):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_the_database_refuses_a_line_amount_edited_off_balance(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Guards `OR UPDATE` on `spend_lines`.

    Narrowing the trigger to `AFTER INSERT OR DELETE` leaves the rest of this
    file green; editing one row of a balanced set is the hole that opens.
    """
    doc = await document(amount_total=Decimal("100.00"))
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    await session.execute(
        text(
            "UPDATE spend_lines SET amount = CAST('61.00' AS numeric)"
            " WHERE document_id = :d AND amount = CAST('60.00' AS numeric)"
        ),
        {"d": document_id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="spend lines for document"):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_an_allocated_documents_total_cannot_be_nulled(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Clearing `amount_total` is an edit like any other.

    A document whose total is NULL and whose lines sum to 100 is the same
    inconsistency as one whose total is 120, and `spend_facts` would read it the
    same way.
    """
    doc = await document(amount_total=Decimal("100.00"))
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    await session.execute(
        text("UPDATE documents SET amount_total = NULL WHERE id = :d"), {"d": document_id}
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="spend lines for document"):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_a_zero_sum_allocation_still_pins_the_document_total(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """ "No lines" and "lines summing to zero" are different things.

    A guard written as `line_total <> 0` cannot tell them apart, so a document
    allocated across `+50 / -50` could then have its total corrected to anything
    at all — and would contribute 0 to every chart, because `spend_facts` emits
    its line rows rather than the synthetic one. The trigger tests for the
    absence of rows instead.
    """
    doc = await document(amount_total=Decimal("0.00"))
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("50.00")), LineInput(amount=Decimal("-50.00"))],
    )
    await session.commit()
    assert await _line_count(session, document_id) == 2, "a zero-sum split is legal"

    await session.execute(
        text("UPDATE documents SET amount_total = CAST('100.00' AS numeric) WHERE id = :d"),
        {"d": document_id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="spend lines for document"):
        await session.commit()
    await session.rollback()


# --- Refused by name, before anything is written ----------------------------


@pytest.mark.asyncio
async def test_a_sub_cent_line_amount_is_refused_by_name(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """`33.333 * 2 + 33.334` is exactly 100 in Python and 99.99 in the column.

    Without the scale check the set is accepted, each row rounds to `33.33`, and
    the trigger raises at COMMIT as a bare `DBAPIError` — a 500 where the caller
    should have got a named 400. Rejected rather than quantized: silently
    changing the owner's numbers is the failure mode this feature exists to end.
    """
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError, match="decimal places"):
        await replace_lines(
            session,
            doc.id,
            [
                LineInput(amount=Decimal("33.333")),
                LineInput(amount=Decimal("33.333")),
                LineInput(amount=Decimal("33.334")),
            ],
        )
    assert await _line_count(session, doc.id) == 0, "nothing may have been written"


@pytest.mark.asyncio
async def test_a_failed_replace_leaves_the_previous_allocation_intact(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """A rejected replace must not be a half-applied one.

    The bad label is on the **first** line, which is what makes this
    discriminating: an implementation that clears and then resolves as it
    inserts has deleted the old allocation and written one 60.00 row against a
    100.00 total when it raises. (Putting it on the last line proves nothing —
    every row is already written by then, and the set happens to balance.) A
    caller that turns `AllocationError` into a 400 and later commits that
    session would ship that partial set, caught only by the trigger, as a 500.
    """
    doc = await document(amount_total=Decimal("100.00"))
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    with pytest.raises(AllocationError):
        await replace_lines(
            session,
            document_id,
            [
                LineInput(amount=Decimal("60.00"), labels={"scope": "services"}),
                LineInput(amount=Decimal("40.00")),
            ],
        )
    assert await _line_count(session, document_id) == 2
    # The commit is the point: a partial allocation would raise here.
    await session.commit()
    total = await session.scalar(
        text("SELECT sum(amount) FROM spend_lines WHERE document_id = :d"),
        {"d": document_id},
    )
    assert total == Decimal("100.00"), "the original allocation must be untouched"


@pytest.mark.asyncio
async def test_allocating_a_document_that_does_not_exist_says_so(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Distinct from "the document has no amount" — a caller maps them to
    different statuses."""
    with pytest.raises(AllocationError, match="no document with id"):
        await replace_lines(session, 2**40, [LineInput(amount=Decimal("10.00"))])
