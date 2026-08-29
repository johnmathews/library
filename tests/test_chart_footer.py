"""What the total did not count. Every branch is money that would otherwise
vanish from the archive with no way to notice.

The most important test in the file is
`test_every_kind_of_money_is_accounted_for_somewhere`: the individual cases each
pin one branch, but only the balance proves there is no *gap between* the
branches — which is how a NULL `amount_kind` was found to disappear entirely
(spec §8.1.1: NULL means "not yet decided", and the live archive has such
documents today).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from library.charts.footer import chart_footer
from library.charts.query import chart_series
from library.charts.rule import Clause, Rule
from library.models import AMOUNT_SIGN, AmountKind, Grain

#: Invented vendor name. Nothing here corresponds to a real sender or amount.
VENDOR = "Corvus Test Assurance"


@pytest.mark.asyncio
async def test_a_coverage_limit_is_reported_as_excluded(session, document) -> None:
    await document(
        amount_total=Decimal("500000.00"),
        amount_kind="coverage_limit",
        document_date=date(2026, 4, 1),
    )
    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert [(g.amount_kind, g.amount) for g in footer.excluded] == [
        ("coverage_limit", Decimal("500000.00"))
    ]


@pytest.mark.asyncio
async def test_a_refund_is_reported_as_netted_not_as_excluded(session, document) -> None:
    """§9.4: a refund IS in the total, and lowering it is the point.
    Reporting it as excluded would say the opposite of what is true."""
    await document(
        amount_total=Decimal("49.00"), amount_kind="refund", document_date=date(2026, 4, 1)
    )
    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert footer.netted_refunds == Decimal("49.00")
    assert footer.refund_count == 1
    assert all(g.amount_kind != "refund" for g in footer.excluded)


@pytest.mark.asyncio
async def test_money_with_no_label_for_a_rules_facet_is_reported(session, document, facets) -> None:
    """The line §9.4 calls the most important one.

    An unlabelled document matches no rule, so it is invisible in every
    chart. Reporting it inside the chart whose window contains it turns the
    archive's worst failure mode into a visible task.
    """
    await document(
        amount_total=Decimal("89.20"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        labels={},
    )
    footer = await chart_footer(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        currency="EUR",
        since=None,
        until=None,
        facets_in_rule={"category"},
    )
    assert footer.uncategorised is not None
    assert footer.uncategorised.amount == Decimal("89.20")
    assert footer.uncategorised.documents == 1


@pytest.mark.asyncio
async def test_a_labelled_document_outside_the_rule_is_not_uncategorised(
    session, document, facets
) -> None:
    """It was categorised; the owner simply asked a different question.
    Reporting it would make every chart accuse the archive of a gap it does
    not have, and the real gaps would be lost in the noise."""
    await document(
        amount_total=Decimal("30.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        labels={"category": "supplies"},
    )
    footer = await chart_footer(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        currency="EUR",
        since=None,
        until=None,
        facets_in_rule={"category"},
    )
    assert footer.uncategorised is None
    # Nor anywhere else: the document belongs to a different question, and the
    # footer of *this* chart is not an inventory of the archive.
    assert footer.excluded == []
    assert footer.unclassified is None
    assert footer.undated is None
    assert footer.unaccounted is None


@pytest.mark.asyncio
async def test_a_dated_none_document_is_undated_money_when_it_has_no_date(
    session, document
) -> None:
    """A summable amount with no document_date cannot be bucketed, so
    Task 6's query drops it. Here is where it surfaces."""
    await document(amount_total=Decimal("12.00"), amount_kind="payment_made", document_date=None)
    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert footer.undated is not None
    assert footer.undated.amount == Decimal("12.00")


@pytest.mark.asyncio
async def test_the_footer_respects_the_charts_date_window(session, document) -> None:
    """Reporting money from outside the window would attach a gap to a chart
    that never claimed to cover it."""
    await document(
        amount_total=Decimal("500.00"),
        amount_kind="coverage_limit",
        document_date=date(2024, 1, 1),
    )
    footer = await chart_footer(
        session,
        Rule(),
        currency="EUR",
        since=date(2026, 1, 1),
        until=None,
        facets_in_rule=set(),
    )
    assert footer.excluded == []
    # Every group, not just this one: an out-of-window row misrouted into
    # `unclassified`, `uncategorised` or `undated` would attach the same false
    # gap to the same chart, and asserting one group would not see it.
    assert footer.unclassified is None
    assert footer.uncategorised is None
    assert footer.undated is None
    assert footer.unaccounted is None


@pytest.mark.asyncio
async def test_an_undecided_amount_kind_is_reported_as_unclassified(session, document) -> None:
    """Ruling 8. A NULL `amount_kind` means *not yet decided* (§8.1.1), so it
    is neither excluded (which reads as a deliberate non-spend) nor summed.

    Before this group existed such a document matched none of the footer's
    categories — `excluded` filters NOT NULL, and the other three require a
    summable kind — so it appeared nowhere at all while contributing to no
    total: exactly the silent disappearance §9.4 exists to prevent, on the one
    class of document the archive has most of.
    """
    await document(amount_total=Decimal("77.00"), amount_kind=None, document_date=date(2026, 4, 1))
    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert footer.unclassified is not None
    assert footer.unclassified.amount == Decimal("77.00")
    assert footer.unclassified.documents == 1
    # Rendered, never the SQL NULL: `null` in a footer reads as a bug in the
    # footer rather than as a document waiting to be classified.
    assert footer.unclassified.amount_kind == "unclassified"
    assert footer.excluded == []


@pytest.mark.asyncio
async def test_every_kind_of_money_is_accounted_for_somewhere(session, document, facets) -> None:
    """The balance. Worth more than any single case above.

    One document of every `AmountKind`, one with no kind at all, one with no
    date and one with no label — and the money the footer reports plus the
    money the total counted must equal the money seeded. A category that falls
    between the branches shows up here as a difference, which is the only way
    to catch a gap nobody thought to write a test for.

    `netted_refunds` is deliberately absent from the sum: it is a lens on the
    total (the refund is *in* it, §9.4), not a fifth category, so adding it
    would double-count the refund. It is asserted separately below.
    """
    services = {"category": "services"}
    seeds: list[tuple[AmountKind | None, Decimal, dict[str, str], date | None]] = [
        (AmountKind.PAYMENT_DUE, Decimal("100.00"), services, date(2026, 4, 1)),
        (AmountKind.PAYMENT_MADE, Decimal("200.00"), services, date(2026, 4, 2)),
        (AmountKind.ASSESSMENT, Decimal("300.00"), services, date(2026, 4, 3)),
        (AmountKind.REFUND, Decimal("50.00"), services, date(2026, 4, 4)),
        (AmountKind.COVERAGE_LIMIT, Decimal("500000.00"), services, date(2026, 4, 5)),
        (AmountKind.BALANCE, Decimal("1000.00"), services, date(2026, 4, 6)),
        (AmountKind.ESTIMATE, Decimal("450.00"), services, date(2026, 4, 7)),
        (AmountKind.NONE, Decimal("7.00"), services, date(2026, 4, 8)),
        # No kind: undecided, and the reason this test exists.
        (None, Decimal("77.00"), services, date(2026, 4, 9)),
        # Summable but unbucketable.
        (AmountKind.PAYMENT_MADE, Decimal("12.00"), services, None),
        # Summable and in the window, but unlabelled for the rule's facet.
        (AmountKind.PAYMENT_MADE, Decimal("89.20"), {}, date(2026, 4, 10)),
    ]
    for kind, amount, labels, on_date in seeds:
        await document(amount_total=amount, amount_kind=kind, labels=labels, document_date=on_date)

    rule = Rule(all=[Clause(facet="category", op="in", values=["services"])])
    series = await chart_series(
        session, rule, grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    footer = await chart_footer(
        session, rule, currency="EUR", since=None, until=None, facets_in_rule={"category"}
    )

    # A summable amount enters signed (a refund lowers what it is part of); a
    # kind that never enters a total has no sign, so it is accounted as the
    # magnitude it is. Both conventions are the ones the footer itself reports.
    seeded_money = sum(
        (
            AMOUNT_SIGN[kind] * amount if kind in AMOUNT_SIGN else amount
            for kind, amount, _, _ in seeds
        ),
        Decimal(0),
    )
    groups = [
        *footer.excluded,
        *[
            g
            for g in (
                footer.unclassified,
                footer.undated,
                footer.uncategorised,
                # In the sum on purpose: the balance is only a safety net if the
                # net catches money it did not expect. A bucket left out here
                # would make an unforeseen shape read as a shortfall of unknown
                # origin instead of as itself.
                footer.unaccounted,
            )
            if g is not None
        ],
    ]
    accounted = series.total + sum((g.amount for g in groups), Decimal(0))
    assert accounted == seeded_money, (
        f"money fell through the categories: seeded {seeded_money}, "
        f"total {series.total} + footer {accounted - series.total}"
    )

    # And every document, not just every euro: an amount can balance while a
    # document is unrepresented (a payment and an equal refund net to zero).
    assert series.documents + sum(g.documents for g in groups) == len(seeds)
    assert series.unconvertible == [] and footer.unconvertible == []
    # Nothing predicted-by-nobody today. If this ever fires, the `CASE` has a
    # hole and the group above says how much money is in it.
    assert footer.unaccounted is None
    # The refund is inside `series.total`, reported again only as the lens.
    assert (footer.netted_refunds, footer.refund_count) == (Decimal("50.00"), 1)


@pytest.mark.asyncio
async def test_one_payment_documented_twice_is_reported_once(session, document) -> None:
    """The double-count §2.4 exists to remove, on the footer's side. Two
    documents describing one excluded amount are one amount, not two — a
    footer reading its rows without `is_canonical` would report EUR 1,000,000
    of coverage from a EUR 500,000 policy.

    `documents` here counts the canonical rows behind the amount, so it says 1
    where `Series.documents` would say 2 for the same pair. The two counts
    answer different questions and are deliberately not aligned.
    """
    for on_date in (date(2026, 4, 1), date(2026, 4, 20)):
        await document(
            amount_total=Decimal("500000.00"),
            amount_kind="coverage_limit",
            document_date=on_date,
            sender=VENDOR,
            reference="TEST-REF-4417",
        )
    # Assert the merge happened rather than assuming it: unmerged documents
    # would make the assertion below fail for the right-looking wrong reason.
    merged = await session.execute(text("SELECT count(DISTINCT payment_id) FROM payments"))
    assert merged.scalar_one() == 1

    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert [(g.amount_kind, g.amount, g.documents) for g in footer.excluded] == [
        ("coverage_limit", Decimal("500000.00"), 1)
    ]


@pytest.mark.asyncio
async def test_the_netted_refund_is_the_one_the_total_actually_netted(
    session, document, facets
) -> None:
    """The header line reads "including 1 refund netted off" beside the total,
    so it has to describe *that* total. A refund from a different question
    would make the header's arithmetic wrong while looking plausible."""
    await document(
        amount_total=Decimal("49.00"),
        amount_kind="refund",
        document_date=date(2026, 4, 1),
        labels={"category": "services"},
    )
    await document(
        amount_total=Decimal("20.00"),
        amount_kind="refund",
        document_date=date(2026, 4, 2),
        labels={"category": "supplies"},
    )
    rule = Rule(all=[Clause(facet="category", op="in", values=["services"])])
    series = await chart_series(
        session, rule, grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    footer = await chart_footer(
        session, rule, currency="EUR", since=None, until=None, facets_in_rule={"category"}
    )
    assert series.total == Decimal("-49.00")
    assert (footer.netted_refunds, footer.refund_count) == (Decimal("49.00"), 1)


@pytest.mark.asyncio
async def test_a_refund_lowers_the_group_it_belongs_to_rather_than_inflating_it(
    session, document
) -> None:
    """A group of summable money is a *net*, exactly like the total: a refund
    that could not be bucketed is money coming back, so reporting it as
    +40 would overstate the gap by 80.

    The unconvertible line carries the same convention, and that is where it
    bites hardest — Task 10 merges it with `query.py`'s list by currency, so
    the two must agree on what a refund does to a sum.
    """
    await document(amount_total=Decimal("100.00"), amount_kind="payment_made", document_date=None)
    await document(amount_total=Decimal("40.00"), amount_kind="refund", document_date=None)
    await document(
        amount_total=Decimal("30.00"),
        amount_kind="refund",
        currency="ZZZ",
        document_date=None,
    )
    footer = await chart_footer(
        session, Rule(), currency="EUR", since=None, until=None, facets_in_rule=set()
    )
    assert footer.undated is not None
    assert (footer.undated.amount, footer.undated.documents) == (Decimal("60.00"), 2)
    assert [(u.currency, u.amount) for u in footer.unconvertible] == [("ZZZ", Decimal("-30.00"))]
    # Undated money is not in any total, so it was netted off nothing.
    assert (footer.netted_refunds, footer.refund_count) == (Decimal("0"), 0)


@pytest.mark.asyncio
async def test_an_unlabelled_row_a_not_in_rule_already_counted_is_not_uncategorised(
    session, document, facets
) -> None:
    """A `not_in` rule matches unlabelled rows on purpose (see `rule.py`), so
    such a row is already *in* the total. Reporting it as a gap as well would
    count it twice and send the owner to fix a document the chart did not
    miss."""
    await document(
        amount_total=Decimal("15.00"),
        amount_kind="payment_made",
        document_date=date(2026, 4, 1),
        labels={},
    )
    rule = Rule(all=[Clause(facet="category", op="not_in", values=["supplies"])])
    series = await chart_series(
        session, rule, grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None
    )
    footer = await chart_footer(
        session, rule, currency="EUR", since=None, until=None, facets_in_rule={"category"}
    )
    assert series.total == Decimal("15.00")
    assert footer.uncategorised is None


@pytest.mark.asyncio
async def test_money_the_footer_cannot_convert_is_reported_not_counted_one_to_one(
    session, document, fx_rates
) -> None:
    """§9.3, on the footer's own rows. A currency with no rate has no size in
    the chart's currency, and a NULL currency — a permitted live state, since
    `documents.currency` is nullable and the coupling check only warns — has
    none either. Both are reported with their document counts rather than
    dropped or added at 1:1."""
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(
        amount_total=Decimal("40.00"),
        amount_kind="coverage_limit",
        currency="ZZZ",
        document_date=date(2026, 4, 2),
    )
    await document(
        amount_total=Decimal("25.00"),
        amount_kind="payment_made",
        currency=None,
        document_date=None,
    )
    footer = await chart_footer(
        session, Rule(), currency="USD", since=None, until=None, facets_in_rule=set()
    )
    # Neither group can state an amount, so neither group is reported at all.
    assert footer.excluded == []
    assert footer.undated is None
    # None sorts last, as it does in `query.py`: a currency code and None
    # cannot be compared, so a bare `sorted` raises as soon as both appear.
    assert [(u.currency, u.amount, u.documents) for u in footer.unconvertible] == [
        ("ZZZ", Decimal("40.00"), 1),
        (None, Decimal("25.00"), 1),
    ]


@pytest.mark.asyncio
async def test_a_refund_the_total_could_not_convert_is_not_reported_twice(
    session, document, fx_rates
) -> None:
    """`query.py` already reports the rows that would have entered the total,
    and Task 10 merges the two lists by currency. Reporting them here as well
    would double every unconvertible amount in the merged footer — and inflate
    `netted_refunds` with a refund the total never netted."""
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(
        amount_total=Decimal("30.00"),
        amount_kind="refund",
        currency="ZZZ",
        document_date=date(2026, 4, 2),
        sender=VENDOR,
    )
    series = await chart_series(
        session, Rule(), grain=Grain.MONTH, split=None, currency="USD", since=None, until=None
    )
    footer = await chart_footer(
        session, Rule(), currency="USD", since=None, until=None, facets_in_rule=set()
    )
    assert [(u.currency, u.amount) for u in series.unconvertible] == [("ZZZ", Decimal("-30.00"))]
    assert footer.unconvertible == []
    assert (footer.netted_refunds, footer.refund_count) == (Decimal("0"), 0)
