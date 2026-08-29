"""The one relation every chart reads.

Each case is built so a plausible-but-wrong view goes red. Three in
particular exist because the obvious SQL is wrong, not because the correct
SQL is subtle: NULLs sort first under DESC, a deleted twin can hold the
canonical slot, and a merged pair's two documents can carry different
labels.

Every case that turns on a *merge* names the same `sender=` on both documents.
The `document` fixture defaults `sender` to None so fixture documents never
merge by accident, and all three payment rules require a non-NULL matching
`sender_id` — so a merge case that forgets the sender asserts nothing at all.
`test_the_merge_fixture_really_collapses_a_pair` is the guard against that
defaulting silently changing under us.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS, AmountKind, SpendLine
from library.spend_lines import LineInput, replace_lines
from tests.conftest import DocumentFactory

pytestmark = pytest.mark.integration

#: Invented sender names. Two documents merge only when their `sender_id`
#: matches, so a test that wants a merge passes the same name to both.
VENDOR = "Aurora Test Supplies"
OTHER_VENDOR = "Borealis Test Services"

#: Passthrough values for the two `UNION ALL` arms. Every one is away from the
#: fixture default (`FIXTURE_DOCUMENT_DATE`, `EUR`, `reference=None`) and the
#: two arms differ from each other, so `date`, `currency` and `reference` are
#: pinned per arm rather than able to pass by coincidence. All invented.
ARM1_DATE = date(2025, 11, 7)
ARM1_CURRENCY = "GBP"
ARM1_REFERENCE = "AT-9271-Q"
ARM2_DATE = date(2025, 9, 23)
ARM2_CURRENCY = "CHF"
ARM2_REFERENCE = "LN-4408-B"

#: Rendered from the model constants rather than written out, so a new
#: contributing kind cannot make these helpers disagree with the application.
_SUMMABLE = ", ".join(f"'{kind.value}'" for kind in sorted(SUMMABLE_AMOUNT_KINDS))
_SIGNED_AMOUNT = "f.amount * (CASE f.amount_kind {} ELSE 0 END)".format(
    " ".join(f"WHEN '{kind.value}' THEN {sign}" for kind, sign in AMOUNT_SIGN.items())
)

Allocation = Decimal | tuple[Decimal, Mapping[str, str]]


async def _facts(session: AsyncSession) -> list[dict[str, Any]]:
    """Every `spend_facts` row, in a stable order."""
    result = await session.execute(
        text("SELECT * FROM spend_facts ORDER BY document_id, line_id NULLS FIRST")
    )
    return [dict(row) for row in result.mappings().all()]


async def _payment_group_count(session: AsyncSession) -> int:
    """How many distinct payments `spend_facts` currently describes."""
    result = await session.execute(text("SELECT count(DISTINCT payment_id) FROM spend_facts"))
    return int(result.scalar_one())


def _bucket(axis: str | None) -> tuple[str, dict[str, str]]:
    if axis is None:
        return "'all'", {}
    if axis == "sender":
        return "CAST(f.sender_id AS text)", {}
    return "f.labels ->> :axis", {"axis": axis}


async def _totals(session: AsyncSession, axis: str | None, amount: str) -> dict[str, Decimal]:
    bucket, params = _bucket(axis)
    result = await session.execute(
        text(
            f"SELECT {bucket} AS bucket, sum({amount}) AS total "
            "FROM spend_facts f "
            f"WHERE f.is_canonical AND f.amount_kind IN ({_SUMMABLE}) "
            "GROUP BY 1"
        ),
        params,
    )
    return {row.bucket: row.total for row in result.all()}


async def _totals_by(session: AsyncSession, axis: str | None) -> dict[str, Decimal]:
    """Magnitudes: what the archive shows, before sign is applied."""
    return await _totals(session, axis, "f.amount")


async def _signed_totals_by(session: AsyncSession, axis: str | None) -> dict[str, Decimal]:
    """The same, with `AMOUNT_SIGN` applied — a refund subtracts."""
    return await _totals(session, axis, _SIGNED_AMOUNT)


async def _allocate(
    session: AsyncSession, document_id: int, allocations: Sequence[Allocation]
) -> list[SpendLine]:
    """Split a document across lines and COMMIT.

    `replace_lines` only flushes; the sum trigger is DEFERRABLE INITIALLY
    DEFERRED and fires at the caller's COMMIT, so a helper that did not commit
    would leave the invariant unchecked and the view unable to see the lines.
    """
    lines = [
        LineInput(amount=entry[0], labels=dict(entry[1]))
        if isinstance(entry, tuple)
        else LineInput(amount=entry)
        for entry in allocations
    ]
    created = await replace_lines(session, document_id, lines)
    await session.commit()
    return created


async def _override(session: AsyncSession, kind: str, doc_a: int, doc_b: int) -> None:
    """Record a human MERGE/SPLIT correction.

    The later correction wins and an exact tie falls to the SPLIT (0034), so the
    re-MERGE is stamped a second into the future rather than at `now()`.
    """
    seconds = 1 if kind == "MERGE" else 0
    await session.execute(
        text(
            "INSERT INTO payment_overrides (kind, doc_a, doc_b, created_at) "
            "VALUES (:kind, :doc_a, :doc_b, "
            "now() + CAST(:seconds AS double precision) * interval '1 second')"
        ),
        {
            "kind": kind,
            "doc_a": min(doc_a, doc_b),
            "doc_b": max(doc_a, doc_b),
            "seconds": seconds,
        },
    )
    await session.commit()


@pytest.mark.asyncio
async def test_the_merge_fixture_really_collapses_a_pair(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The guard every canonicality assertion in this file rests on.

    `document(...)` defaults `sender` to None so fixture documents never merge
    by accident. Every rule needs a non-NULL *matching* sender, so a merge case
    that omits `sender=` produces two payments and asserts nothing. Prove both
    directions here, once, rather than trusting each case to notice.
    """
    merging_a = await document(
        amount_total=Decimal("64.00"), amount_kind=AmountKind.PAYMENT_DUE, sender=VENDOR
    )
    merging_b = await document(
        amount_total=Decimal("64.00"), amount_kind=AmountKind.PAYMENT_MADE, sender=VENDOR
    )
    rows = await _facts(session)
    assert {row["document_id"] for row in rows} == {merging_a.id, merging_b.id}
    assert await _payment_group_count(session) == 1

    # Identical in every respect except the sender default, and therefore two
    # separate payments: the trap this test exists to catch.
    await document(amount_total=Decimal("71.00"), amount_kind=AmountKind.PAYMENT_DUE)
    await document(amount_total=Decimal("71.00"), amount_kind=AmountKind.PAYMENT_MADE)
    assert await _payment_group_count(session) == 3


@pytest.mark.asyncio
async def test_an_unsplit_document_becomes_one_row_carrying_its_labels(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    doc = await document(
        amount_total=Decimal("100.00"),
        labels={"category": "services"},
        document_date=ARM1_DATE,
        currency=ARM1_CURRENCY,
        reference=ARM1_REFERENCE,
    )
    rows = await _facts(session)
    assert len(rows) == 1
    assert rows[0]["document_id"] == doc.id
    assert rows[0]["line_id"] is None
    assert rows[0]["amount"] == Decimal("100.00")
    assert rows[0]["labels"] == {"category": "services"}
    assert rows[0]["is_canonical"] is True
    # The passthrough columns every chart in Tasks 5-10 groups and filters by.
    # Untested they would ship a transcription slip green: `d.created_at AS
    # date` buckets by ingest time, and a currency or reference read from the
    # wrong column filters the wrong money. Each value is deliberately away
    # from the fixture default so no assertion can pass by luck.
    assert rows[0]["date"] == ARM1_DATE
    assert rows[0]["currency"] == ARM1_CURRENCY
    assert rows[0]["reference"] == ARM1_REFERENCE
    assert rows[0]["sender_id"] is None
    assert rows[0]["payment_id"] == doc.id
    assert rows[0]["amount_kind"] is None


@pytest.mark.asyncio
async def test_a_deleted_twin_neither_appears_nor_holds_the_canonical_slot(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Adversarial: the deleted document has the LOWER id and is
    `payment_made`, so it wins every tie-break. A view that filters deletes
    only in its outer SELECT still ranks it, and the live document comes
    back is_canonical=false — contributing to no total, silently.

    The pair shares a sender, date, amount and currency, so R1 would merge the
    two were the first not deleted. Deleted documents get no `payments` row at
    all (the view seeds its reachability from `documents WHERE deleted_at IS
    NULL`), so the join to `payments` excludes them — as does the view's own
    `deleted_at IS NULL` filter. Neither is removable alone; see the migration.
    """
    dead = await document(
        amount_total=Decimal("50.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        deleted=True,
        sender=VENDOR,
    )
    live = await document(
        amount_total=Decimal("50.00"), amount_kind=AmountKind.PAYMENT_DUE, sender=VENDOR
    )
    rows = await _facts(session)
    assert dead.id < live.id
    assert [row["document_id"] for row in rows] == [live.id]
    assert rows[0]["is_canonical"] is True


@pytest.mark.asyncio
async def test_an_undecided_kind_does_not_outrank_payment_made(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """`(amount_kind = 'payment_made') DESC` is NULL for an undecided
    document, and Postgres sorts NULLs FIRST under DESC. Without the
    COALESCE the undecided document becomes canonical and the payment is
    represented by a kind that is never summed. Confirmed red by mutation.
    """
    undecided = await document(amount_total=Decimal("40.00"), amount_kind=None, sender=VENDOR)
    made = await document(
        amount_total=Decimal("40.00"), amount_kind=AmountKind.PAYMENT_MADE, sender=VENDOR
    )
    rows = await _facts(session)
    assert undecided.id < made.id
    assert await _payment_group_count(session) == 1
    assert {row["document_id"] for row in rows if row["is_canonical"]} == {made.id}


@pytest.mark.asyncio
async def test_a_line_bearing_document_wins_canonical_despite_a_higher_id(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Otherwise merging an itemised invoice with its receipt discards the
    split, and the allocation the owner made by hand disappears."""
    receipt = await document(
        amount_total=Decimal("100.00"), amount_kind=AmountKind.PAYMENT_MADE, sender=VENDOR
    )
    invoice = await document(
        amount_total=Decimal("100.00"), amount_kind=AmountKind.PAYMENT_DUE, sender=VENDOR
    )
    await _allocate(session, invoice.id, [Decimal("60.00"), Decimal("40.00")])
    rows = await _facts(session)
    assert receipt.id < invoice.id
    assert await _payment_group_count(session) == 1
    assert {row["document_id"] for row in rows if row["is_canonical"]} == {invoice.id}
    assert sum((row["amount"] for row in rows if row["is_canonical"]), Decimal("0")) == Decimal(
        "100.00"
    )


@pytest.mark.asyncio
async def test_a_line_overrides_one_facet_and_inherits_the_rest(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    doc = await document(
        amount_total=Decimal("100.00"),
        labels={"category": "services", "scope": "business"},
        document_date=ARM2_DATE,
        currency=ARM2_CURRENCY,
        reference=ARM2_REFERENCE,
    )
    await _allocate(
        session,
        doc.id,
        [(Decimal("60.00"), {}), (Decimal("40.00"), {"scope": "personal"})],
    )
    rows = await _facts(session)
    assert [row["labels"] for row in rows] == [
        {"category": "services", "scope": "business"},
        {"category": "services", "scope": "personal"},
    ]
    # The two UNION ALL arms are written out separately, so they can disagree
    # on which column lands where and stay green everywhere else. A line row
    # carries its DOCUMENT's date, currency and reference — a line has none of
    # its own — and the values differ from arm 1's, so an arm that read the
    # wrong row would show it.
    assert [row["line_id"] is not None for row in rows] == [True, True]
    assert {row["date"] for row in rows} == {ARM2_DATE}
    assert {row["currency"] for row in rows} == {ARM2_CURRENCY}
    assert {row["reference"] for row in rows} == {ARM2_REFERENCE}
    assert {row["document_id"] for row in rows} == {doc.id}
    assert {row["payment_id"] for row in rows} == {doc.id}


@pytest.mark.asyncio
async def test_a_merged_pair_contributes_once_under_the_canonical_labels(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """Two properties on one fixture: no double count, and the money lands
    under the CANONICAL document's labels.

    Adversarial: the pair's two documents carry DIFFERENT categories, so a
    view that lets the non-canonical row through does not merely double the
    total — it moves money into a category the owner never chose.
    """
    invoice = await document(
        amount_total=Decimal("250.00"),
        amount_kind=AmountKind.PAYMENT_DUE,
        labels={"category": "supplies"},
        sender=VENDOR,
    )
    receipt = await document(
        amount_total=Decimal("250.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "services"},
        sender=VENDOR,
    )
    assert await _payment_group_count(session) == 1
    rows = await _facts(session)
    assert {row["document_id"] for row in rows if row["is_canonical"]} == {receipt.id}
    assert {row["document_id"] for row in rows if not row["is_canonical"]} == {invoice.id}

    totals = await _totals_by(session, "category")
    assert totals == {"services": Decimal("250.00")}
    assert "supplies" not in totals


@pytest.mark.asyncio
async def test_the_total_is_invariant_across_split_axes(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    """Distinct senders and distinct amounts, so nothing merges and the
    `sender` axis really does split into two buckets rather than collapsing to
    one NULL bucket that would make the axis prove nothing."""
    await document(
        amount_total=Decimal("250.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "services", "scope": "business"},
        sender=VENDOR,
    )
    await document(
        amount_total=Decimal("80.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "services", "scope": "personal"},
        sender=OTHER_VENDOR,
    )
    assert await _payment_group_count(session) == 2
    flat = await _totals_by(session, None)
    assert sum(flat.values()) == Decimal("330.00")
    for axis in ("category", "scope", "sender"):
        by_axis = await _totals_by(session, axis)
        assert sum(by_axis.values()) == Decimal("330.00")
    assert len(await _totals_by(session, "scope")) == 2
    assert len(await _totals_by(session, "sender")) == 2


@pytest.mark.asyncio
async def test_merge_then_split_then_merge_returns_to_the_merged_total(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """A -> B -> A. Running a reversible operation one way proves nothing."""
    a = await document(
        amount_total=Decimal("90.00"), amount_kind=AmountKind.PAYMENT_DUE, sender=VENDOR
    )
    b = await document(
        amount_total=Decimal("90.00"), amount_kind=AmountKind.PAYMENT_MADE, sender=VENDOR
    )
    assert await _payment_group_count(session) == 1
    merged = await _totals_by(session, None)
    await _override(session, "SPLIT", a.id, b.id)
    after_split = await _totals_by(session, None)
    assert await _payment_group_count(session) == 2
    await _override(session, "MERGE", a.id, b.id)
    after_remerge = await _totals_by(session, None)
    assert await _payment_group_count(session) == 1
    assert sum(merged.values()) == Decimal("90.00")
    assert sum(after_split.values()) == Decimal("180.00")
    assert after_remerge == merged


@pytest.mark.asyncio
async def test_a_refund_lowers_the_total_of_the_category_it_belongs_to(
    session: AsyncSession, document: DocumentFactory, facets: dict[str, tuple[str, ...]]
) -> None:
    await document(
        amount_total=Decimal("200.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        labels={"category": "services"},
    )
    await document(
        amount_total=Decimal("49.00"),
        amount_kind=AmountKind.REFUND,
        document_date=date(2026, 5, 1),
        labels={"category": "services"},
    )
    assert await _signed_totals_by(session, "category") == {"services": Decimal("151.00")}
