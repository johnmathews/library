"""Aggregate queries over `spend_facts`.

Every SELECT against `spend_facts` lives here; no router builds SQL.

Two invariants this module exists to hold:

* **The total is invariant across split changes** (§9.2). The split is a
  GROUP BY over the same rows the flat total sums, never an extra filter, and
  an unlabelled row lands in a NULL bucket rather than being dropped.
* **Each amount converts at its own document's date** (§9.3), not at the
  period's. Converting a period's sum at one rate is a different number
  whenever a rate moves inside the bucket.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from library.charts.rule import Rule, rule_predicate
from library.fx import convert_amount
from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS, AmountKind, Grain

#: The one split axis that is a real column rather than a facet label (§9.2):
#: `sender` comes free because `spend_facts` carries `sender_id`.
SENDER_SPLIT = "sender"

#: The three possible split expressions. Chosen between as whole literals —
#: the caller's axis name is *bound* as `:split`, never interpolated into the
#: column list, because it reaches here from an LLM draft as well as the owner.
_SPLIT_NONE = "CAST(NULL AS text)"
_SPLIT_SENDER = "CAST(sf.sender_id AS text)"
_SPLIT_FACET = "sf.labels->>:split"

# Rows, not sums: conversion is per row and per date, so the aggregation
# happens in Python after each amount has been converted at its own date.
#
# `CAST(x AS type)`, never the `::type` shorthand, anywhere a bind parameter is
# nearby. `text()` parses `:name` itself, so `:since::date` leaves the parameter
# unbound and Postgres receives a literal colon. The `::` casts inside the
# `spend_facts` view (0035) are fine because that DDL carries no binds at all.
#
# `sf.date IS NOT NULL` excludes rows that cannot be bucketed. They are not
# lost: the footer (Task 7) accounts for them. A chart with a time axis simply
# has nowhere to draw them.
#
# ONE definition, read by `chart_series` and by `chart_cell` through
# `_rows_query` alone. The drill-through has to select over provably the same
# rows the chart summed, and a second copy of this SELECT would drift from it
# silently: the panel would list payments that do not add up to the bar the
# owner clicked, and nothing would go red. `chart_cell` narrows this query, it
# never restates it.
#: The time bucket, written once. It is both a selected column and — for the
#: drill-through — a filter, and the two have to be the same expression or the
#: panel opens a bucket the chart never drew. Editing one of two copies is the
#: whole failure mode.
_PERIOD_EXPR = "CAST(date_trunc(:grain, CAST(sf.date AS timestamp)) AS date)"

_ROWS_SQL = """
SELECT {period_expr} AS period,
       {split_expr}  AS split_value,
       sf.amount, sf.currency, sf.date, sf.amount_kind,
       sf.payment_id, sf.document_id
FROM spend_facts sf
WHERE sf.is_canonical
  AND sf.amount_kind = ANY(:summable)
  AND sf.date IS NOT NULL
  AND (CAST(:since AS date) IS NULL OR sf.date >= CAST(:since AS date))
  AND (CAST(:until AS date) IS NULL OR sf.date <= CAST(:until AS date))
  AND ({rule}){narrowing}
"""

# The drill-through's only addition to `_ROWS_SQL`: one time bucket and one
# split bucket. It is appended to the shared WHERE rather than replacing any
# part of it.
#
# `IS NOT DISTINCT FROM`, never `=`: `split_value=None` means the *unlabelled*
# bucket, and `= NULL` is never true, so `=` would make that bucket
# unreachable — the one cell in a chart whose rows are hardest to find by hand
# would be the one cell the panel could not open.
_CELL_NARROWING = """
  AND {period_expr} = CAST(:period AS date)
  AND {split_expr} IS NOT DISTINCT FROM CAST(:split_value AS text)"""

# `payments` holds one row per *live* document (its reachability is built from
# `documents WHERE deleted_at IS NULL`), so counting its rows for a set of
# payment ids counts the documents behind those payments and a soft-deleted
# twin is already absent.
_DOCUMENTS_SQL = "SELECT count(*) FROM payments WHERE payment_id = ANY(:payment_ids)"

# Every document in each payment group, canonical or not (§9.5). The panel is
# where a wrong merge is noticed and split, so hiding the other half of a
# merged pair hides the only evidence the merge was wrong.
#
# Read from `payments`, not from `spend_facts`: a manual MERGE override joins
# two live documents on the owner's say-so alone, with none of the rule arms'
# `amount_total IS NOT NULL` precondition, so a hand-merged document with no
# amount has no `spend_facts` row at all. That document is precisely a
# hand-made merge that may be wrong, which is what this list exists to show.
#
# `is_canonical` is a property of the `spend_facts` row, so it is read back
# from there and is false for a document that has none. Asked as EXISTS rather
# than joined: a document split across spend lines has one row per line, all
# carrying the same flag, and a join would list it once per line.
_CELL_DOCUMENTS_SQL = """
SELECT p.payment_id, d.id, d.title, d.document_date, d.amount_total,
       d.currency, d.amount_kind, d.reference,
       EXISTS (
         SELECT 1 FROM spend_facts sf
         WHERE sf.document_id = d.id AND sf.is_canonical
       ) AS is_canonical
FROM payments p
JOIN documents d ON d.id = p.document_id
WHERE p.payment_id = ANY(:payment_ids)
ORDER BY p.payment_id, d.id
"""


def _rows_query(
    rule: Rule,
    *,
    grain: Grain,
    split: str | None,
    since: date | None,
    until: date | None,
    cell: bool = False,
) -> tuple[TextClause, dict[str, object]]:
    """Build the one row-selecting query both public functions read.

    The split expression, the SELECT list, the WHERE clause and every bind but
    the drill-through's own two are defined here once. `chart_cell` asks for
    `cell=True` and adds `:period` and `:split_value`; it cannot select a
    different set of rows from `chart_series` without changing this function,
    which is the whole point of the extraction (§9.5 — the panel's numbers have
    to be the chart's numbers).

    The narrowing is a **flag, not a SQL string**. Taking the fragment from the
    caller would leave the guarantee resting on the caller's manners: the
    narrowing is appended after `AND ({rule})`, `AND` binds tighter than `OR`,
    and a fragment of `" OR TRUE"` would defeat the entire WHERE clause and
    hand the panel every row in the archive. A boolean makes a widening
    narrowing unrepresentable rather than merely unwritten.
    """
    fragment, params = rule_predicate(rule)
    if split is None:
        split_expr = _SPLIT_NONE
    elif split == SENDER_SPLIT:
        split_expr = _SPLIT_SENDER
    else:
        split_expr = _SPLIT_FACET
        params["split"] = split
    # `date_trunc` takes the grain name directly, so `Grain`'s own values ARE
    # the SQL argument — bound as a parameter, never interpolated. Passed
    # straight through rather than through a lookup table: a table here could
    # only restate the enum, and a transposed entry (`WEEK: "month"`) would
    # misbucket every week, quarter and year chart. `Grain` is the single
    # source of that string, and `test_every_grain_buckets_by_its_own_calendar_unit`
    # proves Postgres accepts all four.
    params["grain"] = grain.value
    params["summable"] = sorted(kind.value for kind in SUMMABLE_AMOUNT_KINDS)
    params["since"] = since
    params["until"] = until
    # The rule fragment is a conjunction of parenthesised atoms; the outer
    # parentheses at the splice site keep that from being load-bearing.
    narrowing = (
        _CELL_NARROWING.format(period_expr=_PERIOD_EXPR, split_expr=split_expr) if cell else ""
    )
    statement = _ROWS_SQL.format(
        period_expr=_PERIOD_EXPR,
        split_expr=split_expr,
        rule=fragment,
        narrowing=narrowing,
    )
    return text(statement), params


async def _converted(session: AsyncSession, row: Row[Any], currency: str) -> Decimal | None:
    """One row's signed contribution in `currency`, or None if it has no rate.

    ONE definition, read by `chart_series` and by `chart_cell`. Both the sign
    and the conversion date are decisions the two functions must make
    identically or the panel stops matching the bar, and neither is visible in
    a result that looks plausible: converting at the *period's* date instead of
    the row's (§9.3) is a wrong number, not an error, and the fixtures that
    would catch it are exactly the ones a single-currency corpus does not have
    — `convert_amount` short-circuits when the currencies match, so the date
    argument is never even read there. Written twice, this was green under
    mutation; written once, there is nothing to diverge.

    `row.date` is the **document's** own date, never the bucket's.
    """
    converted = await convert_amount(session, row.amount, row.currency, currency, row.date)
    if converted is None:
        return None
    return AMOUNT_SIGN[AmountKind(row.amount_kind)] * converted


class Cell(BaseModel):
    """One (time bucket, split bucket) point. `split_value` is None both when
    there is no split axis and when the row carries no value for it."""

    period: date
    split_value: str | None
    total: Decimal
    payments: int


class Unconvertible(BaseModel):
    """Money the chart could not express in its display currency (§9.3).

    `currency` is None when the amount carries **no currency at all**.
    `documents.currency` is nullable and the `amount_currency_coupling` check
    is a warning rather than a block, so an amount-bearing, dated, summable
    document with no currency is a permitted live state that reaches
    `spend_facts` untouched. It has no usable rate, which is exactly what this
    class means, so it is reported here rather than excluded — never dropped
    and never counted 1:1.

    `amount` is in `currency` and is *signed*: it is the net that is missing
    from the total, so a refund lowers it exactly as it would have lowered the
    total.

    `documents` counts the **canonical rows** behind that net — which is not
    what `Series.documents` counts (payment-group members, so 2 for a merged
    pair where this reports 1). The two answer different questions and are
    deliberately not aligned. A consumer must show this count *beside* the
    amount and never infer "nothing is missing" from the amount alone: an
    unconvertible payment and an equal unconvertible refund net to
    `amount == 0.00, documents == 2`, and two documents are still unrepresented.
    """

    currency: str | None
    amount: Decimal
    documents: int


class Series(BaseModel):
    """A chart's answer. `total` is the sum of `cells`, so the headline and the
    drawing can never disagree (spec §2.5).

    `payments` counts the distinct payments that reached the total; `documents`
    counts the documents behind those payments, which is the larger number
    ("15 payments from 18 documents", §9.4). It is deliberately not the count of
    the rows summed: the query reads only canonical rows, so a merged pair
    contributes one row and counting rows would report 1 for the 2 documents the
    owner can see.
    """

    cells: list[Cell]
    total: Decimal
    payments: int
    documents: int
    unconvertible: list[Unconvertible]


async def chart_series(
    session: AsyncSession,
    rule: Rule,
    *,
    grain: Grain,
    split: str | None,
    currency: str,
    since: date | None,
    until: date | None,
) -> Series:
    """Answer one chart: rows matching `rule`, bucketed by time and by `split`.

    `split` is a facet key, `"sender"`, or None. It never filters — an
    unlabelled row lands in the None bucket — so the total is the same under
    every axis (§9.2).
    """
    statement, params = _rows_query(rule, grain=grain, split=split, since=since, until=until)
    rows = (await session.execute(statement, params)).all()

    totals: dict[tuple[date, str | None], Decimal] = {}
    cell_payments: dict[tuple[date, str | None], set[int]] = defaultdict(set)
    counted_payments: set[int] = set()
    # Keyed by `str | None`: a row with no currency at all belongs here too.
    missing_amounts: dict[str | None, Decimal] = {}
    missing_documents: dict[str | None, set[int]] = defaultdict(set)

    for row in rows:
        contribution = await _converted(session, row, currency)
        if contribution is None:
            # Reported in the row's OWN currency, so the sign is re-applied to
            # the unconverted amount rather than taken from `_converted`, which
            # by definition has no value to give here.
            sign = AMOUNT_SIGN[AmountKind(row.amount_kind)]
            missing_amounts[row.currency] = (
                missing_amounts.get(row.currency, Decimal(0)) + sign * row.amount
            )
            missing_documents[row.currency].add(row.document_id)
            continue
        key = (row.period, row.split_value)
        totals[key] = totals.get(key, Decimal(0)) + contribution
        cell_payments[key].add(row.payment_id)
        counted_payments.add(row.payment_id)

    # None last inside a period: "unlabelled" reads as the tail of a legend.
    cells = [
        Cell(
            period=period,
            split_value=value,
            total=totals[(period, value)],
            payments=len(cell_payments[(period, value)]),
        )
        for period, value in sorted(totals, key=lambda k: (k[0], k[1] is None, k[1] or ""))
    ]
    return Series(
        cells=cells,
        total=sum((cell.total for cell in cells), Decimal(0)),
        payments=len(counted_payments),
        documents=await _document_count(session, counted_payments),
        unconvertible=[
            Unconvertible(currency=code, amount=amount, documents=len(missing_documents[code]))
            # None last, as in the cells: a currency code cannot be compared
            # with None, so a bare `sorted` raises as soon as an amount with
            # no currency meets one with an unknown code.
            for code, amount in sorted(
                missing_amounts.items(), key=lambda item: (item[0] is None, item[0] or "")
            )
        ],
    )


async def _document_count(session: AsyncSession, payment_ids: set[int]) -> int:
    """How many live documents belong to these payments (§9.4)."""
    if not payment_ids:
        return 0
    result = await session.execute(text(_DOCUMENTS_SQL), {"payment_ids": sorted(payment_ids)})
    return int(result.scalar_one())


class CellDocument(BaseModel):
    """One document behind a payment in a drilled-into cell (§9.5).

    Listed whether or not it is canonical. Only the canonical row carried the
    money into the chart, so `CellPayment.total` comes from that row alone —
    but the panel is where a wrong merge is noticed and split, and a list that
    showed only the canonical half would hide the only evidence the merge was
    wrong.

    `amount` and `currency` are nullable because the columns are. A manual
    MERGE override joins two live documents without the rule arms'
    `amount_total IS NOT NULL` precondition, and `documents.currency` is
    nullable with `amount_currency_coupling` a warning rather than a block
    (the same permitted live state `Unconvertible.currency=None` reports).
    Declaring either non-optional would turn drilling into such a cell into a
    validation error — a 500 on the panel rather than a wrong number.
    """

    id: int
    title: str | None
    date: date | None
    amount: Decimal | None
    currency: str | None
    amount_kind: str | None
    reference: str | None
    is_canonical: bool


class CellPayment(BaseModel):
    """One payment inside a drilled-into cell.

    `total` is that payment's contribution to the cell — the signed, converted
    sum of its *canonical* rows inside this time bucket and this split bucket,
    which is exactly what `chart_series` added to the cell. Summing
    `documents` instead would double a merged pair and stop matching the bar.

    `documents` lists every live document in the payment group, canonical or
    not, so the group can be inspected and corrected here.
    """

    payment_id: int
    total: Decimal
    documents: list[CellDocument]


async def chart_cell(
    session: AsyncSession,
    rule: Rule,
    *,
    grain: Grain,
    split: str | None,
    split_value: str | None,
    period: date,
    currency: str,
    since: date | None,
    until: date | None,
) -> list[CellPayment]:
    """The payments behind one cell of `chart_series`, each with its documents.

    Every argument `chart_series` took is taken again and passed to the same
    `_rows_query`, plus the cell's own `period` and `split_value`; the rows are
    then converted exactly as `chart_series` converts them — at each document's
    own date (§9.3) — so `sum(p.total)` equals the cell's `total`.

    A row `library.fx` cannot convert is skipped, precisely as `chart_series`
    skips it into `Series.unconvertible`: it never entered the cell's total, so
    counting it here would make the panel disagree with the bar, and counting
    it 1:1 is the silent failure §9.3 exists to forbid. A payment *all* of
    whose rows are unconvertible therefore does not appear at all — again
    matching the chart, which did not count it either. Its documents are not
    hidden: the footer (`charts/footer.py`) accounts for them, and where such a
    document shares a payment group with a convertible one it is still listed
    under that payment.

    Payments are ordered by contribution, largest first, with the payment id
    breaking ties, so the panel's order is stable across calls.
    """
    statement, params = _rows_query(
        rule,
        grain=grain,
        split=split,
        since=since,
        until=until,
        cell=True,
    )
    params["period"] = period
    params["split_value"] = split_value
    rows = (await session.execute(statement, params)).all()

    totals: dict[int, Decimal] = {}
    for row in rows:
        contribution = await _converted(session, row, currency)
        if contribution is None:
            continue
        totals[row.payment_id] = totals.get(row.payment_id, Decimal(0)) + contribution
    if not totals:
        return []

    documents: dict[int, list[CellDocument]] = defaultdict(list)
    document_rows = await session.execute(
        text(_CELL_DOCUMENTS_SQL), {"payment_ids": sorted(totals)}
    )
    for row in document_rows:
        documents[row.payment_id].append(
            CellDocument(
                id=row.id,
                title=row.title,
                date=row.document_date,
                amount=row.amount_total,
                currency=row.currency,
                amount_kind=row.amount_kind,
                reference=row.reference,
                is_canonical=row.is_canonical,
            )
        )
    return [
        CellPayment(
            payment_id=payment_id, total=totals[payment_id], documents=documents[payment_id]
        )
        for payment_id in sorted(totals, key=lambda pid: (-totals[pid], pid))
    ]
