"""The accounting for everything a chart's rule touched but its total did not.

Spec §9.4, and the most important module in the feature: *a document the model
failed to label matches no rule, so without this it disappears from every chart
with no way to notice.* Reporting that money inside the chart whose date and
currency window contains it turns the archive's worst failure mode into a
visible task.

**Separate from `query.py` on purpose.** It answers the opposite question — what
the total *missed* — and mixing the two is how "nothing is excluded silently"
quietly stops being true: a refactor of the sum has no reason to keep the
accounting correct, and no test notices.

**The categories are a partition, and that is the whole design.** One SQL
statement classifies every row the chart touched into exactly one bucket, so a
row cannot be counted twice and — the failure this module exists to prevent —
cannot fall between two `WHERE` clauses that were each written correctly. The
buckets:

| bucket | what it is | reported as |
| --- | --- | --- |
| `counted` | in the total already | — (`query.py`'s job) |
| `netted_refund` | in the total, and lowering it (§9.4) | `netted_refunds` |
| `excluded` | a kind that never enters a total | `excluded` |
| `unclassified` | `amount_kind IS NULL` — *not yet decided* (§8.1.1) | `unclassified` |
| `undated` | summable, but no date to bucket it by | `undated` |
| `uncategorised` | summable, unlabelled for a facet the rule names | `uncategorised` |
| `outside` | dated outside the chart's window | — not this chart's claim |
| `unaccounted` | the `ELSE`: a shape nobody predicted | `unaccounted` |

A row labelled for a *different* value never reaches the `CASE` at all — the
outer `WHERE` admits only rows the rule matches or that are missing one of its
labels — so it is a different chart's business, not an unaccounted one.

`unaccounted` is reported rather than filtered out, and that is the difference
between a safety net and a decoration. It is unreachable today (the outer
`WHERE` guarantees that arm 5's rule or arm 6's unlabelled test fires, and a
NULL rule with a FALSE unlabelled test keeps the row out of `classified`
entirely), which is exactly why no test would catch it being dropped — so the
`ELSE` must surface, in its own group, under its own name. Calling it
`uncategorised` would misdescribe the one row that gets there: by definition
nobody predicted it.

`unclassified` is the category the brief did not have. A document with an amount
and no `amount_kind` is summed by nothing, and `excluded` filters `NOT NULL`
while the other three require a summable kind — so before this bucket existed it
appeared *nowhere*, which is precisely what §9.4 forbids, on the one class of
document the live archive has most of.

**Touched, not merely matching.** The rows considered are those the rule matches
*or* that are missing a label for a facet the rule names. Restricting to matches
alone would hide every unlabelled row behind the very label it is missing, which
is the gap; widening to the whole archive would make every chart report money
belonging to a different question (see `uncategorised` in §9.4 and the test for
a labelled document outside the rule).

**Signs.** A summable amount is accounted signed, through `AMOUNT_SIGN`, so a
refund lowers what it is part of exactly as it lowers the total. A kind that
never enters a total has no sign, so `excluded` and `unclassified` report
magnitudes. `netted_refunds` is a positive magnitude with its count, because
§9.4 renders it in the header block beside the total it is already inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.charts.query import Unconvertible
from library.charts.rule import Rule, rule_predicate
from library.fx import convert_amount
from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS, AmountKind

#: Rendered in place of the SQL NULL for an undecided `amount_kind`. A literal
#: `null` in a footer reads as a bug in the footer rather than as a document
#: waiting to be classified.
UNCLASSIFIED = "unclassified"

#: `ExcludedGroup.amount_kind` for the groups that are not about one kind: the
#: field names the group's reason, which is what the renderer prints.
UNDATED = "undated"
UNCATEGORISED = "uncategorised"
UNACCOUNTED = "unaccounted"

# One statement, one CASE, one bucket per row.
#
# `CAST(x AS type)`, never the `::type` shorthand: `text()` parses `:name`
# itself, so `:since::date` leaves the parameter unbound.
#
# Branch order is load-bearing:
#
# * `outside` first, but only for rows that *have* a date. An undated row can
#   sit in no window at all, so no window may drop it — that is the one bound
#   this module ignores, and it looks like a bug to anyone reading quickly.
# * kind before rule, so a coverage limit is reported as excluded rather than
#   competing with the label branches.
# * rule before `uncategorised`, so an unlabelled row that a `not_in` rule
#   *already counted* (see `rule.py`: NULL satisfies the negation) is not also
#   reported as a gap the chart did not have.
#
# `is_canonical` matches `query.py`: a merged pair contributes one row, so the
# footer never reports the same money twice under two documents.
_CLASSIFY_SQL = """
WITH classified AS (
  SELECT sf.document_id, sf.amount, sf.currency, sf.date, sf.amount_kind,
         CASE
           WHEN sf.date IS NOT NULL AND NOT (
                    (CAST(:since AS date) IS NULL OR sf.date >= CAST(:since AS date))
                AND (CAST(:until AS date) IS NULL OR sf.date <= CAST(:until AS date))
                ) THEN 'outside'
           WHEN sf.amount_kind IS NULL THEN 'unclassified'
           WHEN NOT (sf.amount_kind = ANY(:summable)) THEN 'excluded'
           WHEN sf.date IS NULL THEN 'undated'
           WHEN ({rule}) THEN
                CASE WHEN sf.amount_kind = :refund THEN 'netted_refund' ELSE 'counted' END
           WHEN {unlabelled} THEN 'uncategorised'
           ELSE 'unaccounted'
         END AS bucket
  FROM spend_facts sf
  WHERE sf.is_canonical AND (({rule}) OR {unlabelled})
)
SELECT document_id, amount, currency, date, amount_kind, bucket
FROM classified
-- Only the two buckets the chart legitimately does not owe an account for are
-- dropped here. Every other bucket — including one this module does not know
-- the name of — reaches Python and is reported.
WHERE bucket NOT IN ('counted', 'outside')
"""

#: True for a row missing a label for any facet the rule names. `?&` asks
#: whether jsonb holds *all* of a `text[]`; the explicit CAST is what tells
#: Postgres the bind is that array rather than leaving it to inference.
_UNLABELLED = "NOT (sf.labels ?& CAST(:facets AS text[]))"

#: A rule naming no facet cannot have a label gap, so nothing is unlabelled
#: with respect to it — and `?&` against an empty array is true for every row,
#: which would make the fragment above false anyway. Written out so the empty
#: case never depends on that.
_NEVER_UNLABELLED = "FALSE"


class ExcludedGroup(BaseModel):
    """Money the total did not count, and why.

    `amount_kind` is the reason, not always a kind: `"unclassified"`,
    `"undated"` and `"uncategorised"` name their group. `documents` counts the
    canonical documents behind `amount` and must be shown beside it — a payment
    and an equal refund net to `amount == 0.00, documents == 2`, which reads as
    "nothing missing" while two documents are unrepresented.
    """

    amount_kind: str
    amount: Decimal
    documents: int


class Footer(BaseModel):
    """What the chart's total did not count (§9.4).

    `netted_refunds` is *in* the total and is reported here only so the header
    can say so; the groups below are not, and together with the total they
    account for every row the rule touched — `unaccounted` last, so that the
    accounting stays complete even for a shape this module does not know.
    """

    netted_refunds: Decimal
    refund_count: int
    excluded: list[ExcludedGroup]
    unclassified: ExcludedGroup | None
    uncategorised: ExcludedGroup | None
    undated: ExcludedGroup | None
    #: Money that reached the `CASE`'s `ELSE`. Always `None` today; if it is
    #: ever not, the classification has a hole and this is the money in it.
    unaccounted: ExcludedGroup | None
    unconvertible: list[Unconvertible]


@dataclass
class _Group:
    """A running amount and the documents behind it."""

    amount: Decimal = Decimal(0)
    documents: set[int] = field(default_factory=set)

    def add(self, amount: Decimal, document_id: int) -> None:
        self.amount += amount
        self.documents.add(document_id)

    def rendered(self, amount_kind: str) -> ExcludedGroup | None:
        """`None` when nothing landed here — an empty group is not a report."""
        if not self.documents:
            return None
        return ExcludedGroup(
            amount_kind=amount_kind, amount=self.amount, documents=len(self.documents)
        )


#: `_accounted_rows`'s bucket for a refund that matched the rule: it is
#: already inside the total (`query.py` counts it, signed, there), and this
#: module reports it again only as a lens on that total — "how much was
#: netted off" — which is why it is the one bucket whose `_AccountedRow.amount`
#: is not `sign * converted`.
_NETTED_REFUND = "netted_refund"

#: Every bucket `_CLASSIFY_SQL` can name that this module actually recognises.
#: `_resolved_bucket` maps anything else — including a name this module does
#: not know, which `_CLASSIFY_SQL`'s own comment says can reach Python — to
#: `UNACCOUNTED`, the same way `chart_footer`'s dispatch always has. One
#: function shared by `chart_footer` and `chart_footer_documents`: without it,
#: an unforeseen bucket name would make the footer report `unaccounted` money
#: while the drill route returned nothing for it — the one bucket whose whole
#: reason to be drillable is the shape nobody predicted, going silent in
#: exactly that shape.
_KNOWN_BUCKETS = frozenset({_NETTED_REFUND, "excluded", UNCLASSIFIED, UNDATED, UNCATEGORISED})


def _resolved_bucket(row_bucket: str) -> str:
    """The bucket a row is reported under: `row_bucket` itself if this module
    recognises it, `UNACCOUNTED` otherwise."""
    return row_bucket if row_bucket in _KNOWN_BUCKETS else UNACCOUNTED


@dataclass(frozen=True, slots=True)
class _AccountedRow:
    """One classified row, converted to the chart's currency.

    `amount` is `sign * converted` for every bucket except `_NETTED_REFUND`:
    a refund lowers what it is part of, and a kind that never enters a total
    contributes its magnitude — the same treatment the total gives it.
    `_NETTED_REFUND` is the one exception, carrying the plain converted
    magnitude rather than the signed value, because it is not being counted
    here at all (`query.py` already counted it, signed); `Footer.netted_refunds`
    is a positive magnitude rendered beside the total it is already inside, and
    negating it a second time would say the opposite of what happened.

    `currency` and `date` are the **document's own**, carried through for
    display rather than converted — `amount` is the only field in the chart's
    currency.
    """

    document_id: int
    bucket: str
    amount_kind: str | None
    amount: Decimal
    currency: str | None
    date: date | None


async def _accounted_rows(
    session: AsyncSession,
    rule: Rule,
    *,
    currency: str,
    since: date | None,
    until: date | None,
    facets_in_rule: set[str],
) -> tuple[list[_AccountedRow], dict[str | None, _Group]]:
    """Every row the rule touched, classified and converted, plus the ones no
    rate could convert.

    The one execution of `_CLASSIFY_SQL` **and** the one place a row is
    converted (see `_AccountedRow` for the one bucket where "signed" does not
    apply). `chart_footer` aggregates the first return value and
    `chart_footer_documents` filters it, so the count a footer reports and the
    list a panel opens cannot disagree — not because a test compares them, but
    because there is only one of them.

    A row whose amount no rate can convert is in the second return value and
    in neither bucket, exactly as the footer reports it: `unconvertible` is
    not a bucket of the `CASE` but a separate account (docs/charts.md §5). A
    `_NETTED_REFUND` row that cannot convert is dropped entirely — it was not
    in the total either, so it was not netted off anything, and `query.py` has
    already reported it; joining `missing` here would double the merged
    unconvertible line (Task 10).
    """
    fragment, params = rule_predicate(rule)
    unlabelled = _UNLABELLED if facets_in_rule else _NEVER_UNLABELLED
    params["facets"] = sorted(facets_in_rule)
    params["summable"] = sorted(kind.value for kind in SUMMABLE_AMOUNT_KINDS)
    params["refund"] = AmountKind.REFUND.value
    params["since"] = since
    params["until"] = until
    statement = text(_CLASSIFY_SQL.format(rule=fragment, unlabelled=unlabelled))
    rows = (await session.execute(statement, params)).all()

    accounted: list[_AccountedRow] = []
    # Keyed by `str | None`: a row with no currency at all belongs here too.
    missing: dict[str | None, _Group] = {}

    for row in rows:
        converted = await convert_amount(session, row.amount, row.currency, currency, row.date)
        if row.bucket == _NETTED_REFUND:
            if converted is not None:
                accounted.append(
                    _AccountedRow(
                        document_id=row.document_id,
                        bucket=row.bucket,
                        amount_kind=row.amount_kind,
                        amount=converted,
                        currency=row.currency,
                        date=row.date,
                    )
                )
            continue
        # A summable amount enters signed, so a refund lowers what it is part
        # of; a kind that never enters a total has no sign and is accounted as
        # the magnitude it is.
        kind = AmountKind(row.amount_kind) if row.amount_kind is not None else None
        sign = 1 if kind is None else AMOUNT_SIGN.get(kind, 1)
        if converted is None:
            # Reported, never dropped and never counted 1:1 (§9.3). The sign
            # convention is the one the group would have used, so the merge in
            # Task 10 stays coherent.
            missing.setdefault(row.currency, _Group()).add(sign * row.amount, row.document_id)
            continue
        accounted.append(
            _AccountedRow(
                document_id=row.document_id,
                bucket=row.bucket,
                amount_kind=row.amount_kind,
                amount=sign * converted,
                currency=row.currency,
                date=row.date,
            )
        )
    return accounted, missing


class FooterDocument(BaseModel):
    """One document behind a footer bucket.

    `amount` is the **sum of this document's rows in this bucket**, each
    already converted (and, per `_AccountedRow`, signed for every bucket this
    route can open) — not one row's raw value. A document split across spend
    lines emits one row per line, and a `100.00` document split `60.00`/`40.00`
    with neither line labelled emits two, proved against Postgres before this
    was written. Rendering a single row's amount would print a number the
    footer never reports.

    `currency` and `date` are the **document's own**, carried through for
    display; `amount` is in the chart's currency. A row showing `amount` in
    EUR beside `currency: "GBP"` is not a bug — it is the document's own
    currency next to the chart's converted figure.
    """

    document_id: int
    amount: Decimal
    currency: str | None
    date: date | None
    amount_kind: str | None


async def chart_footer_documents(
    session: AsyncSession,
    rule: Rule,
    *,
    bucket: str,
    amount_kind: str | None,
    currency: str,
    since: date | None,
    until: date | None,
    facets_in_rule: set[str],
) -> list[FooterDocument]:
    """The documents behind one footer bucket, deduplicated by document.

    `len(...)` equals the bucket's reported `documents` and the amounts sum to
    its reported `amount`, because both come from the same `_accounted_rows`
    the footer aggregated — unconvertible rows included in neither.

    `amount_kind` selects one group out of `excluded`, which is a list of
    groups rather than a single figure; it is ignored for every other bucket,
    which has exactly one group.

    Ordering is by descending absolute amount then document id — the largest
    contributor first, and stable across calls.
    """
    rows, _missing = await _accounted_rows(
        session,
        rule,
        currency=currency,
        since=since,
        until=until,
        facets_in_rule=facets_in_rule,
    )
    selected = (
        row
        for row in rows
        if _resolved_bucket(row.bucket) == bucket
        and (bucket != "excluded" or row.amount_kind == amount_kind)
    )
    merged: dict[int, FooterDocument] = {}
    for row in selected:
        existing = merged.get(row.document_id)
        if existing is None:
            merged[row.document_id] = FooterDocument(
                document_id=row.document_id,
                amount=row.amount,
                currency=row.currency,
                date=row.date,
                amount_kind=row.amount_kind,
            )
        else:
            merged[row.document_id] = existing.model_copy(
                update={"amount": existing.amount + row.amount}
            )
    return sorted(merged.values(), key=lambda doc: (-abs(doc.amount), doc.document_id))


async def chart_footer(
    session: AsyncSession,
    rule: Rule,
    *,
    currency: str,
    since: date | None,
    until: date | None,
    facets_in_rule: set[str],
) -> Footer:
    """Account for everything `rule` touched that the total did not count.

    `facets_in_rule` is given rather than derived (the caller knows which
    clauses it kept); an empty set means the rule asks about everything, and a
    rule that asks about everything cannot have a label gap, so `uncategorised`
    is `None`.

    Every amount converts at its own document's date, exactly as the total does
    (§9.3). An amount with no usable rate — including one carrying no currency
    at all — joins `unconvertible` rather than being counted at 1:1. Rows that
    *would have* entered the total are left to `query.py`, which already
    reports them: Task 10 merges the two lists by currency, and reporting a row
    in both would double it.

    The dispatch below is the only thing this function still does itself:
    `_accounted_rows` does the fetch, the conversion and the signing, shared
    with `chart_footer_documents`, so the two cannot disagree about what a row
    is worth.
    """
    rows, missing = await _accounted_rows(
        session,
        rule,
        currency=currency,
        since=since,
        until=until,
        facets_in_rule=facets_in_rule,
    )

    excluded: dict[str, _Group] = {}
    unclassified, uncategorised, undated = _Group(), _Group(), _Group()
    unaccounted = _Group()
    refunds = _Group()

    for row in rows:
        resolved = _resolved_bucket(row.bucket)
        if resolved == _NETTED_REFUND:
            refunds.add(row.amount, row.document_id)
        elif resolved == "excluded":
            if row.amount_kind is None:
                # `_CLASSIFY_SQL` routes a NULL `amount_kind` to `unclassified`
                # before it ever reaches the `excluded` arm, so this never
                # actually fires today. Routed to `unaccounted` rather than
                # asserted, so an unforeseen future shape is reported instead
                # of 500ing the whole chart — the same policy `_resolved_bucket`
                # already applies to a bucket name this module does not know.
                unaccounted.add(row.amount, row.document_id)
            else:
                excluded.setdefault(row.amount_kind, _Group()).add(row.amount, row.document_id)
        elif resolved == UNCLASSIFIED:
            unclassified.add(row.amount, row.document_id)
        elif resolved == UNDATED:
            undated.add(row.amount, row.document_id)
        elif resolved == UNCATEGORISED:
            uncategorised.add(row.amount, row.document_id)
        else:
            # `_resolved_bucket` returns `UNACCOUNTED` for anything none of the
            # arms above claimed, so this is reached only by that bucket —
            # reported rather than dropped, the whole point of it existing.
            unaccounted.add(row.amount, row.document_id)

    return Footer(
        netted_refunds=refunds.amount,
        refund_count=len(refunds.documents),
        # Sorted by kind: a footer whose lines reorder between two identical
        # requests reads as the archive having changed.
        excluded=[
            ExcludedGroup(amount_kind=kind, amount=group.amount, documents=len(group.documents))
            for kind, group in sorted(excluded.items())
        ],
        unclassified=unclassified.rendered(UNCLASSIFIED),
        uncategorised=uncategorised.rendered(UNCATEGORISED),
        undated=undated.rendered(UNDATED),
        unaccounted=unaccounted.rendered(UNACCOUNTED),
        unconvertible=[
            Unconvertible(currency=code, amount=group.amount, documents=len(group.documents))
            # None last: a currency code cannot be compared with None, so a
            # bare `sorted` raises as soon as an amount with no currency meets
            # one with an unknown code (`query.py` sorts the same way).
            for code, group in sorted(
                missing.items(), key=lambda item: (item[0] is None, item[0] or "")
            )
        ],
    )
