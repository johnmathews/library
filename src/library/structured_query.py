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
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, Sender
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
}

Aggregate = Literal["list", "distinct_senders", "sum_amount"]
GroupBy = Literal["sender", "kind"]


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A document summary row for ``list`` results and citations."""

    id: int
    title: str | None
    sender: str | None
    kind: str | None
    document_date: date | None
    amount_total: str | None
    currency: str | None


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


async def list_documents(
    session: AsyncSession, *, filters: DocumentFilters, limit: int = 50
) -> list[DocumentRef]:
    """Matching documents, newest first (unknown dates last)."""
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
    return [
        DocumentRef(
            id=document.id,
            title=document.title,
            sender=document.sender.name if document.sender else None,
            kind=document.kind.slug if document.kind else None,
            document_date=document.document_date,
            amount_total=str(document.amount_total) if document.amount_total is not None else None,
            currency=document.currency,
        )
        for document in documents
    ]


async def distinct_senders(session: AsyncSession, *, filters: DocumentFilters) -> list[SenderGroup]:
    """Distinct senders among matching documents, most documents first."""
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
    return [
        SenderGroup(sender=name, document_count=count, document_ids=sorted(ids)[:MAX_CITED_IDS])
        for name, count, ids in rows
    ]


async def sum_amount(
    session: AsyncSession, *, filters: DocumentFilters, group_by: GroupBy | None = None
) -> list[AmountGroup]:
    """Sum ``amount_total`` over matching documents.

    Always grouped by currency (amounts in different currencies cannot be
    added); optionally also by sender or kind. Documents without an amount are
    excluded.
    """
    conditions = [*filter_conditions(filters), Document.amount_total.isnot(None)]
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
    return groups


async def query_documents(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    aggregate: Aggregate = "list",
    group_by: GroupBy | None = None,
    limit: int = 50,
) -> dict[str, object]:
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
