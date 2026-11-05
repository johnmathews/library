"""Structured / analytical queries over extracted document metadata.

The semantic retriever (``library.search.semantic_search``) answers
content questions; this module answers *aggregation* questions —
"who was my energy provider last year?", "how much did I spend on
utilities in 2025?" — by querying the structured columns the extractor
populates (``sender``, ``kind``, ``document_date``, ``amount_total``)
rather than document text.

Every result carries the contributing document ids (capped) so the caller
can cite sources. Filters reuse ``library.search.DocumentFilters`` so the
two retrieval paths share one filter vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal, TypedDict

from sqlalchemy import (
    CTE,
    ColumnElement,
    Integer,
    and_,
    case,
    cast,
    distinct,
    func,
    null,
    select,
    true,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from library.models import (
    AMOUNT_SIGN,
    SUMMABLE_AMOUNT_KINDS,
    AmountKind,
    Document,
    Kind,
    ReviewStatus,
    Sender,
    spend_facts,
)
from library.search import DocumentFilters, filter_conditions

# How many contributing document ids to attach to each aggregated row.
MAX_CITED_IDS: int = 25

#: The most rows ``distinct_senders`` and ``sum_amount`` return: one row per
#: sender (per currency) would otherwise grow with the archive, each citing up
#: to ``MAX_CITED_IDS`` ids, into a tool result no model context holds. The
#: largest groups are kept, and the documents of the rest are reported as
#: ``over_limit``, as ``list_documents`` reports its own cut. Read at call
#: time, so a test can lower it.
MAX_GROUP_ROWS: int = 50

# Maps everyday concepts an LLM might use to the fixed kind slugs (seeded in
# migration 0001). Surfaced in the tool description so the model can translate
# "energy"/"electricity" → kind="utility-bill"; not used for routing here.
CONCEPT_TO_KIND: dict[str, str] = {
    "energy": "utility-bill",
    "electricity": "utility-bill",
    "gas": "utility-bill",
    "water": "utility-bill",
    "utility": "utility-bill",
    "bill": "utility-bill",
    "invoice": "invoice",
    "receipt": "receipt",
    "insurance": "certificate",
    "warranty": "warranty",
    "contract": "contract",
    "parking": "parking-ticket",
    "quote": "quote",
    "estimate": "quote",
}

Aggregate = Literal["list", "distinct_senders", "sum_amount"]
GroupBy = Literal["sender", "kind"]

# How to read a result, worded for a model. Ask's `query_documents` tool and
# the MCP `money_query` tool both quote these, so the two LLM surfaces over
# this module explain sign, dedup and coverage in the same words; a change
# here changes both.

#: What ``sum_amount`` totals: signed by ``amount_kind``, real expenditure
#: only, one document per payment, and where the rest is reported.
SUM_AMOUNT_GUIDE: str = (
    "sum_amount: total real expenditure. It reads what each "
    "amount MEANS, so a refund reduces the total, a policy's "
    "cover limit or an account balance is left out, and one "
    "payment documented as both an invoice and a receipt is "
    "counted once, on the date of the document that carries it (the "
    "receipt when both match your other filters) — each reported in "
    "`coverage.excluded` as not_summable_kind, duplicate_payment "
    "(payment_dated_elsewhere when the carrying document is dated outside "
    "your date window) or quote_not_spend. Pass kind='quote' to total "
    "quotes instead."
)

#: How to read the ``coverage`` block every result carries (``Coverage``).
COVERAGE_GUIDE: str = (
    "Every result carries a `coverage` block — `matched` documents met "
    "your filters, `included` are the ones the rows account for, "
    "`excluded` maps a reason to how many were dropped for it, and "
    "`needs_review` counts included documents whose extracted metadata "
    "the archive flagged as untrustworthy. Read it before you answer: a "
    "total over `included` documents is not a total over `matched` ones. "
    "`matched`/`included`/`excluded` always count DOCUMENTS, even for "
    "distinct_senders — a coverage of 1 excluded out of 12 with 3 sender "
    "rows means 1 of 12 documents was dropped, not 1 of 3 senders. "
    f"distinct_senders and sum_amount return at most {MAX_GROUP_ROWS} rows, "
    "the largest groups; `over_limit` counts the documents in the rows that "
    "were cut (narrow the filters, or drop group_by for the whole total). "
    "Totals over adjacent date windows with the same other filters add up; "
    "totals under different other filters (kind='utility-bill' and "
    "kind='receipt', say) can count the same payment, so never add them "
    "together."
)


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A document summary row for ``list`` results and citations."""

    id: int
    title: str | None
    sender: str | None
    recipient: str | None
    kind: str | None
    document_date: date | None
    amount_total: str | None
    currency: str | None
    review_status: str  # "verified" | "needs_review" | "unreviewed"


@dataclass(frozen=True, slots=True)
class SenderGroup:
    """A distinct sender and how many matching documents it has."""

    sender: str
    document_count: int
    document_ids: list[int]


@dataclass(frozen=True, slots=True)
class AmountGroup:
    """A summed amount for one (group key, currency) bucket."""

    key: str | None  # sender name / kind slug / None when ungrouped
    total: str
    currency: str | None
    document_count: int
    document_ids: list[int]


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the filtered set a result's rows actually account for.

    Every aggregate silently drops documents — a spend total cannot include a
    bill whose amount never extracted, a sender breakdown cannot include a
    document with no sender, a list cannot exceed its limit. Reporting the
    result without reporting the drop is how a partial number gets presented as
    a complete one, so every aggregate returns this alongside its rows.

    ``matched`` is what met the caller's filters, ``included`` is what the rows
    account for, and ``excluded`` says why the difference was dropped —
    ``included + sum(excluded.values()) == matched`` is an invariant, pinned by
    a test. Reasons that dropped nothing are omitted, so an empty ``excluded``
    reads as "the rows are the whole story".

    ``needs_review`` is a *trust* signal, not a coverage one: those documents are
    counted in ``included``. It is the number of them whose extracted metadata
    ``library.extraction.validation`` flagged as untrustworthy — most often an
    ``amount_grounding`` finding, meaning the amount being summed here does not
    appear anywhere in the document's text.
    """

    matched: int
    included: int
    excluded: dict[str, int]
    needs_review: int


@dataclass(frozen=True, slots=True)
class Aggregated[T]:
    """An aggregate's rows plus the coverage of the set they were drawn from."""

    rows: list[T]
    coverage: Coverage


async def count_coverage(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    include_condition: ColumnElement[bool],
    exclusions: dict[str, ColumnElement[bool]],
) -> Coverage:
    """Count a result's coverage in one round-trip, using conditional aggregates.

    ``include_condition`` selects the documents the caller's rows are built
    from; ``exclusions`` maps a reason name to the condition identifying the
    documents dropped for it. The conditions must partition the matched set —
    the caller owns that, and the invariant is asserted by the caller's tests
    rather than here, so a legitimate partial count (``list_documents``, whose
    over-limit drop is positional and has no SQL predicate) is still expressible.

    One ``SELECT`` with Postgres ``FILTER (WHERE ...)`` clauses rather than N+1
    counts: this runs on every structured tool call, so it must not multiply
    the query cost of asking a question.

    ``filters.review_status`` gets special treatment: if it were left in the
    conditions below, ``matched`` would already have every document of the
    "wrong" trust state removed before the include/exclude gates ever ran —
    so ``review_status="verified"`` could return ``{matched: 14, included: 14,
    excluded: {}, needs_review: 0}``, the exact signature of "complete,
    nothing flagged", while every flagged document was silently dropped from
    the denominator. ``matched`` is therefore always computed against the
    filters MINUS ``review_status`` (everything that met the *other*
    filters), and the trust filter itself becomes the first exclusion reason,
    ``filtered_review_status``. Because it is orthogonal to amount/quote/
    sender/kind, it composes as the first gate in the include-chain: the
    caller's own ``include_condition``/``exclusions`` are additionally gated
    on "review_status matches", so a document that fails the trust filter is
    counted there and nowhere else — the partition invariant survives.
    """
    conditions = filter_conditions(replace(filters, review_status=None))

    review_status_ok: ColumnElement[bool] | None = None
    if filters.review_status is not None:
        review_status_ok = Document.review_status == filters.review_status

    gated_include: ColumnElement[bool]
    if review_status_ok is not None:
        gated_include = review_status_ok & include_condition
        gated_exclusions: dict[str, ColumnElement[bool]] = {
            "filtered_review_status": ~review_status_ok,
            **{name: review_status_ok & condition for name, condition in exclusions.items()},
        }
    else:
        gated_include = include_condition
        gated_exclusions = exclusions

    columns = [
        func.count(Document.id),
        func.count(Document.id).filter(gated_include),
        func.count(Document.id).filter(
            gated_include, Document.review_status == ReviewStatus.NEEDS_REVIEW
        ),
        *(func.count(Document.id).filter(condition) for condition in gated_exclusions.values()),
    ]
    row = (await session.execute(select(*columns).where(*conditions))).one()
    matched, included, needs_review = int(row[0]), int(row[1]), int(row[2])
    excluded = {
        reason: int(count)
        for reason, count in zip(gated_exclusions, row[3:], strict=True)
        # A reason that dropped nothing is noise in the model's context and
        # would read as a caveat where there is none.
        if int(count) > 0
    }
    return Coverage(
        matched=matched, included=included, excluded=excluded, needs_review=needs_review
    )


async def list_documents(
    session: AsyncSession, *, filters: DocumentFilters, limit: int = 50
) -> Aggregated[DocumentRef]:
    """Matching documents, newest first (unknown dates last), with coverage.

    The over-limit drop is positional, not predicated: which documents fall off
    depends on the ORDER BY, so there is no SQL condition to hand
    ``count_coverage``. It is therefore computed here from ``matched`` and the
    page size, and ``needs_review`` is counted over the returned page — the rows
    the caller can actually see — rather than over the whole match.

    ``filters.review_status`` gets the same treatment as in ``count_coverage``:
    ``matched`` is computed against the filters MINUS ``review_status``, and the
    trust filter's drop is reported as its own ``filtered_review_status``
    exclusion rather than silently shrinking ``matched`` before ``over_limit``
    is even computed.
    """
    statement = (
        select(Document)
        .where(*filter_conditions(filters))
        .order_by(
            Document.document_date.desc().nulls_last(),
            Document.created_at.desc(),
            Document.id.desc(),
        )
        .limit(limit)
    )
    documents = (await session.execute(statement)).scalars().all()
    refs = [
        DocumentRef(
            id=document.id,
            title=document.title,
            sender=document.sender.name if document.sender else None,
            recipient=document.recipient.name if document.recipient else None,
            kind=document.kind.slug if document.kind else None,
            document_date=document.document_date,
            amount_total=str(document.amount_total) if document.amount_total is not None else None,
            currency=document.currency,
            review_status=document.review_status.value,
        )
        for document in documents
    ]

    baseline_conditions = filter_conditions(replace(filters, review_status=None))
    if filters.review_status is not None:
        review_status_ok = Document.review_status == filters.review_status
        row = (
            await session.execute(
                select(
                    func.count(Document.id),
                    func.count(Document.id).filter(review_status_ok),
                ).where(*baseline_conditions)
            )
        ).one()
        matched, filtered_matched = int(row[0]), int(row[1])
    else:
        matched = int(
            (
                await session.execute(select(func.count(Document.id)).where(*baseline_conditions))
            ).scalar_one()
        )
        filtered_matched = matched

    filtered_out = matched - filtered_matched
    over_limit = max(0, filtered_matched - len(refs))
    excluded: dict[str, int] = {}
    if filtered_out:
        excluded["filtered_review_status"] = filtered_out
    if over_limit:
        excluded["over_limit"] = over_limit

    return Aggregated(
        rows=refs,
        coverage=Coverage(
            matched=matched,
            included=len(refs),
            excluded=excluded,
            needs_review=sum(
                1 for document in documents if document.review_status is ReviewStatus.NEEDS_REVIEW
            ),
        ),
    )


async def distinct_senders(
    session: AsyncSession, *, filters: DocumentFilters
) -> Aggregated[SenderGroup]:
    """Distinct senders among matching documents, most documents first.

    The join to ``Sender`` is inner, so a document whose sender never extracted
    is absent from the breakdown entirely — reported as ``no_sender`` rather
    than left for the reader to notice the counts do not add up.

    At most ``MAX_GROUP_ROWS`` senders, those with the most documents; the
    documents of the senders cut are ``over_limit``.
    """
    # Grouped by id as well as name: names are unique, so the groups are the
    # same, and the id is what the over-limit predicate below needs.
    statement = (
        select(
            Sender.id,
            Sender.name,
            func.count(Document.id),
            func.array_agg(Document.id),
        )
        .join(Sender, Document.sender_id == Sender.id)
        .where(*filter_conditions(filters))
        .group_by(Sender.id, Sender.name)
        .order_by(func.count(Document.id).desc(), Sender.name)
    )
    rows = (await session.execute(statement)).all()
    kept = rows[:MAX_GROUP_ROWS]
    groups = [
        SenderGroup(sender=name, document_count=count, document_ids=sorted(ids)[:MAX_CITED_IDS])
        for _, name, count, ids in kept
    ]
    include: ColumnElement[bool] = Document.sender_id.isnot(None)
    exclusions: dict[str, ColumnElement[bool]] = {"no_sender": Document.sender_id.is_(None)}
    if len(kept) < len(rows):
        shown = Document.sender_id.in_([sender_id for sender_id, *_ in kept])
        exclusions["over_limit"] = include & ~shown
        include = include & shown
    coverage = await count_coverage(
        session, filters=filters, include_condition=include, exclusions=exclusions
    )
    return Aggregated(rows=groups, coverage=coverage)


def _summable_kinds(filters: DocumentFilters) -> frozenset[AmountKind]:
    """Which ``amount_kind`` values this call is asking to total.

    ``SUMMABLE_AMOUNT_KINDS`` — real expenditure — for every question but one.
    A caller filtering ``kind='quote'`` is asking "how much have my quotes come
    to?", and a quote's amount is an ``estimate``: the kind that exists
    *precisely* so a quote cannot contaminate a spend total, and therefore the
    only kind that can answer a question about quotes. The summable set follows
    the question rather than being fixed, which is what keeps the quote total
    working after the move onto ``spend_facts`` — under the fixed set it would
    have quietly become zero.

    It stays a **kind gate** either way: a document filed under kind ``quote``
    but carrying, say, a coverage ceiling still falls out, under
    ``not_summable_kind``.
    """
    if filters.kind_slug == "quote":
        return frozenset({AmountKind.ESTIMATE})
    return SUMMABLE_AMOUNT_KINDS


def _signed_amount(kinds: frozenset[AmountKind]) -> ColumnElement[Decimal]:
    """A ``spend_facts`` row's signed contribution to a total.

    ``amount`` is always a magnitude; the sign is a property of what the number
    *means* and lives in ``AMOUNT_SIGN``. A refund is the only negative, and
    getting this wrong is invisible in the result — a refund that adds produces
    a plausible number that is wrong by twice the refund.

    ``AMOUNT_SIGN.get(kind, 1)`` covers ``estimate``, which is summable only for
    the quote question above and carries no sign of its own: an estimate is
    money that would go out, so it counts positively.

    Sorted, though the branches are mutually exclusive and the total does not
    depend on their order: ``kinds`` is a **set**, so unsorted iteration emits a
    differently-ordered CASE on each process, and SQLAlchemy's compiled-statement
    cache is keyed on the statement's structure. Same answer, a fresh cache miss
    every run.

    ``else_`` is unreachable — the caller's WHERE restricts ``amount_kind`` to
    this same ``kinds`` — and is 0 rather than NULL so that if it ever became
    reachable it would fail as a wrong total rather than as a NULL that
    propagates through ``sum()`` and erases the whole group.
    """
    return spend_facts.c.amount * case(
        *(
            (spend_facts.c.amount_kind == kind.value, AMOUNT_SIGN.get(kind, 1))
            for kind in sorted(kinds, key=lambda kind: kind.value)
        ),
        else_=0,
    )


def _payment_choices(
    candidates: Sequence[ColumnElement[bool]],
    group_key_id: InstrumentedAttribute[int | None] | None,
) -> CTE:
    """One row per candidate document: which document carries its payment.

    Columns: ``document_id``; ``is_chosen`` (this document carries its
    payment); and the chosen document's ``chosen_date``, ``chosen_has_key``,
    ``chosen_key_id`` and ``chosen_currency``, which say where the payment
    lands: in which date window, whether in any grouped row at all, and in
    which row.

    **The ordering** is the ``spend_facts`` view's own (migration 0035): a
    line-bearing document first, so an itemised invoice keeps its split; then
    a ``payment_made``; then the lowest id. When grouping, having the group
    key comes before all three, so a payment is carried by a twin that can
    appear in a row whenever it has one (``chosen_has_key`` false means none
    does).

    **The candidates** are the documents that pass every caller filter
    EXCEPT the date window, and the earlier gates of ``sum_amount``. The view
    ranks every live document archive-wide, so its ``is_canonical`` can name a
    twin a ``kind`` or tag filter leaves out, and a total gated on it drops
    that payment. Ranking within the filters fixes that. The date window is
    deliberately not among them: a window that picked the twin would let a
    bill dated 28 September carry its payment in a September total while its
    3 October receipt carried it in October, and month totals would not add
    up. The window is applied to the chosen document instead
    (``payment_dated_elsewhere``).

    One row per document, not per ``spend_facts`` row: grouped by document,
    so a document with spend lines is ranked once (``has_lines`` is
    ``bool_or`` over its rows).

    A ``MATERIALIZED`` CTE: the row query and the coverage query each test
    several memberships against it, and computing the ranking once per
    statement instead of once per membership is most of their cost. It reads
    ``Document`` itself, and a CTE is never correlated to the outer query.
    """
    has_key: ColumnElement[bool] = group_key_id.isnot(None) if group_key_id is not None else true()
    window: dict[str, Any] = {
        "partition_by": spend_facts.c.payment_id,
        "order_by": (
            has_key.desc(),
            func.bool_or(spend_facts.c.line_id.isnot(None)).desc(),
            func.coalesce(Document.amount_kind == AmountKind.PAYMENT_MADE.value, False).desc(),
            Document.id.asc(),
        ),
    }
    return (
        select(
            Document.id.label("document_id"),
            ((func.row_number().over(**window) == 1) & has_key).label("is_chosen"),
            func.first_value(Document.document_date).over(**window).label("chosen_date"),
            func.first_value(has_key).over(**window).label("chosen_has_key"),
            func.first_value(group_key_id if group_key_id is not None else cast(null(), Integer))
            .over(**window)
            .label("chosen_key_id"),
            func.first_value(func.coalesce(Document.currency, ""))
            .over(**window)
            .label("chosen_currency"),
        )
        .select_from(Document)
        .join(spend_facts, spend_facts.c.document_id == Document.id)
        .where(*candidates)
        # `documents.id` is the primary key, so every `Document` column is
        # functionally dependent on it and may be read without aggregating.
        .group_by(Document.id, spend_facts.c.payment_id)
        .cte("payment_choices")
        .prefix_with("MATERIALIZED")
    )


def _chosen_in_window(choices: CTE, filters: DocumentFilters) -> ColumnElement[bool]:
    """The chosen document's date is inside the caller's date window.

    ``COALESCE(..., false)``: an undated chosen document is in no window, and a
    NULL here would drop its twins out of every coverage reason at once.
    """
    bounds: list[ColumnElement[bool]] = []
    if filters.date_from is not None:
        bounds.append(choices.c.chosen_date >= filters.date_from)
    if filters.date_to is not None:
        bounds.append(choices.c.chosen_date <= filters.date_to)
    if not bounds:
        return true()
    return func.coalesce(and_(*bounds), False)


async def sum_amount(
    session: AsyncSession, *, filters: DocumentFilters, group_by: GroupBy | None = None
) -> Aggregated[AmountGroup]:
    """Sum matching documents' money over ``spend_facts``, with coverage.

    Always grouped by currency (amounts in different currencies cannot be
    added); optionally also by sender or kind.

    **The rows are built from the ``spend_facts`` view, not from
    ``documents.amount_total``.** That view is the one relation that gets money
    right, and reading it is what buys three things the column cannot give:

    * **Sign.** ``amount_total`` is a magnitude; ``amount_kind`` says what it
      means. A refund reduces a total instead of adding to it.
    * **Kind.** Only ``SUMMABLE_AMOUNT_KINDS`` are expenditure, so a policy's
      cover limit, an account balance, an estimate and an undecided (NULL) kind
      stay out of "what did I spend".
    * **Payment identity.** The view's ``payment_id`` groups the documents of
      one payment, so one payment documented as an invoice *and* a receipt is
      totalled once. Which document carries it is decided among the documents
      that pass every filter but the date window (:func:`_payment_choices`),
      not by the view's archive-wide ``is_canonical``: a filter matching only
      the invoice still counts the payment. The date window then applies to
      the carrying document, so totals over adjacent windows add up. The
      charts keep the archive-wide flag.

    Coverage is still counted over ``documents`` by :func:`count_coverage`, and
    deliberately: every exclusion is expressible as a document-level
    predicate — ``amount_kind`` is a column on ``documents``, and where a
    document's payment landed is membership of one ranking CTE — so the
    partition holds by construction.

    The reasons a document is dropped, each meaning "survived every earlier
    gate, fails this one" so that they partition the matched set:

    * ``no_amount`` — extraction found no total. The dominant case, and the one
      that used to make a partial sum indistinguishable from a complete one.
    * ``quote_not_spend`` — quotes are not actual expenditure, so kind ``quote``
      is excluded unless the caller explicitly filters for it. Correct, but
      surprising enough that the answer should be able to say it happened.
    * ``not_summable_kind`` — the amount is real but is not spending: a
      ``coverage_limit``, a ``balance``, an ``estimate``, a ``none``, or an
      ``amount_kind`` nothing has decided yet.
    * ``duplicate_payment`` — a second document for a payment counted in these
      rows, never one whose payment the rows leave out.
    * ``payment_dated_elsewhere`` — its payment is carried by a twin dated
      outside the date window (or undated), so it counts in that twin's window.
    * ``no_sender`` / ``no_kind`` — only when grouping by that column, whose
      INNER JOIN drops documents that lack it: a document without the key
      whose payment no twin with the key carries.
    * ``over_limit`` — its payment lands in a group cut by ``MAX_GROUP_ROWS``
      (the largest |total|s are kept), whether it carries the payment or not.
    """
    # Explicitly correlated to Document: group_by="kind" joins Kind into the
    # outer query too, and without this SQLAlchemy auto-correlates the two
    # Kind references and strips this subquery of its FROM entirely.
    is_quote = (
        select(1)
        .where(Kind.id == Document.kind_id, Kind.slug == "quote")
        .correlate(Document)
        .exists()
    )
    has_amount = Document.amount_total.isnot(None)
    kinds = _summable_kinds(filters)
    kind_values = sorted(kind.value for kind in kinds)
    # `notin_` alone is NULL for a NULL `amount_kind`, and a NULL never satisfies
    # a FILTER clause — so an undecided document would be counted under no reason
    # at all and silently break the partition. The NULL arm is what makes
    # "undecided" a reportable state rather than a hole.
    summable_kind = Document.amount_kind.in_(kind_values)
    unsummable_kind = Document.amount_kind.is_(None) | Document.amount_kind.notin_(kind_values)
    # The group key's id column on `Document`. Names and slugs are unique, so
    # grouping rows by it as well as by the name changes no group, and it is
    # what the over-limit predicate and the payment choice read.
    key_column: InstrumentedAttribute[str] | None = None
    key_id: InstrumentedAttribute[int | None] | None = None
    if group_by == "sender":
        key_column, key_id = Sender.name, Document.sender_id
    elif group_by == "kind":
        key_column, key_id = Kind.slug, Document.kind_id

    # Each exclusion is "survived every earlier gate, but fails this one", so the
    # reasons partition the matched set instead of overlapping. A quote with an
    # amount and no sender must land in exactly one bucket, not two. The ORDER is
    # the contract: swapping two links leaves every total right and every reason
    # wrong, which no total-based assertion would catch.
    include: ColumnElement[bool] = has_amount
    exclusions: dict[str, ColumnElement[bool]] = {"no_amount": Document.amount_total.is_(None)}
    if filters.kind_slug != "quote":
        exclusions["quote_not_spend"] = include & is_quote
        include = include & ~is_quote
    exclusions["not_summable_kind"] = include & unsummable_kind
    include = include & summable_kind

    # Payment identity (see `_payment_choices`): ranked over every filter but
    # the date window, among the documents that passed every gate above.
    choices = _payment_choices(
        [*filter_conditions(replace(filters, date_from=None, date_to=None)), include], key_id
    )

    def member(condition: ColumnElement[bool]) -> ColumnElement[bool]:
        return Document.id.in_(select(choices.c.document_id).where(condition))

    conditions = [
        *filter_conditions(filters),
        member(choices.c.is_chosen),
        spend_facts.c.amount_kind.in_(kind_values),
    ]
    if filters.kind_slug != "quote":
        conditions.append(~is_quote)

    # DISTINCT on both: a document split across spend lines is one `spend_facts`
    # row PER LINE, so a plain count would report an itemised document once per
    # line and array_agg would cite it as many times. The money is right either
    # way, which is what makes this the quiet half of the bug.
    statement = (
        select(
            func.sum(_signed_amount(kinds)),
            spend_facts.c.currency,
            func.count(distinct(spend_facts.c.document_id)),
            func.array_agg(distinct(spend_facts.c.document_id)),
        )
        .select_from(Document)
        .join(spend_facts, spend_facts.c.document_id == Document.id)
        .where(*conditions)
    )

    # Joined through `Document`, not through the view's own `sender_id`, so the
    # join and the payment choice read the same column.
    if group_by == "sender":
        statement = statement.join(Sender, Document.sender_id == Sender.id)
    elif group_by == "kind":
        statement = statement.join(Kind, Document.kind_id == Kind.id)

    if key_column is not None and key_id is not None:
        statement = statement.add_columns(key_column, key_id).group_by(
            key_column, key_id, spend_facts.c.currency
        )
    else:
        statement = statement.group_by(spend_facts.c.currency)
    statement = statement.order_by(func.sum(_signed_amount(kinds)).desc())

    rows = (await session.execute(statement)).all()
    keyed = key_column is not None

    def size(index: int) -> tuple[Decimal, str, str]:
        """Largest |total| first, either sign: a refund-only sender's -90.00
        outweighs another's +5.00. Key and currency make ties deterministic."""
        row = rows[index]
        return (-abs(Decimal(row[0])), (row[4] or "") if keyed else "", row[1] or "")

    # Shown in the query's order (largest total first), not in size order.
    kept = sorted(sorted(range(len(rows)), key=size)[:MAX_GROUP_ROWS])
    groups = [
        AmountGroup(
            key=rows[index][4] if keyed else None,
            total=str(Decimal(rows[index][0])),
            currency=rows[index][1],
            document_count=rows[index][2],
            document_ids=sorted(rows[index][3])[:MAX_CITED_IDS],
        )
        for index in kept
    ]
    # Where each matched document's payment landed, read off its chosen
    # document. Every document past the gates above is a candidate, so it has
    # exactly one row in `choices` and exactly one of these holds for it:
    #   no chosen document with the key  -> no_sender / no_kind
    #   chosen, dated outside the window -> payment_dated_elsewhere
    #   chosen, in the window, row cut   -> over_limit
    #   chosen, in a row, not this one   -> duplicate_payment
    #   this document is the chosen one  -> included
    carried = choices.c.chosen_has_key
    in_window = carried & _chosen_in_window(choices, filters)
    shown: ColumnElement[bool] = true()
    if len(kept) < len(rows):
        # The row a payment lands in is its chosen document's. A NULL currency
        # is a group of its own (coalesced to "" in `choices`).
        if key_id is None:
            shown = choices.c.chosen_currency.in_([rows[index][1] or "" for index in kept])
        else:
            shown = tuple_(choices.c.chosen_key_id, choices.c.chosen_currency).in_(
                [(rows[index][5], rows[index][1] or "") for index in kept]
            )
    exclusions["duplicate_payment"] = include & member(in_window & shown & ~choices.c.is_chosen)
    exclusions["payment_dated_elsewhere"] = include & member(carried & ~in_window)
    if group_by is not None:
        exclusions["no_sender" if group_by == "sender" else "no_kind"] = include & member(~carried)
    if len(kept) < len(rows):
        exclusions["over_limit"] = include & member(in_window & ~shown)
    include = include & member(in_window & shown & choices.c.is_chosen)
    coverage = await count_coverage(
        session, filters=filters, include_condition=include, exclusions=exclusions
    )
    return Aggregated(rows=groups, coverage=coverage)


class QueryResult(TypedDict):
    """The shape every ``query_documents`` branch returns.

    A ``TypedDict`` rather than ``dict[str, object]`` so the caller can iterate
    ``result["rows"]`` without a cast: the row type is derived from this
    declaration instead of being re-asserted at the call site.

    ``result_type`` reuses ``Aggregate`` rather than widening to ``str``, so a
    new aggregate cannot be echoed back under a name the dispatcher does not
    know about.

    ``coverage`` is a serialised :class:`Coverage`. It is present on every
    branch — an aggregate that has nothing to disclose reports
    ``excluded == {}`` rather than omitting the key, so the model never has to
    distinguish "nothing was dropped" from "this tool does not say".
    """

    result_type: Aggregate
    rows: list[dict[str, Any]]
    coverage: dict[str, Any]


async def query_documents(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    aggregate: Aggregate = "list",
    group_by: GroupBy | None = None,
    limit: int = 50,
) -> QueryResult:
    """Dispatch a structured query and return a JSON-friendly result.

    The single entry point the ``/ask`` tool-use loop calls. ``result_type``
    echoes the aggregate so the caller can interpret ``rows``; ``coverage``
    says how much of the filtered set those rows account for.
    """
    if aggregate == "distinct_senders":
        senders = await distinct_senders(session, filters=filters)
        return {
            "result_type": "distinct_senders",
            "rows": [asdict(group) for group in senders.rows],
            "coverage": asdict(senders.coverage),
        }
    if aggregate == "sum_amount":
        amounts = await sum_amount(session, filters=filters, group_by=group_by)
        return {
            "result_type": "sum_amount",
            "rows": [asdict(group) for group in amounts.rows],
            "coverage": asdict(amounts.coverage),
        }
    documents = await list_documents(session, filters=filters, limit=limit)
    return {
        "result_type": "list",
        "rows": [_serialise_ref(ref) for ref in documents.rows],
        "coverage": asdict(documents.coverage),
    }


def _serialise_ref(ref: DocumentRef) -> dict[str, object]:
    """A DocumentRef as a JSON-friendly dict (date as ISO string)."""
    row = asdict(ref)
    row["document_date"] = ref.document_date.isoformat() if ref.document_date else None
    return row
