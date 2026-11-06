"""Which filter values the archive does not have.

``query_documents`` totals and counts what its filters match. A tag, project,
matter, kind or facet the archive lacks matches nothing, and a total over
nothing is a 0 that reads like an answer. The two surfaces that total (MCP
``money_query`` and Ask's ``query_documents``) call :func:`unknown_filter_values`
and refuse instead. The search tools do not: an empty result carries counts
that already say nothing matched.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Facet, FacetValue, Kind, Matter, Project, Tag
from library.search import DocumentFilters
from library.structured_query import CONCEPT_TO_KIND


@dataclass(frozen=True)
class UnknownFilterValues:
    """What ``filters`` names that the archive does not have."""

    kind: str | None = None
    #: The kind slug ``CONCEPT_TO_KIND`` maps ``kind`` to, when it is a concept word.
    kind_suggestion: str | None = None
    #: One phrase per unknown tag, project, matter, facet key or facet value,
    #: in argument order.
    others: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.kind is not None or bool(self.others)


async def unknown_filter_values(
    session: AsyncSession, filters: DocumentFilters
) -> UnknownFilterValues:
    """The kind, tag, project, matter, facet key and facet value in ``filters``
    that the archive does not have."""
    kind: str | None = None
    suggestion: str | None = None
    if filters.kind_slug is not None:
        known_kind = await session.scalar(select(Kind.id).where(Kind.slug == filters.kind_slug))
        if known_kind is None:
            kind = filters.kind_slug
            suggestion = CONCEPT_TO_KIND.get(kind.strip().lower())
    others: list[str] = []
    for label, model, slugs in (
        ("tag", Tag, filters.tag_slugs),
        ("project", Project, filters.project_slugs),
        ("matter", Matter, filters.matter_slugs),
    ):
        if not slugs:
            continue
        known = set((await session.scalars(select(model.slug).where(model.slug.in_(slugs)))).all())
        others.extend(f"unknown {label} '{slug}'" for slug in slugs if slug not in known)
    if filters.facets:
        pairs = (
            await session.execute(
                select(Facet.key, FacetValue.key)
                .outerjoin(FacetValue, FacetValue.facet_id == Facet.id)
                .where(Facet.key.in_(list(filters.facets)))
            )
        ).all()
        facets = {facet for facet, _ in pairs}
        values = set(pairs)
        for facet, value in filters.facets.items():
            if facet not in facets:
                others.append(f"unknown facet '{facet}'")
            elif (facet, value) not in values:
                others.append(f"unknown facet value {facet}='{value}'")
    return UnknownFilterValues(kind=kind, kind_suggestion=suggestion, others=others)
