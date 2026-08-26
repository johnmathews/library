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

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, TypedDict

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, ReviewStatus, Sender
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
    """
    conditions = filter_conditions(filters)
    columns = [
        func.count(Document.id),
        func.count(Document.id).filter(include_condition),
        func.count(Document.id).filter(
            include_condition, Document.review_status == ReviewStatus.NEEDS_REVIEW
        ),
        *(func.count(Document.id).filter(condition) for condition in exclusions.values()),
    ]
    row = (await session.execute(select(*columns).where(*conditions))).one()
    matched, included, needs_review = int(row[0]), int(row[1]), int(row[2])
    excluded = {
        reason: int(count)
        for reason, count in zip(exclusions, row[3:], strict=True)
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
    matched = (
        await session.execute(select(func.count(Document.id)).where(*filter_conditions(filters)))
    ).scalar_one()
    over_limit = max(0, int(matched) - len(refs))
    return Aggregated(
        rows=refs,
        coverage=Coverage(
            matched=int(matched),
            included=len(refs),
            excluded={"over_limit": over_limit} if over_limit else {},
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


async def sum_amount(
    session: AsyncSession, *, filters: DocumentFilters, group_by: GroupBy | None = None
) -> Aggregated[AmountGroup]:
    """Sum ``amount_total`` over matching documents, with coverage.

    Always grouped by currency (amounts in different currencies cannot be
    added); optionally also by sender or kind. Three things drop documents from
    the total, and all three are reported in ``coverage.excluded`` rather than
    happening silently:

    * ``no_amount`` — extraction found no total. The dominant case, and the one
      that used to make a partial sum indistinguishable from a complete one.
    * ``quote_not_spend`` — quotes/estimates are not actual expenditure, so kind
      ``quote`` is excluded unless the caller explicitly filters for it (e.g.
      "how much have my quotes come to?"). Correct, but surprising enough that
      the answer should be able to say it happened.
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

    # Each exclusion is "survived every earlier gate, but fails this one", so the
    # reasons partition the matched set instead of overlapping. A quote with an
    # amount and no sender must land in exactly one bucket, not two.
    include: ColumnElement[bool] = has_amount
    exclusions: dict[str, ColumnElement[bool]] = {"no_amount": Document.amount_total.is_(None)}
    if filters.kind_slug != "quote":
        exclusions["quote_not_spend"] = include & is_quote
        include = include & ~is_quote
    if group_by == "sender":
        exclusions["no_sender"] = include & Document.sender_id.is_(None)
        include = include & Document.sender_id.isnot(None)
    elif group_by == "kind":
        exclusions["no_kind"] = include & Document.kind_id.is_(None)
        include = include & Document.kind_id.isnot(None)

    conditions = [*filter_conditions(filters), has_amount]
    if filters.kind_slug != "quote":
        conditions.append(~is_quote)
    key_column = None
    statement = select(
        func.sum(Document.amount_total),
        Document.currency,
        func.count(Document.id),
        func.array_agg(Document.id),
    ).where(*conditions)

    if group_by == "sender":
        key_column = Sender.name
        statement = statement.join(Sender, Document.sender_id == Sender.id)
    elif group_by == "kind":
        key_column = Kind.slug
        statement = statement.join(Kind, Document.kind_id == Kind.id)

    if key_column is not None:
        statement = statement.add_columns(key_column).group_by(key_column, Document.currency)
    else:
        statement = statement.group_by(Document.currency)
    statement = statement.order_by(func.sum(Document.amount_total).desc())

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
    """

    result_type: Aggregate
    rows: list[dict[str, Any]]


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
    echoes the aggregate so the caller can interpret ``rows``.
    """
    if aggregate == "distinct_senders":
        senders = await distinct_senders(session, filters=filters)
        return {"result_type": "distinct_senders", "rows": [asdict(group) for group in senders]}
    if aggregate == "sum_amount":
        amounts = await sum_amount(session, filters=filters, group_by=group_by)
        return {"result_type": "sum_amount", "rows": [asdict(group) for group in amounts]}
    documents = await list_documents(session, filters=filters, limit=limit)
    return {"result_type": "list", "rows": [_serialise_ref(ref) for ref in documents]}


def _serialise_ref(ref: DocumentRef) -> dict[str, object]:
    """A DocumentRef as a JSON-friendly dict (date as ISO string)."""
    row = asdict(ref)
    row["document_date"] = ref.document_date.isoformat() if ref.document_date else None
    return row
