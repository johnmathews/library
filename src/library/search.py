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

from library.models import (
    Document,
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
