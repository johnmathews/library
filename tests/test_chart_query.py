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

from library.charts.query import Series, chart_cell, chart_series, period_start
from library.charts.rule import Clause, Rule
from library.fx import convert_amount
from library.models import Grain
from library.spend_lines import LineInput, replace_lines

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
        # year. The May document is what separates QUARTER from YEAR: with only
        # the February pair both grains expect `[2026-01-01]`, so a transposition
        # between exactly those two would have stayed green.
        (Grain.WEEK, [date(2026, 1, 26), date(2026, 2, 23), date(2026, 5, 11)]),
        (Grain.MONTH, [date(2026, 2, 1), date(2026, 5, 1)]),
        (Grain.QUARTER, [date(2026, 1, 1), date(2026, 4, 1)]),
        (Grain.YEAR, [date(2026, 1, 1)]),
    ],
)
@pytest.mark.asyncio
async def test_every_grain_buckets_by_its_own_calendar_unit(
    session, document, grain, periods
) -> None:
    """All four grains, not just MONTH, and no two of them agreeing.

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
    await document(
        amount_total=Decimal("5.00"),
        amount_kind="payment_made",
        document_date=date(2026, 5, 15),
    )
    series = await chart_series(
        session, Rule(), grain=grain, split=None, currency="EUR", since=None, until=None
    )
    assert [cell.period for cell in series.cells] == periods
    assert series.total == Decimal("35.00")


@pytest.mark.parametrize("grain", list(Grain))
@pytest.mark.asyncio
async def test_the_boundary_the_api_validates_is_the_bucket_the_chart_drew(
    session, document, grain
) -> None:
    """`period_start` and the chart's own `period` column are one expression.

    The API refuses a `period` that is not a bucket boundary. If it computed
    that boundary from a second definition — the Python `date_trunc` this
    replaced — the two could disagree, and the visible failure would be a 422
    on a cell the chart had just drawn. Asserted by comparison against the
    engine rather than against a literal, for all four grains, so a divergence
    in either expression is caught rather than a wrong constant.
    """
    day = date(2026, 5, 15)
    await document(amount_total=Decimal("10.00"), amount_kind="payment_made", document_date=day)
    series = await chart_series(
        session, Rule(), grain=grain, split=None, currency="EUR", since=None, until=None
    )
    assert [cell.period for cell in series.cells] == [await period_start(session, grain, day)]


@pytest.mark.asyncio
async def test_a_split_document_fills_two_buckets_and_one_total(session, document, facets) -> None:
    """The feature the branch is *for*, through the engine rather than the view.

    One 100.00 document allocated 60/40 across two `scope` values. Every other
    split-invariance test in this file uses unsplit documents, so the seam
    between `spend_lines` and `chart_series` — the one place a document's money
    is divided before it is bucketed — was traced and never executed.

    The three assertions are the three things that can go wrong independently:
    the parts land in the buckets their *lines* name, they still add to the
    document's own amount under every axis, and the document is one payment —
    not two — however many pieces it was cut into.
    """
    row = await document(
        amount_total=Decimal("100.00"),
        amount_kind="payment_made",
        labels={"category": "software"},
    )
    await replace_lines(
        session,
        row.id,
        [
            LineInput(amount=Decimal("60.00"), labels={"scope": "business"}),
            LineInput(amount=Decimal("40.00"), labels={"scope": "personal"}),
        ],
    )
    await session.commit()

    async def series(split: str | None) -> Series:
        return await chart_series(
            session, Rule(), grain=Grain.MONTH, split=split, currency="EUR", since=None, until=None
        )

    by_scope = await series("scope")
    assert sorted((cell.split_value or "", cell.total) for cell in by_scope.cells) == [
        ("business", Decimal("60.00")),
        ("personal", Decimal("40.00")),
    ]
    assert by_scope.total == Decimal("100.00")

    flat = await series(None)
    assert [(cell.split_value, cell.total) for cell in flat.cells] == [(None, Decimal("100.00"))]

    # The lines carry no `category` of their own, so both inherit the
    # document's — a split that dropped inheritance would show 0.00 here while
    # every assertion above still passed.
    by_category = await series("category")
    assert [(cell.split_value, cell.total) for cell in by_category.cells] == [
        ("software", Decimal("100.00"))
    ]

    for answer in (by_scope, flat, by_category):
        assert answer.total == Decimal("100.00")
        assert answer.payments == 1, "one document is one payment however many lines it has"
        assert answer.documents == 1


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


@pytest.mark.asyncio
async def test_a_cell_lists_its_payments_with_every_document_in_the_group(
    session, document
) -> None:
    """Including the NON-canonical one. The drill-through is where a wrong
    merge is noticed and split, so hiding the other half of a merged pair
    hides the only evidence the merge was wrong (§9.5).

    `sender=` is named on both documents or they never merge at all: the
    fixture defaults it to None and every payment rule requires a non-NULL
    matching `sender_id`, so without it this test would assert `documents == 2`
    against two *unmerged* documents and prove nothing.
    """
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_due",
        document_date=date(2026, 4, 1),
        title="doc-due",
        sender=VENDOR,
    )
    await document(
        amount_total=Decimal("60.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        title="doc-made",
        sender=VENDOR,
    )
    merged = await session.execute(text("SELECT count(DISTINCT payment_id) FROM payments"))
    assert merged.scalar_one() == 1

    payments = await chart_cell(
        session,
        Rule(),
        grain=Grain.MONTH,
        split=None,
        split_value=None,
        period=date(2026, 4, 1),
        currency="EUR",
        since=None,
        until=None,
    )
    assert len(payments) == 1
    # 60, not 120: the total is the canonical row's alone, or the merged pair
    # would be counted twice and the panel would contradict the bar.
    assert payments[0].total == Decimal("60.00")
    assert len(payments[0].documents) == 2
    assert sum(d.is_canonical for d in payments[0].documents) == 1
    assert {d.title for d in payments[0].documents} == {"doc-due", "doc-made"}


@pytest.mark.asyncio
async def test_a_cells_payments_sum_to_the_cell_shown_in_the_chart(
    session, seeded, document
) -> None:
    """The property that makes drill-through trustworthy. Asserted by
    comparison against `chart_series`, so it cannot pass by coincidence.

    Run over three sets of arguments, because `chart_cell` takes five inputs
    that can each be dropped independently and a single all-defaults pass
    exercises none of them: the second pass carries a `since`/`until` window
    that cuts INSIDE a drilled bucket, the third a non-empty rule and a
    non-MONTH grain. A window that only cut between buckets would prove
    nothing — the period narrowing already excludes those rows — so the extra
    documents below straddle the bounds inside April.

    `seeded` alone does not discriminate on the kind filter either. Its only
    non-summable document (a coverage ceiling) is labelled `accountancy`, a
    category no *summable* row carries — so it produces no cell of its own,
    the loop never drills that bucket, and `chart_cell` dropping the
    `amount_kind = ANY(:summable)` filter passes unnoticed (confirmed by
    mutation: 22 passed). The two ceilings added here sit in buckets the
    series really draws — one labelled, one unlabelled, so both arms of the
    split match are covered — and make the same mutation fail.
    """
    # Non-summable, inside buckets the series draws: guards the kind filter.
    await document(
        amount_total=Decimal("9500.00"),
        amount_kind="coverage_limit",
        document_date=date(2026, 4, 5),
        labels={"category": "services"},
    )
    await document(
        amount_total=Decimal("8200.00"),
        amount_kind="coverage_limit",
        document_date=date(2026, 4, 6),
        labels={},
    )
    # Summable, same month and same `services` bucket as one of `seeded`'s
    # rows but on either side of the window used in pass 2: guards `since`
    # and `until` separately.
    await document(
        amount_total=Decimal("11.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 3),
        labels={"category": "services"},
    )
    await document(
        amount_total=Decimal("13.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 28),
        labels={"category": "services"},
    )

    async def _drill(
        rule: Rule,
        *,
        grain: Grain,
        split: str,
        since: date | None,
        until: date | None,
    ) -> None:
        series = await chart_series(
            session, rule, grain=grain, split=split, currency="EUR", since=since, until=until
        )
        # Assert the shape rather than trusting it: a one-cell series, or one
        # with no unlabelled bucket, would make the loop below near-vacuous.
        assert len(series.cells) > 1
        assert None in {cell.split_value for cell in series.cells}
        for cell in series.cells:
            payments = await chart_cell(
                session,
                rule,
                grain=grain,
                split=split,
                split_value=cell.split_value,
                period=cell.period,
                currency="EUR",
                since=since,
                until=until,
            )
            where = f"{grain.value} {cell.period}/{cell.split_value}"
            assert payments, f"cell {where} drilled through to nothing"
            assert sum(p.total for p in payments) == cell.total, where
            # The payment *set* matches too, not just its sum: a cell whose
            # payments were right in total but wrong in membership would
            # still be a broken panel.
            assert len(payments) == cell.payments, where

    # 1. Everything: no rule, no window, the default grain.
    await _drill(Rule(), grain=Grain.MONTH, split="category", since=None, until=None)
    # 2. A window cutting inside April's bucket at BOTH ends. Dropping `since`
    #    readmits the 04-02 and 04-03 rows to the `services` cell; dropping
    #    `until` readmits the 04-28 one.
    await _drill(
        Rule(),
        grain=Grain.MONTH,
        split="category",
        since=date(2026, 4, 6),
        until=date(2026, 4, 25),
    )
    # 3. A non-empty rule and a non-MONTH grain, on a different split axis.
    #    Dropping the rule floods the Q2 `scope IS NULL` bucket, which holds
    #    four summable rows of which only one is `software`.
    await _drill(
        Rule(all=[Clause(facet="category", op="in", values=["software"])]),
        grain=Grain.QUARTER,
        split="scope",
        since=None,
        until=None,
    )


@pytest.mark.asyncio
async def test_the_null_split_bucket_is_reachable(session, document, facets) -> None:
    """`split_value=None` must select the unlabelled rows, not every row.
    `= NULL` is never true in SQL — this needs `IS NOT DISTINCT FROM`."""
    await document(
        amount_total=Decimal("5.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        labels={},
    )
    await document(
        amount_total=Decimal("7.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        labels={"category": "services"},
    )
    payments = await chart_cell(
        session,
        Rule(),
        grain=Grain.MONTH,
        split="category",
        split_value=None,
        period=date(2026, 4, 1),
        currency="EUR",
        since=None,
        until=None,
    )
    # Both bounds matter: `=` empties the bucket, and a narrowing dropped
    # altogether returns 12.00 — the labelled row as well.
    assert sum(p.total for p in payments) == Decimal("5.00")
    assert len(payments) == 1


@pytest.mark.asyncio
async def test_a_cell_skips_an_unconvertible_row_exactly_as_the_chart_did(
    session, document, fx_rates
) -> None:
    """§9.3: never dropped, never counted 1:1 — and never counted *here*.

    The chart could not put this money in the cell, so the panel must not
    either; the footer is where it is accounted for. A payment whose every row
    is unconvertible is therefore absent from the drill-through, because it was
    absent from the bar. Asserted against `chart_series` so the two cannot
    disagree.
    """
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
    assert [(u.currency, u.amount) for u in series.unconvertible] == [("ZZZ", Decimal("40.00"))]

    payments = await chart_cell(
        session,
        Rule(),
        grain=Grain.MONTH,
        split=None,
        split_value=None,
        period=date(2026, 4, 1),
        currency="USD",
        since=None,
        until=None,
    )
    assert len(payments) == 1
    assert sum(p.total for p in payments) == series.cells[0].total == Decimal("100.00")


@pytest.mark.asyncio
async def test_a_cell_converts_each_amount_at_its_own_date_not_the_periods(
    session, document, fx_rates
) -> None:
    """§9.3, in the drill path. Two rates inside one month.

    Every other drill test is single-currency, and `convert_amount`
    short-circuits when the currencies match — so the conversion *date* is
    never read and `chart_cell` converting at the bucket's date instead of the
    document's stays green through all of them. It is a wrong number, not an
    error: the panel would quietly disagree with the bar. Asserted against
    `chart_series` so the two cannot drift apart.
    """
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
    payments = await chart_cell(
        session,
        Rule(),
        grain=Grain.MONTH,
        split=None,
        split_value=None,
        period=date(2026, 4, 1),
        currency="USD",
        since=None,
        until=None,
    )
    # 270, not 240: converting both rows at the period's own date (2026-04-01,
    # rate 1.20) is the mutation this test exists to redden.
    assert sum(p.total for p in payments) == series.cells[0].total == Decimal("270.00")


@pytest.mark.asyncio
async def test_a_hand_merged_document_with_no_amount_is_listed_rather_than_crashing(
    session, document
) -> None:
    """A manual MERGE is the merge most likely to be wrong, and §9.5 says the
    panel is where it is noticed and split.

    The rule arms of `payment_edges` all require `amount_total IS NOT NULL` and
    equal amounts, but the OVERRIDE arm requires only two live documents — so a
    hand-merged document may carry no amount and no currency, and 0035's
    `eligible` CTE then gives it no `spend_facts` row at all. Two things are
    pinned here, both load-bearing and both invisible otherwise:

    * the document list is read from `payments`, not from `spend_facts`, or
      this document would be silently absent — the panel would show a merge of
      one, which is precisely the evidence the owner needs;
    * `CellDocument.amount` and `.currency` are optional. Tidied back to the
      brief's non-optional `Decimal` / `str`, this raises `ValidationError` and
      drilling the cell answers 500 rather than a wrong number.
    """
    priced = await document(
        amount_total=Decimal("42.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        title="doc-priced",
    )
    unpriced = await document(
        amount_total=None,
        currency=None,
        amount_kind=None,
        document_date=date(2026, 4, 1),
        title="doc-unpriced",
    )
    low, high = sorted((priced.id, unpriced.id))
    await session.execute(
        text("INSERT INTO payment_overrides (kind, doc_a, doc_b) VALUES ('MERGE', :low, :high)"),
        {"low": low, "high": high},
    )
    await session.commit()
    merged = await session.execute(text("SELECT count(DISTINCT payment_id) FROM payments"))
    assert merged.scalar_one() == 1

    payments = await chart_cell(
        session,
        Rule(),
        grain=Grain.MONTH,
        split=None,
        split_value=None,
        period=date(2026, 4, 1),
        currency="EUR",
        since=None,
        until=None,
    )
    assert len(payments) == 1
    # The amountless document carried no money, so the cell is unchanged.
    assert payments[0].total == Decimal("42.00")
    listed = {d.title: d for d in payments[0].documents}
    assert set(listed) == {"doc-priced", "doc-unpriced"}
    assert listed["doc-priced"].is_canonical is True
    assert listed["doc-priced"].amount == Decimal("42.00")
    # No `spend_facts` row at all, so no canonicality — and nothing to show
    # but the fact of the merge, which is the point.
    assert listed["doc-unpriced"].is_canonical is False
    assert listed["doc-unpriced"].amount is None
    assert listed["doc-unpriced"].currency is None
