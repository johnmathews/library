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

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
_ROWS_SQL = """
SELECT CAST(date_trunc(:grain, CAST(sf.date AS timestamp)) AS date) AS period,
       {split_value}                                                AS split_value,
       sf.amount, sf.currency, sf.date, sf.amount_kind,
       sf.payment_id, sf.document_id
FROM spend_facts sf
WHERE sf.is_canonical
  AND sf.amount_kind = ANY(:summable)
  AND sf.date IS NOT NULL
  AND (CAST(:since AS date) IS NULL OR sf.date >= CAST(:since AS date))
  AND (CAST(:until AS date) IS NULL OR sf.date <= CAST(:until AS date))
  AND ({rule})
"""

# `payments` holds one row per *live* document (its reachability is built from
# `documents WHERE deleted_at IS NULL`), so counting its rows for a set of
# payment ids counts the documents behind those payments and a soft-deleted
# twin is already absent.
_DOCUMENTS_SQL = "SELECT count(*) FROM payments WHERE payment_id = ANY(:payment_ids)"


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
    fragment, params = rule_predicate(rule)
    if split is None:
        split_value = _SPLIT_NONE
    elif split == SENDER_SPLIT:
        split_value = _SPLIT_SENDER
    else:
        split_value = _SPLIT_FACET
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
    statement = text(_ROWS_SQL.format(split_value=split_value, rule=fragment))
    rows = (await session.execute(statement, params)).all()

    totals: dict[tuple[date, str | None], Decimal] = {}
    cell_payments: dict[tuple[date, str | None], set[int]] = defaultdict(set)
    counted_payments: set[int] = set()
    # Keyed by `str | None`: a row with no currency at all belongs here too.
    missing_amounts: dict[str | None, Decimal] = {}
    missing_documents: dict[str | None, set[int]] = defaultdict(set)

    for row in rows:
        sign = AMOUNT_SIGN[AmountKind(row.amount_kind)]
        converted = await convert_amount(session, row.amount, row.currency, currency, row.date)
        if converted is None:
            missing_amounts[row.currency] = (
                missing_amounts.get(row.currency, Decimal(0)) + sign * row.amount
            )
            missing_documents[row.currency].add(row.document_id)
            continue
        key = (row.period, row.split_value)
        totals[key] = totals.get(key, Decimal(0)) + sign * converted
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
