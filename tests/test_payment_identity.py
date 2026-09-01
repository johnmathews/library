"""Payment identity: which documents describe one payment.

Every case here mirrors a real ambiguous shape in the archive, with invented
senders and amounts. The two that matter most are the pair four days apart that
must stay SEPARATE (two real purchases) and the pair months apart that must
MERGE (an invoice and the receipt that settled it).
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import AmountKind, Document, DocumentSource, DocumentStatus, Sender
from library.money.payments import add_override, collapse_counts, payment_group, payment_id_for

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _pair(
    database_url: str,
    rows: list[tuple[str | None, str, AmountKind | None, str | None]],
    currency: str | None = "EUR",
) -> list[int]:
    """Seed documents for ONE fresh sender. Rows are (date|None, amount, kind, ref)."""

    async def _work(session: AsyncSession) -> list[int]:
        sender = Sender(name=f"Vendor-{uuid.uuid4().hex[:8]}")
        session.add(sender)
        await session.flush()
        ids: list[int] = []
        for when, amount, kind, reference in rows:
            marker = f"pay:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                sender_id=sender.id,
                document_date=date.fromisoformat(when) if when else None,
                amount_total=Decimal(amount),
                currency=currency,
                amount_kind=kind,
                reference=reference,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def _group(database_url: str, document_id: int) -> list[int]:
    return asyncio.run(_run(database_url, lambda s: payment_group(s, document_id)))


def test_r1_same_day_same_amount_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
            ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r2_a_reference_match_merges_across_any_gap(api_database_url: str) -> None:
    """The case a date window cannot reach: a receipt issued months later."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-01-05", "900.00", AmountKind.PAYMENT_DUE, "K-100"),
            ("2026-03-20", "900.00", AmountKind.PAYMENT_MADE, "K-100"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r3_complementary_kinds_within_sixty_days_merge(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-18", "13.25", AmountKind.PAYMENT_DUE, None),
            ("2026-08-24", "13.25", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_a_monthly_subscription_does_not_chain_across_billing_cycles(
    api_database_url: str,
) -> None:
    """Three cycles of one recurring charge stay three payments, not one.

    A subscription bills the same amount from the same sender every month, and
    each cycle arrives as an invoice and, days later, a receipt. Every one of
    those documents is complementary to every document of the *neighbouring*
    cycle as well as to its own partner, and the gaps between cycles are far
    inside R3's 60-day bound: a receipt on the 3rd is 29 days from the next
    month's invoice on the 1st. An R3 that fires on every complementary pair
    within the window therefore chains cycle to cycle, and the recursive
    closure in the ``payments`` view collapses the whole subscription history
    into a single payment of six documents.

    R3 pairs only MUTUAL NEAREST complementary partners, which is what keeps
    each cycle to itself: the 3rd's nearest invoice is its own cycle's 1st
    (2 days), never the next cycle's (29 days). The two neighbours are not
    equally close here, which is why this fixture alone does not settle the
    question — see the tied-gap and short-February cases below, which it
    could not distinguish and an unsigned nearest-gap ranking got wrong.
    """
    jan_due, jan_made, feb_due, feb_made, mar_due, mar_made = _pair(
        api_database_url,
        [
            ("2026-01-01", "9.99", AmountKind.PAYMENT_DUE, None),
            ("2026-01-03", "9.99", AmountKind.PAYMENT_MADE, None),
            ("2026-02-01", "9.99", AmountKind.PAYMENT_DUE, None),
            ("2026-02-03", "9.99", AmountKind.PAYMENT_MADE, None),
            ("2026-03-01", "9.99", AmountKind.PAYMENT_DUE, None),
            ("2026-03-03", "9.99", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, jan_due) == sorted([jan_due, jan_made])
    assert _group(api_database_url, feb_due) == sorted([feb_due, feb_made])
    assert _group(api_database_url, mar_due) == sorted([mar_due, mar_made])


def test_a_tied_gap_cadence_does_not_chain_across_cycles(api_database_url: str) -> None:
    """A charge invoiced on the 1st and paid on the 16th, twelve months running.

    This is the shape a nearest-partner rule ranked by *unsigned* gap cannot
    separate. In a 30-day month the receipt sits 15 days after its own invoice
    and exactly 15 days before the next month's, so both candidates tie for
    "nearest" and a mutual-nearest test admits the cross-cycle edge as readily
    as the real one. The recursive closure then welds neighbouring cycles
    together: twelve cycles came back as nine payments, four of them groups of
    four documents.

    R3 ranks by *direction* instead — a payment follows the thing it pays, so
    every forward candidate outranks every backward one — and the receipt on
    the 16th can only ever choose the invoice behind it.
    """
    ids = _pair(
        api_database_url,
        [
            (f"2026-{month:02d}-{day:02d}", "9.99", kind, None)
            for month in range(1, 13)
            for day, kind in ((1, AmountKind.PAYMENT_DUE), (16, AmountKind.PAYMENT_MADE))
        ],
    )
    for cycle in range(12):
        due, made = ids[cycle * 2], ids[cycle * 2 + 1]
        assert _group(api_database_url, due) == sorted([due, made])


def test_a_short_february_pairs_each_invoice_with_its_own_receipt(
    api_database_url: str,
) -> None:
    """February is 28 days long, and that alone used to move a pairing.

    On the same 1st/16th cadence, February's receipt is 15 days after its own
    invoice but only 13 days before *March's* invoice — so a rule that picks
    the smallest unsigned gap pairs February's receipt with March's invoice
    and leaves February's invoice unpaid. Forward-preference removes the
    question: a receipt never looks at an invoice dated after it while one
    dated before it is in reach.
    """
    ids = _pair(
        api_database_url,
        [
            (f"2026-{month:02d}-{day:02d}", "41.50", kind, None)
            for month in range(1, 5)
            for day, kind in ((1, AmountKind.PAYMENT_DUE), (16, AmountKind.PAYMENT_MADE))
        ],
    )
    for cycle in range(4):
        due, made = ids[cycle * 2], ids[cycle * 2 + 1]
        assert _group(api_database_url, due) == sorted([due, made])


def test_a_vetoed_neighbour_does_not_steal_the_nearest_slot(api_database_url: str) -> None:
    """A conflicting reference must remove a candidate, not just its edge.

    The middle document here is one day from the invoice and carries a
    reference that contradicts it, so the VETO means the two can never merge.
    But the nearest-partner ranking is computed separately from the rule
    `CASE`, so unless it applies the veto too, the vetoed receipt still wins
    the invoice's "nearest" slot and suppresses the merge with the receipt
    four days out, which nothing forbids. The invoice ends up unpaired for a
    reason that has nothing to do with it.
    """
    invoice, conflicting, genuine = _pair(
        api_database_url,
        [
            ("2026-01-01", "62.00", AmountKind.PAYMENT_DUE, "INV-1"),
            ("2026-01-02", "62.00", AmountKind.PAYMENT_MADE, "INV-9"),
            ("2026-01-05", "62.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, invoice) == sorted([invoice, genuine])
    assert _group(api_database_url, conflicting) == [conflicting]


def test_a_prepayment_with_no_reference_still_merges(api_database_url: str) -> None:
    """Money paid before the invoice arrives is still one payment.

    Forward-preference is a preference, not a filter: when a receipt has no
    invoice behind it, the one in front of it is still its nearest partner and
    still merges. Only a receipt with a genuine choice prefers the invoice it
    follows.
    """
    made, due = _pair(
        api_database_url,
        [
            ("2026-01-01", "128.00", AmountKind.PAYMENT_MADE, None),
            ("2026-01-05", "128.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, made) == sorted([made, due])


def test_a_backward_match_is_only_used_when_no_forward_one_exists(
    api_database_url: str,
) -> None:
    """The invoice has a receipt on each side and takes the later one.

    The earlier receipt is nine days back, the later one ten days on, so an
    unsigned ranking would take the prepayment. Direction is worth more than
    a day: the invoice pairs forward, and the stray receipt — which now has no
    invoice behind it and none in front either, its only candidate having been
    claimed — stands alone.
    """
    early, due, late = _pair(
        api_database_url,
        [
            ("2026-01-01", "17.00", AmountKind.PAYMENT_MADE, None),
            ("2026-01-10", "17.00", AmountKind.PAYMENT_DUE, None),
            ("2026-01-20", "17.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, due) == sorted([due, late])
    assert _group(api_database_url, early) == [early]


def test_one_invoice_is_not_claimed_by_two_receipts(api_database_url: str) -> None:
    """Mutual-nearest keeps a single invoice to a single receipt.

    Both receipts are complementary to the invoice and both are inside the
    window, but only the one the invoice also chooses gets the edge. Without
    mutuality the closure would join all three into one payment of two
    unrelated receipts.
    """
    due, before, after = _pair(
        api_database_url,
        [
            ("2026-01-03", "24.00", AmountKind.PAYMENT_DUE, None),
            ("2026-01-01", "24.00", AmountKind.PAYMENT_MADE, None),
            ("2026-01-05", "24.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, due) == sorted([due, after])
    assert _group(api_database_url, before) == [before]


def test_an_unpaid_invoice_does_not_steal_a_later_cycles_receipt(
    api_database_url: str,
) -> None:
    """An invoice that was never paid must not absorb the next one's receipt.

    January's invoice is the only candidate 33 days behind February's receipt,
    and February's own invoice is two days behind it. Forward-preference alone
    does not decide this — both are forward — so the ranking has to be by
    distance *among* forward candidates. The unpaid invoice stays unpaid,
    which is the honest answer, and February's late receipt keeps to itself.
    """
    january, february, paid, stray = _pair(
        api_database_url,
        [
            ("2026-01-01", "88.00", AmountKind.PAYMENT_DUE, None),
            ("2026-02-01", "88.00", AmountKind.PAYMENT_DUE, None),
            ("2026-02-03", "88.00", AmountKind.PAYMENT_MADE, None),
            ("2026-02-28", "88.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, february) == sorted([february, paid])
    assert _group(api_database_url, january) == [january]
    assert _group(api_database_url, stray) == [stray]


def test_a_receipt_equidistant_between_two_invoices_pairs_with_the_earlier(
    api_database_url: str,
) -> None:
    """Ten days from each invoice, and the tie is broken by direction.

    The receipt settles the invoice it follows, not the one that has not been
    issued yet, and the later invoice is left unpaid rather than being handed
    a receipt that predates it.
    """
    first, made, second = _pair(
        api_database_url,
        [
            ("2026-01-01", "310.00", AmountKind.PAYMENT_DUE, None),
            ("2026-01-11", "310.00", AmountKind.PAYMENT_MADE, None),
            ("2026-01-21", "310.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, first) == sorted([first, made])
    assert _group(api_database_url, second) == [second]


def test_r3_reaches_sixty_days_and_no_further(api_database_url: str) -> None:
    """R3's bound, at both sides of it. This is the view's only date window,
    and it lives in the `sym` CTE (`abs(m.document_date - d.document_date)
    <= 60`), which is what §4.2 of docs/money-facts.md points at."""
    due_60, made_60 = _pair(
        api_database_url,
        [
            ("2026-01-01", "410.00", AmountKind.PAYMENT_DUE, None),
            ("2026-03-02", "410.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, due_60) == sorted([due_60, made_60])

    due_61, made_61 = _pair(
        api_database_url,
        [
            ("2026-01-01", "411.00", AmountKind.PAYMENT_DUE, None),
            ("2026-03-03", "411.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, due_61) == [due_61]
    assert _group(api_database_url, made_61) == [made_61]


def test_a_systematically_reversed_cadence_pairs_off_by_one_cycle(
    api_database_url: str,
) -> None:
    """The one shape forward-preference gets wrong, pinned rather than hidden.

    A charge taken on the 1st and *invoiced* on the 5th reverses the archive's
    normal order. Every receipt then has an invoice 27 days behind it (the
    previous cycle's) and its own 4 days ahead, and forward-preference picks
    the one behind — so the cycles pair off by one, the first receipt and the
    last invoice are left unpaired, and three cycles come back as four
    payments.

    The consequence is an overcount of one payment across a run of this
    cadence, not a collapse: no group is wrong about how much money it holds,
    and each still holds exactly one invoice and one receipt. Ranking by
    magnitude with direction only as a tie-break fixes this shape and
    re-breaks the short-February one above, which is the worse trade because
    invoice-then-payment is the archive's normal order. See
    docs/money-facts.md §5.
    """
    jan_made, jan_due, feb_made, feb_due, mar_made, mar_due = _pair(
        api_database_url,
        [
            ("2026-01-01", "7.25", AmountKind.PAYMENT_MADE, None),
            ("2026-01-05", "7.25", AmountKind.PAYMENT_DUE, None),
            ("2026-02-01", "7.25", AmountKind.PAYMENT_MADE, None),
            ("2026-02-05", "7.25", AmountKind.PAYMENT_DUE, None),
            ("2026-03-01", "7.25", AmountKind.PAYMENT_MADE, None),
            ("2026-03-05", "7.25", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, jan_made) == [jan_made]
    assert _group(api_database_url, jan_due) == sorted([jan_due, feb_made])
    assert _group(api_database_url, feb_due) == sorted([feb_due, mar_made])
    assert _group(api_database_url, mar_due) == [mar_due]


def test_two_real_purchases_four_days_apart_stay_separate(api_database_url: str) -> None:
    """Both are payment_made, so R3 cannot fire. This is why complementarity,
    not a date window, is what makes date-tolerant merging safe."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == [a]
    assert _group(api_database_url, b) == [b]


def test_four_same_amount_invoices_merge_only_the_same_day_pair(
    api_database_url: str,
) -> None:
    a, b, c, d = _pair(
        api_database_url,
        [
            ("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2026-11-22", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2027-01-05", "689.40", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, c) == [c]
    assert _group(api_database_url, d) == [d]


def test_differing_references_veto_a_same_day_merge(api_database_url: str) -> None:
    a, _b = _pair(
        api_database_url,
        [
            ("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-1"),
            ("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-2"),
        ],
    )
    assert _group(api_database_url, a) == [a]


def test_a_refund_does_not_merge_with_the_same_day_payment_it_reverses(
    api_database_url: str,
) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-04-01", "49.00", AmountKind.PAYMENT_MADE, None),
            ("2026-04-01", "49.00", AmountKind.REFUND, None),
        ],
    )
    assert _group(api_database_url, a) == [a], "R1 must not merge across opposite signs"
    assert _group(api_database_url, b) == [b], "R1 must not merge across opposite signs"


def test_a_credit_note_quoting_its_invoice_reference_does_not_merge(
    api_database_url: str,
) -> None:
    """The case the precondition exists for.

    R2 is the strongest rule and matches at any date gap. Without the guard
    these two merge, which was confirmed by executing the pre-0034 view.
    """
    a, b = _pair(
        api_database_url,
        [
            ("2026-04-01", "120.00", AmountKind.PAYMENT_DUE, "X-1"),
            ("2026-06-30", "120.00", AmountKind.REFUND, "X-1"),
        ],
    )
    assert _group(api_database_url, a) == [a]
    assert _group(api_database_url, b) == [b]


def test_an_undecided_kind_never_merges_with_a_refund(api_database_url: str) -> None:
    """NULL counts as not-a-refund: the cautious direction, and a NULL
    contributes to no total anyway."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-04-01", "30.00", None, None),
            ("2026-04-01", "30.00", AmountKind.REFUND, None),
        ],
    )
    assert _group(api_database_url, a) == [a]
    assert _group(api_database_url, b) == [b]


def test_the_guard_does_not_break_the_rules_it_sits_above(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-04-01", "77.00", AmountKind.PAYMENT_DUE, None),
            ("2026-04-01", "77.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b]), (
        "R1 must still fire between two positive kinds"
    )

    c, d = _pair(
        api_database_url,
        [
            ("2026-04-01", "15.00", AmountKind.REFUND, None),
            ("2026-04-01", "15.00", AmountKind.REFUND, None),
        ],
    )
    assert _group(api_database_url, c) == sorted([c, d]), (
        "two refunds on one day are still one payment"
    )


def test_two_unbackfilled_documents_still_merge_on_r1(api_database_url: str) -> None:
    """Regression guard for the sign guard's spelling.

    ``(a.amount_kind = 'refund') = (b.amount_kind = 'refund')`` looks like an
    equivalent rewrite of the guard but is NOT: a NULL amount_kind makes each
    side NULL, so the equality is itself NULL and the whole WHERE clause
    drops the pair from ``pairs`` -- silently killing R1 for every
    un-backfilled document, which is most of the live archive. ``IS DISTINCT
    FROM`` treats NULL as a definite value and does not have this failure
    mode. This must still merge like any other same-day, same-amount,
    same-sender pair.
    """
    a, b = _pair(
        api_database_url,
        [
            ("2026-04-01", "62.00", None, None),
            ("2026-04-01", "62.00", None, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, b) == sorted([a, b])


def test_two_unbackfilled_documents_still_merge_on_r2(api_database_url: str) -> None:
    """The R2 half of the same regression: a shared reference, any date gap,
    must still merge two NULL-``amount_kind`` documents."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-01-10", "88.00", None, "Q-7"),
            ("2026-07-02", "88.00", None, "Q-7"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, b) == sorted([a, b])


def test_dateless_documents_still_pair_on_reference(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            (None, "75.00", AmountKind.PAYMENT_DUE, "Z-9"),
            (None, "75.00", AmountKind.PAYMENT_MADE, "Z-9"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_currency_less_documents_can_pair(api_database_url: str) -> None:
    """`currency = currency` is NULL for two NULL currencies; IS NOT DISTINCT FROM is not."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-05-01", "60.00", AmountKind.PAYMENT_DUE, None),
            ("2026-05-01", "60.00", AmountKind.PAYMENT_MADE, None),
        ],
        currency=None,
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_unbackfilled_amount_kinds_do_not_merge_on_r3(api_database_url: str) -> None:
    """NULL amount_kind must not satisfy complementarity, or an un-backfilled
    archive would silently collapse unrelated same-amount documents."""
    a, _b = _pair(
        api_database_url,
        [("2026-04-01", "99.00", None, None), ("2026-04-20", "99.00", None, None)],
    )
    assert _group(api_database_url, a) == [a]


def test_a_chain_of_three_collapses_to_one_payment(api_database_url: str) -> None:
    a, b, c = _pair(
        api_database_url,
        [
            ("2026-09-01", "30.00", AmountKind.PAYMENT_DUE, "T-1"),
            ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1"),
            ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b, c])


def test_a_split_override_unmerges_an_automatic_pair(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
            ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "SPLIT", a, b)))
    assert _group(api_database_url, a) == [a]


def test_a_merge_override_joins_a_pair_no_rule_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_the_latest_correction_on_a_pair_wins_in_both_directions(
    api_database_url: str,
) -> None:
    """A pair carries both a MERGE and a SPLIT row (the unique constraint is on
    the ``(kind, doc_a, doc_b)`` triple), so which one applies is decided by
    ``created_at``. Every correction after the first has to land, including the
    third: a guard that let a SPLIT win unconditionally would make a MERGE
    unable to undo one, and an override insert that left ``created_at`` alone
    when the row already existed would make the third correction a no-op.
    """
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
            ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    # Each correction is its own transaction, as it is over HTTP: now() is the
    # transaction timestamp, so recording them in one would tie every row.
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])

    asyncio.run(_run(api_database_url, lambda s: add_override(s, "SPLIT", a, b)))
    assert _group(api_database_url, a) == [a]

    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_an_override_pair_is_ordered_regardless_of_argument_order(
    api_database_url: str,
) -> None:
    """doc_a < doc_b is a check constraint; add_override must order the pair."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", b, a)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_a_deleted_partner_leaves_the_survivor_alone(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-06-01", "55.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-01", "55.00", AmountKind.PAYMENT_MADE, None),
        ],
    )

    async def _delete(session: AsyncSession) -> None:
        from datetime import UTC, datetime

        document = await session.get(Document, b)
        assert document is not None
        document.deleted_at = datetime.now(UTC)

    asyncio.run(_run(api_database_url, _delete))
    assert _group(api_database_url, a) == [a]


def test_a_deleted_override_partner_does_not_corrupt_the_survivors_payment_id(
    api_database_url: str,
) -> None:
    """The override-specific version of the case above.

    Unlike a rule edge (R1/R2/R3), which is only ever derived by joining two
    LIVE documents, the ``payment_edges`` override union previously had no
    ``deleted_at`` filter. A trashed document stayed reachable as a `member`
    in the ``payments`` view's recursive closure and could still win
    ``min(member)`` — so the LIVE survivor's ``payment_id`` became an id that
    no longer exists anywhere else in the API (`payment_id_for` on that id
    itself would 404). These two documents are seeded so that NO automatic
    rule connects them (different amounts, same kind — R1/R2/R3 all miss);
    the only edge between them is the MERGE override, isolating exactly the
    behaviour the view fix targets.
    """
    a, b = _pair(
        api_database_url,
        [
            ("2026-06-01", "55.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-01", "91.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])

    victim, survivor = sorted([a, b])

    async def _delete(session: AsyncSession) -> None:
        from datetime import UTC, datetime

        document = await session.get(Document, victim)
        assert document is not None
        document.deleted_at = datetime.now(UTC)

    asyncio.run(_run(api_database_url, _delete))

    # The survivor must be alone in its OWN payment, not carrying the
    # deleted document's id — `payment_id_for(survivor)` must equal
    # `survivor`, and its group must contain nothing else.
    survivor_payment_id = asyncio.run(_run(api_database_url, lambda s: payment_id_for(s, survivor)))
    assert survivor_payment_id == survivor
    assert _group(api_database_url, survivor) == [survivor]


def test_collapse_counts_reports_payments_and_documents(api_database_url: str) -> None:
    """A merged pair plus a standalone document: 2 payments from 3 documents."""
    a, b, standalone = _pair(
        api_database_url,
        [
            ("2026-06-15", "22.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-15", "22.00", AmountKind.PAYMENT_MADE, None),
            ("2026-06-16", "77.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, standalone) == [standalone]

    async def _work(session: AsyncSession) -> tuple[int, int]:
        return await collapse_counts(session, [a, b, standalone])

    payments, documents = asyncio.run(_run(api_database_url, _work))
    assert (payments, documents) == (2, 3)


def test_collapse_counts_of_no_documents_is_zero_without_a_query(
    api_database_url: str,
) -> None:
    async def _work(session: AsyncSession) -> tuple[int, int]:
        return await collapse_counts(session, [])

    assert asyncio.run(_run(api_database_url, _work)) == (0, 0)
