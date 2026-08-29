"""The aggregate. Fixtures are shaped to make wrong answers visible.

February appears deliberately: a month bucket computed by adding 30 days
lands inside the next month, and every other month hides it.

Every case that turns on a *merge* names the same `sender=` on both documents.
The `document` fixture defaults `sender` to None so fixture documents never
merge by accident, and all three payment rules require a non-NULL matching
`sender_id` — so a merge case that forgets the sender asserts nothing at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from library.charts.query import Series, chart_series
from library.charts.rule import Clause, Rule
from library.fx import convert_amount
from library.models import Grain

#: Invented vendor names. Two documents merge into one payment only when their
#: `sender_id` matches, so a test that wants a merge passes the same name twice.
VENDOR = "Lyra Test Consulting"


@pytest.mark.asyncio
async def test_the_total_is_identical_under_every_split(session, seeded) -> None:
    """The property §9.2 exists to guarantee. Asserted by comparison, not by
    a literal, so it cannot pass by a fixture coincidence."""
    flat = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    buckets: dict[str, set[str | None]] = {}
    for axis in ("category", "scope", "sender"):
        split = await chart_series(
            session, Rule(), grain=Grain.MONTH, split=axis, currency="EUR", since=None, until=None
        )
        assert split.total == flat.total, f"split by {axis} changed the total"
        assert sum(cell.total for cell in split.cells) == flat.total
        buckets[axis] = {cell.split_value for cell in split.cells}
    # Invariance alone does not prove the axis *splits*: a split expression
    # rendered inert (every row into one NULL bucket) satisfies every assertion
    # above. `seeded` carries three senders and four categories, so a working
    # axis must produce more than one named bucket.
    assert len(buckets["sender"] - {None}) >= 2, "the sender axis did not partition"
    assert len(buckets["category"] - {None}) >= 2, "the category axis did not partition"


@pytest.mark.asyncio
async def test_an_unlabelled_row_lands_in_a_null_bucket_not_the_bin(
    session, document, facets
) -> None:
    """Dropping it would make the total depend on the split axis, which is
    exactly what §9.2 forbids."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind="payment_made",
        labels={"category": "services"},
    )
    await document(amount_total=Decimal("5.00"), amount_kind="payment_made", labels={})
    series = await chart_series(
        session,
        Rule(),
        grain=Grain.MONTH,
        split="category",
        currency="EUR",
        since=None,
        until=None,
    )
    assert series.total == Decimal("15.00")
    # The exact set, not just `None in ...`: an inert split expression puts
    # every row in the NULL bucket, which satisfies a membership check while
    # having stopped splitting anything at all.
    assert {cell.split_value for cell in series.cells} == {"services", None}


@pytest.mark.asyncio
async def test_a_refund_lowers_its_period_rather_than_being_dropped(session, document) -> None:
    await document(
        amount_total=Decimal("200.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 10),
    )
    await document(
        amount_total=Decimal("49.00"), amount_kind="refund", document_date=date(2026, 4, 20)
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    assert series.total == Decimal("151.00")
    assert [cell.total for cell in series.cells] == [Decimal("151.00")]


@pytest.mark.asyncio
async def test_a_non_contributing_kind_never_enters_the_total(session, document) -> None:
    """The case that motivated the whole redesign: an insurance ceiling is
    large enough to wreck any total it enters (spec §2.2)."""
    await document(amount_total=Decimal("100.00"), amount_kind="payment_made")
    await document(amount_total=Decimal("500000.00"), amount_kind="coverage_limit")
    await document(amount_total=Decimal("450.00"), amount_kind="estimate")
    await document(amount_total=Decimal("77.00"), amount_kind=None)
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    assert series.total == Decimal("100.00")


@pytest.mark.asyncio
async def test_february_buckets_by_calendar_month_not_by_thirty_days(session, document) -> None:
    """A bucket computed as a 30-day offset puts 2026-02-28 in March. Every
    month except February hides that."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind="payment_made",
        document_date=date(2026, 2, 1),
    )
    await document(
        amount_total=Decimal("20.00"),
        amount_kind="payment_made",
        document_date=date(2026, 2, 28),
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    assert [cell.period for cell in series.cells] == [date(2026, 2, 1)]
    assert series.cells[0].total == Decimal("30.00")


@pytest.mark.parametrize(
    ("grain", "periods"),
    [
        # 2026-02-01 is a Sunday and 2026-02-28 a Saturday, so they fall in
        # different ISO weeks (Monday-based) but the same month, quarter and
        # year — one fixture discriminates all four grains.
        (Grain.WEEK, [date(2026, 1, 26), date(2026, 2, 23)]),
        (Grain.MONTH, [date(2026, 2, 1)]),
        (Grain.QUARTER, [date(2026, 1, 1)]),
        (Grain.YEAR, [date(2026, 1, 1)]),
    ],
)
@pytest.mark.asyncio
async def test_every_grain_buckets_by_its_own_calendar_unit(
    session, document, grain, periods
) -> None:
    """All four grains, not just MONTH.

    The grain reaches `date_trunc` as a bound parameter, so a transposed
    mapping (WEEK meaning "month") would misbucket every week, quarter and
    year chart while every month-only test stayed green.
    """
    await document(
        amount_total=Decimal("10.00"),
        amount_kind="payment_made",
        document_date=date(2026, 2, 1),
    )
    await document(
        amount_total=Decimal("20.00"),
        amount_kind="payment_made",
        document_date=date(2026, 2, 28),
    )
    series = await chart_series(
        session, Rule(), grain=grain, split=None, currency="EUR", since=None, until=None
    )
    assert [cell.period for cell in series.cells] == periods
    assert series.total == Decimal("30.00")


@pytest.mark.asyncio
async def test_a_merged_pair_counts_as_one_payment(session, document) -> None:
    """One payment, two documents — the §9.4 footer's "15 payments from 18
    documents" shape. `sender=` is named on both or the pair never merges."""
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_due",
        document_date=date(2026, 4, 1),
        sender=VENDOR,
    )
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        sender=VENDOR,
    )
    # Assert the merge actually happened rather than assuming it: with the
    # sender left off, this test would pass a `documents == 2` assertion while
    # proving nothing, because two unmerged documents also count 2.
    merged = await session.execute(text("SELECT count(DISTINCT payment_id) FROM payments"))
    assert merged.scalar_one() == 1

    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    assert series.total == Decimal("60.00")
    assert series.payments == 1
    assert series.documents == 2


@pytest.mark.asyncio
async def test_a_deleted_twin_is_not_counted_among_the_documents(session, document) -> None:
    """`payments` builds its reachability from live documents only, so a
    soft-deleted twin has no row there at all. Counting documents off
    `documents` directly, or off a payment relation that ignored `deleted_at`,
    would report the deleted one as money the chart covered."""
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_due",
        document_date=date(2026, 4, 1),
        sender=VENDOR,
        deleted=True,
    )
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        sender=VENDOR,
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    assert series.total == Decimal("60.00")
    assert series.payments == 1
    assert series.documents == 1


@pytest.mark.asyncio
async def test_the_fixture_writes_usd_per_unit_not_the_inverse(session, fx_rates) -> None:
    """Pin the direction the `fx_rates` fixture writes, rather than trusting it.

    `library.fx` stores `rate_to_base` as the value of one unit in USD, so
    GBP 100 at 1.20 is USD 120 — not USD 83.33. A flipped fixture would still
    make `test_each_amount_converts_at_its_own_date_not_the_periods` fail on a
    *number*, which reads as an implementation bug rather than a fixture one.
    """
    await fx_rates([("2026-04-01", "GBP", "1.20")])
    converted = await convert_amount(session, Decimal("100.00"), "GBP", "USD", date(2026, 4, 2))
    assert converted == Decimal("120.00")


@pytest.mark.asyncio
async def test_each_amount_converts_at_its_own_date_not_the_periods(
    session, document, fx_rates
) -> None:
    """Two documents in one month at different rates. Converting the
    period's sum at one rate gives a different, wrong number."""
    await fx_rates(
        [
            ("2026-04-01", "USD", "1.00"),
            ("2026-04-01", "GBP", "1.20"),
            ("2026-04-20", "GBP", "1.50"),
        ]
    )
    await document(
        amount_total=Decimal("100.00"),
        currency="GBP",
        amount_kind="payment_made",
        document_date=date(2026, 4, 2),
    )
    await document(
        amount_total=Decimal("100.00"),
        currency="GBP",
        amount_kind="payment_made",
        document_date=date(2026, 4, 25),
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="USD", since=None, until=None
    )
    assert series.total == Decimal("270.00")


@pytest.mark.asyncio
async def test_an_unconvertible_amount_is_reported_never_counted_one_to_one(
    session, document, fx_rates
) -> None:
    """§9.3. Counting it 1:1 is the silent failure this replaces."""
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(
        amount_total=Decimal("100.00"),
        currency="USD",
        amount_kind="payment_made",
        document_date=date(2026, 4, 2),
    )
    await document(
        amount_total=Decimal("40.00"),
        currency="ZZZ",
        amount_kind="payment_made",
        document_date=date(2026, 4, 3),
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="USD", since=None, until=None
    )
    assert series.total == Decimal("100.00")
    assert [(u.currency, u.amount) for u in series.unconvertible] == [("ZZZ", Decimal("40.00"))]


@pytest.mark.asyncio
async def test_an_unconvertible_refund_lowers_the_reported_amount(
    session, document, fx_rates
) -> None:
    """The reported amount is the *net* that is missing from the total, so a
    refund lowers it exactly as it would have lowered the total. A regression
    to `abs()` is invisible against a single positive row — and Task 10 merges
    this list with the footer's by currency, which is where the sign matters."""
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(
        amount_total=Decimal("100.00"),
        currency="ZZZ",
        amount_kind="payment_made",
        document_date=date(2026, 4, 2),
    )
    await document(
        amount_total=Decimal("40.00"),
        currency="ZZZ",
        amount_kind="refund",
        document_date=date(2026, 4, 3),
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="USD", since=None, until=None
    )
    assert series.cells == []
    assert series.total == Decimal("0")
    assert [(u.currency, u.amount, u.documents) for u in series.unconvertible] == [
        ("ZZZ", Decimal("60.00"), 2)
    ]


@pytest.mark.asyncio
async def test_a_document_with_no_currency_is_reported_rather_than_crashing(
    session, document, fx_rates
) -> None:
    """A NULL currency is a permitted live state, not a corrupt row.

    `documents.currency` is nullable and `amount_currency_coupling` is a
    *warn*, not a block, so an amount-bearing, dated, summable document with no
    currency reaches `spend_facts` untouched. It has no usable rate, which is
    what `unconvertible` means — reported, never dropped and never counted 1:1
    (§9.3). Before this was handled the row reached `Unconvertible(currency=None)`
    and raised, or `sorted()` raised `TypeError` comparing str to None as soon
    as a second unconvertible currency appeared: a 500 on every chart route
    whose range contained one such document, not a wrong number.
    """
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(
        amount_total=Decimal("100.00"),
        currency="USD",
        amount_kind="payment_made",
        document_date=date(2026, 4, 2),
    )
    await document(
        amount_total=Decimal("25.00"),
        currency=None,
        amount_kind="payment_made",
        document_date=date(2026, 4, 3),
    )
    await document(
        amount_total=Decimal("40.00"),
        currency="ZZZ",
        amount_kind="payment_made",
        document_date=date(2026, 4, 4),
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="USD", since=None, until=None
    )
    assert series.total == Decimal("100.00")
    assert [(u.currency, u.amount) for u in series.unconvertible] == [
        ("ZZZ", Decimal("40.00")),
        (None, Decimal("25.00")),
    ]


@pytest.mark.asyncio
async def test_the_range_filters_the_data_rather_than_clamping_the_axis(session, document) -> None:
    """Spec §2.5 and §10.3.2: the old page clamped the axis and left the
    statistics computed over six years, so the headline and the chart
    disagreed. The total must move when the range does."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind="payment_made",
        document_date=date(2025, 1, 1),
    )
    await document(
        amount_total=Decimal("20.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
    )
    await document(
        amount_total=Decimal("35.00"),
        amount_kind="payment_made",
        document_date=date(2026, 9, 5),
    )

    async def _total(since: date | None, until: date | None) -> Series:
        return await chart_series(
            session,
            Rule(),
            grain=Grain.MONTH,
            split=None,
            currency="EUR",
            since=since,
            until=until,
        )

    open_ended = await _total(date(2026, 1, 1), None)
    assert open_ended.total == Decimal("55.00")
    assert [cell.period for cell in open_ended.cells] == [date(2026, 4, 1), date(2026, 9, 1)]

    # Closed at both ends. `until` is otherwise never bound anywhere in this
    # file, so an inverted comparator or a since/until swap would be silent —
    # and Task 10 binds it on every route. A swap makes this window empty.
    closed = await _total(date(2026, 1, 1), date(2026, 6, 30))
    assert closed.total == Decimal("20.00")
    assert len(closed.cells) == 1

    # Both comparators are inclusive: a single-day window on a document's own
    # date contains it.
    assert (await _total(date(2026, 4, 1), date(2026, 4, 1))).total == Decimal("20.00")


@pytest.mark.asyncio
async def test_a_rule_restricts_the_rows_it_names(session, document, facets) -> None:
    await document(
        amount_total=Decimal("10.00"),
        amount_kind="payment_made",
        labels={"category": "services"},
    )
    await document(
        amount_total=Decimal("90.00"),
        amount_kind="payment_made",
        labels={"category": "supplies"},
    )
    series = await chart_series(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        grain=Grain.MONTH,
        split=None,
        currency="EUR",
        since=None,
        until=None,
    )
    assert series.total == Decimal("10.00")
