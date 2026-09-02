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

from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal, TypedDict

from sqlalchemy import ColumnElement, case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    """
    statement = (
        select(
            Sender.name,
            func.count(Document.id),
            func.array_agg(Document.id),
        )
        .join(Sender, Document.sender_id == Sender.id)
        .where(*filter_conditions(filters))
        .group_by(Sender.name)
        .order_by(func.count(Document.id).desc(), Sender.name)
    )
    rows = (await session.execute(statement)).all()
    groups = [
        SenderGroup(sender=name, document_count=count, document_ids=sorted(ids)[:MAX_CITED_IDS])
        for name, count, ids in rows
    ]
    coverage = await count_coverage(
        session,
        filters=filters,
        include_condition=Document.sender_id.isnot(None),
        exclusions={"no_sender": Document.sender_id.is_(None)},
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
    * **Payment identity.** ``is_canonical`` picks exactly one document per
      payment, so one payment documented as an invoice *and* a receipt is
      totalled once.

    Coverage is still counted over ``documents`` by :func:`count_coverage`, and
    deliberately: every new exclusion is expressible as a document-level
    predicate — ``amount_kind`` is a column on ``documents``, and canonicality is
    an ``EXISTS`` against the view — so only the row-building query moved.

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
    * ``duplicate_payment`` — a second document for a payment already counted.
    * ``no_sender`` / ``no_kind`` — only when grouping by that column, whose
      INNER JOIN drops documents that lack it.
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
    # `payments` seeds its reachability from every live document, so a document
    # with an amount always has at least one `spend_facts` row. Having none that
    # is canonical therefore means exactly one thing: another document holds the
    # payment's canonical slot.
    is_canonical = (
        select(1)
        .select_from(spend_facts)
        .where(spend_facts.c.document_id == Document.id, spend_facts.c.is_canonical)
        .correlate(Document)
        .exists()
    )

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
    exclusions["duplicate_payment"] = include & ~is_canonical
    include = include & is_canonical
    if group_by == "sender":
        exclusions["no_sender"] = include & Document.sender_id.is_(None)
        include = include & Document.sender_id.isnot(None)
    elif group_by == "kind":
        exclusions["no_kind"] = include & Document.kind_id.is_(None)
        include = include & Document.kind_id.isnot(None)

    conditions = [
        *filter_conditions(filters),
        spend_facts.c.is_canonical,
        spend_facts.c.amount_kind.in_(kind_values),
    ]
    if filters.kind_slug != "quote":
        conditions.append(~is_quote)

    # DISTINCT on both: a document split across spend lines is one `spend_facts`
    # row PER LINE, so a plain count would report an itemised document once per
    # line and array_agg would cite it as many times. The money is right either
    # way, which is what makes this the quiet half of the bug.
    key_column = None
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
    # join and `count_coverage`'s `no_sender` predicate read the same column.
    if group_by == "sender":
        key_column = Sender.name
        statement = statement.join(Sender, Document.sender_id == Sender.id)
    elif group_by == "kind":
        key_column = Kind.slug
        statement = statement.join(Kind, Document.kind_id == Kind.id)

    if key_column is not None:
        statement = statement.add_columns(key_column).group_by(key_column, spend_facts.c.currency)
    else:
        statement = statement.group_by(spend_facts.c.currency)
    statement = statement.order_by(func.sum(_signed_amount(kinds)).desc())

    groups: list[AmountGroup] = []
    for row in (await session.execute(statement)).all():
        total, currency, count, ids = row[0], row[1], row[2], row[3]
        key = row[4] if key_column is not None else None
        groups.append(
            AmountGroup(
                key=key,
                total=str(Decimal(total)),
                currency=currency,
                document_count=count,
                document_ids=sorted(ids)[:MAX_CITED_IDS],
            )
        )
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
