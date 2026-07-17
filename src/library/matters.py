"""Business-matter listing service with document counts.

Mirrors ``library.projects``: one place owns the counting rules (soft-deleted
documents excluded, zero-count matters included, archived matters hidden unless
asked for) so any surface over matters cannot drift. A matter is an evergreen
subject category (e.g. "car insurance") a document may belong to any number of;
its ``hint`` is the free-text description the LLM classifier uses to decide
membership.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Matter, document_matters

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, non-alphanumeric runs to hyphens, trimmed, max 64 chars.

    Falls back to ``"matter"`` when nothing usable remains.
    """
    slug = _NON_ALNUM.sub("-", value.lower()).strip("-")
    slug = slug[:64].strip("-")
    return slug or "matter"


async def get_or_create_matter(session: AsyncSession, identifier: str) -> Matter:
    """Resolve a matter by slug (raw or slugified ``identifier``), creating it.

    Mirrors ``library.projects.get_or_create_project``: ``identifier`` may be an
    existing slug or a human name. We match on slug (the input itself and its
    slugified form) and, failing that, create a new matter whose ``name`` is the
    cleaned input and whose ``slug`` is ``slugify(identifier)``.
    """
    cleaned = identifier.strip()
    slug = slugify(cleaned)
    existing = (
        (await session.execute(select(Matter).where(Matter.slug.in_({cleaned, slug}))))
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    matter = Matter(slug=slug, name=cleaned)
    session.add(matter)
    await session.flush()
    return matter


@dataclass(frozen=True)
class MatterCount:
    """One matter with the number of (non-deleted) documents it groups."""

    id: int
    slug: str
    name: str
    hint: str | None
    document_count: int


async def list_matters(
    session: AsyncSession, *, include_archived: bool = False
) -> list[MatterCount]:
    """All matters ordered by name; counts exclude soft-deleted documents.

    Zero-count matters are included. Archived matters are hidden unless
    ``include_archived`` is True.
    """
    statement = (
        select(
            Matter.id,
            Matter.slug,
            Matter.name,
            Matter.hint,
            func.count(Document.id),
        )
        .join(document_matters, document_matters.c.matter_id == Matter.id, isouter=True)
        .join(
            Document,
            (Document.id == document_matters.c.document_id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Matter.id)
        .order_by(Matter.name)
    )
    if not include_archived:
        statement = statement.where(Matter.archived_at.is_(None))
    rows = (await session.execute(statement)).all()
    return [
        MatterCount(id=matter_id, slug=slug, name=name, hint=hint, document_count=count)
        for matter_id, slug, name, hint, count in rows
    ]
