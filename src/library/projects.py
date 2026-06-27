"""Project/collection listing service with document counts.

Mirrors ``library.taxonomy.list_tags``: one place owns the counting rules
(soft-deleted documents excluded, zero-count projects included, archived
projects hidden unless asked for) so any surface over projects cannot drift.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Project, document_projects

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, non-alphanumeric runs to hyphens, trimmed, max 64 chars.

    Falls back to ``"project"`` when nothing usable remains.
    """
    slug = _NON_ALNUM.sub("-", value.lower()).strip("-")
    slug = slug[:64].strip("-")
    return slug or "project"


async def get_or_create_project(session: AsyncSession, identifier: str) -> Project:
    """Resolve a project by slug (raw or slugified ``identifier``), creating it.

    Mirrors ``extraction.apply.get_or_create_tag``: ``identifier`` may be an
    existing slug or a human name. We match on slug (the input itself and its
    slugified form) and, failing that, create a new project whose ``name`` is
    the cleaned input and whose ``slug`` is ``slugify(identifier)``.
    """
    cleaned = identifier.strip()
    slug = slugify(cleaned)
    existing = (
        (await session.execute(select(Project).where(Project.slug.in_({cleaned, slug}))))
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    project = Project(slug=slug, name=cleaned)
    session.add(project)
    await session.flush()
    return project


@dataclass(frozen=True)
class ProjectCount:
    """One project with the number of (non-deleted) documents it groups."""

    id: int
    slug: str
    name: str
    description: str | None
    document_count: int


async def list_projects(
    session: AsyncSession, *, include_archived: bool = False
) -> list[ProjectCount]:
    """All projects ordered by name; counts exclude soft-deleted documents.

    Zero-count projects are included. Archived projects are hidden unless
    ``include_archived`` is True.
    """
    statement = (
        select(
            Project.id,
            Project.slug,
            Project.name,
            Project.description,
            func.count(Document.id),
        )
        .join(document_projects, document_projects.c.project_id == Project.id, isouter=True)
        .join(
            Document,
            (Document.id == document_projects.c.document_id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Project.id)
        .order_by(Project.name)
    )
    if not include_archived:
        statement = statement.where(Project.archived_at.is_(None))
    rows = (await session.execute(statement)).all()
    return [
        ProjectCount(
            id=project_id, slug=slug, name=name, description=description, document_count=count
        )
        for project_id, slug, name, description, count in rows
    ]
