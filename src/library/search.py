"""Shared document query building for the REST API and the MCP server.

One place owns the filter conditions, the bilingual full-text search
(rank + snippet), and the ordering, so the two consumers cannot drift.

Search semantics (see docs/api.md §1.3.3)
-----------------------------------------
- The query runs ``websearch_to_tsquery`` against both generated tsvector
  columns (``dutch`` and ``english`` configs), OR-combined; the rank is
  ``greatest`` of the two ``ts_rank`` values so a document matching
  strongly in either language surfaces.
- Snippets come from ``ts_headline`` over ``ocr_text`` using whichever
  config produced the higher rank, capped by ``HEADLINE_OPTIONS``. The
  default ``<b>``/``</b>`` markers are kept; the OCR text is NOT
  HTML-escaped, so clients must render snippets as text and interpret
  only the ``<b>`` markers.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Select, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import (
    Document,
    DocumentChunk,
    DocumentLanguage,
    DocumentSource,
    DocumentStatus,
    Kind,
    Sender,
    Tag,
)

# ts_headline options: fragment mode, short fragments, default <b>/</b> markers.
HEADLINE_OPTIONS: str = (
    'MaxFragments=2, MaxWords=12, MinWords=4, ShortWord=2, FragmentDelimiter=" … "'
)

# Reciprocal Rank Fusion constant: a document at rank r in a result list
# contributes 1/(RRF_K + r). The conventional 60 damps the influence of the
# long tail so the top few results from each retriever dominate.
RRF_K: int = 60


@dataclass(frozen=True, slots=True)
class DocumentFilters:
    """Metadata filters that AND-compose (with each other and with a query)."""

    kind_slug: str | None = None
    sender_id: int | None = None
    sender_contains: str | None = None
    tag_slugs: Sequence[str] = field(default_factory=tuple)
    language: DocumentLanguage | None = None
    status: DocumentStatus | None = None
    source: DocumentSource | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True, slots=True)
class DocumentQuery:
    """A ready-to-execute pair of statements for one list/search request.

    ``statement`` yields ``Document`` rows when ``has_rank`` is False, and
    ``(Document, rank, snippet)`` rows when it is True. Apply
    ``.limit()``/``.offset()`` before executing; ``count`` returns the
    filtered total independent of pagination.
    """

    statement: Select[Any]
    count: Select[tuple[int]]
    has_rank: bool


def filter_conditions(filters: DocumentFilters) -> list[Any]:
    """WHERE conditions for the filters; always excludes soft-deleted rows."""
    conditions: list[Any] = [Document.deleted_at.is_(None)]
    if filters.kind_slug is not None:
        conditions.append(Document.kind.has(Kind.slug == filters.kind_slug))
    if filters.sender_id is not None:
        conditions.append(Document.sender_id == filters.sender_id)
    if filters.sender_contains is not None:
        escaped = (
            filters.sender_contains.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        )
        conditions.append(Document.sender.has(Sender.name.ilike(f"%{escaped}%", escape="\\")))
    for slug in filters.tag_slugs:
        conditions.append(Document.tags.any(Tag.slug == slug))
    if filters.language is not None:
        conditions.append(Document.language == filters.language)
    if filters.status is not None:
        conditions.append(Document.status == filters.status)
    if filters.date_from is not None:
        conditions.append(Document.document_date >= filters.date_from)
    if filters.date_to is not None:
        conditions.append(Document.document_date <= filters.date_to)
    if filters.source is not None:
        conditions.append(Document.source == filters.source)
    return conditions


def build_document_query(q: str | None, filters: DocumentFilters) -> DocumentQuery:
    """The list/search statement pair for a query string and filters.

    With ``q`` (non-empty), rows are ``(Document, rank, snippet)`` ordered
    by rank; without it, plain ``Document`` rows ordered by document_date
    (newest first, unknown dates last), then created_at.
    """
    conditions = filter_conditions(filters)

    if q:
        dutch = cast("dutch", REGCONFIG)
        english = cast("english", REGCONFIG)
        tsq_nl = func.websearch_to_tsquery(dutch, q)
        tsq_en = func.websearch_to_tsquery(english, q)
        rank_nl = func.ts_rank(Document.search_vector_nl, tsq_nl)
        rank_en = func.ts_rank(Document.search_vector_en, tsq_en)
        conditions.append(
            or_(
                Document.search_vector_nl.bool_op("@@")(tsq_nl),
                Document.search_vector_en.bool_op("@@")(tsq_en),
            )
        )
        rank = func.greatest(rank_nl, rank_en).label("rank")
        ocr_source = func.coalesce(Document.ocr_text, "")
        snippet = case(
            (rank_nl >= rank_en, func.ts_headline(dutch, ocr_source, tsq_nl, HEADLINE_OPTIONS)),
            else_=func.ts_headline(english, ocr_source, tsq_en, HEADLINE_OPTIONS),
        ).label("snippet")
        statement: Select[Any] = (
            select(Document, rank, snippet)
            .where(*conditions)
            .order_by(rank.desc(), Document.created_at.desc(), Document.id.desc())
        )
        has_rank = True
    else:
        statement = (
            select(Document)
            .where(*conditions)
            .order_by(
                Document.document_date.desc().nulls_last(),
                Document.created_at.desc(),
                Document.id.desc(),
            )
        )
        has_rank = False

    count = select(func.count()).select_from(Document).where(*conditions)
    return DocumentQuery(statement=statement, count=count, has_rank=has_rank)


@dataclass(frozen=True, slots=True)
class SemanticHit:
    """One fused retrieval result: a document plus its best-matching chunk.

    ``score`` is the RRF score (higher is better). ``chunk_index``/``chunk_text``
    are the nearest chunk by vector distance, or ``None`` when the document
    surfaced only through full-text search (no chunk in the candidate pool).
    """

    document: Document
    score: float
    chunk_index: int | None
    chunk_text: str | None


async def _vector_candidates(
    session: AsyncSession,
    conditions: list[Any],
    query_embedding: Sequence[float],
    pool: int,
) -> tuple[list[int], dict[int, tuple[int, str]]]:
    """Documents ranked by their nearest chunk, plus that chunk per document."""
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
    statement = (
        select(DocumentChunk.document_id, DocumentChunk.chunk_index, DocumentChunk.text)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(*conditions)
        .order_by(distance.asc())
        .limit(pool)
    )
    order: list[int] = []
    best_chunk: dict[int, tuple[int, str]] = {}
    for document_id, chunk_index, text in (await session.execute(statement)).all():
        if document_id not in best_chunk:  # rows are distance-ordered: first = nearest
            best_chunk[document_id] = (chunk_index, text)
            order.append(document_id)
    return order, best_chunk


async def _fts_candidates(
    session: AsyncSession, conditions: list[Any], query: str, pool: int
) -> list[int]:
    """Document ids matching the bilingual FTS query, best rank first."""
    dutch = cast("dutch", REGCONFIG)
    english = cast("english", REGCONFIG)
    tsq_nl = func.websearch_to_tsquery(dutch, query)
    tsq_en = func.websearch_to_tsquery(english, query)
    rank = func.greatest(
        func.ts_rank(Document.search_vector_nl, tsq_nl),
        func.ts_rank(Document.search_vector_en, tsq_en),
    )
    statement = (
        select(Document.id)
        .where(
            *conditions,
            or_(
                Document.search_vector_nl.bool_op("@@")(tsq_nl),
                Document.search_vector_en.bool_op("@@")(tsq_en),
            ),
        )
        .order_by(rank.desc(), Document.id.desc())
        .limit(pool)
    )
    return list((await session.execute(statement)).scalars().all())


async def semantic_search(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: Sequence[float],
    filters: DocumentFilters | None = None,
    top_k: int = 10,
    candidate_pool: int | None = None,
) -> list[SemanticHit]:
    """Hybrid retrieval: fuse vector kNN and full-text search with RRF.

    The vector retriever ranks documents by their nearest chunk to
    ``query_embedding``; the FTS retriever ranks by ``ts_rank`` over the query
    text (skipped when ``query`` is blank). Both honour ``filters``. Results
    are combined with Reciprocal Rank Fusion and the top ``top_k`` returned,
    each carrying its nearest chunk for citation.
    """
    filters = filters or DocumentFilters()
    conditions = filter_conditions(filters)
    pool = candidate_pool if candidate_pool is not None else max(top_k * 5, 50)

    vector_order, best_chunk = await _vector_candidates(
        session, conditions, query_embedding, pool
    )
    fts_order = await _fts_candidates(session, conditions, query, pool) if query.strip() else []

    scores: dict[int, float] = {}
    for position, document_id in enumerate(vector_order, start=1):
        scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (RRF_K + position)
    for position, document_id in enumerate(fts_order, start=1):
        scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (RRF_K + position)

    if not scores:
        return []
    ranked = sorted(
        scores, key=lambda document_id: (scores[document_id], document_id), reverse=True
    )[:top_k]

    documents = (
        (await session.execute(select(Document).where(Document.id.in_(ranked)))).scalars().all()
    )
    by_id = {document.id: document for document in documents}
    hits: list[SemanticHit] = []
    for document_id in ranked:
        document = by_id.get(document_id)
        if document is None:
            continue
        chunk = best_chunk.get(document_id)
        hits.append(
            SemanticHit(
                document=document,
                score=scores[document_id],
                chunk_index=chunk[0] if chunk else None,
                chunk_text=chunk[1] if chunk else None,
            )
        )
    return hits
