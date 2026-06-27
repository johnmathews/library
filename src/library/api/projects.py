"""Projects REST endpoints: CRUD over document collections with counts.

Backed by the shared ``library.projects`` service (also behind the MCP
``list_projects`` tool). Authentication is enforced at include level in
app.py (session cookie or bearer token); see docs/api.md §1.9.

Slugs are stable: ``POST`` derives one from the name (or accepts an
explicit, normalised override) and ``PATCH`` never changes it, so inbound
links and the ``?project=`` document filter stay valid across renames.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library import projects as projects_service
from library.db import get_session
from library.models import Document, Project, document_projects

router: APIRouter = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    """Body of POST /api/projects."""

    name: str
    slug: str | None = Field(
        default=None, description="Optional explicit slug; normalised. Defaults to slugify(name)."
    )
    description: str | None = None


class ProjectUpdate(BaseModel):
    """Body of PATCH /api/projects/{slug}; only fields present change.

    The slug is immutable — only the name, description, and archived state can
    change. ``archived`` toggles ``archived_at``.
    """

    name: str | None = None
    description: str | None = None
    archived: bool | None = None


class ProjectOut(BaseModel):
    """One project with its (non-deleted) document count."""

    id: int
    slug: str
    name: str
    description: str | None
    archived: bool
    document_count: int = Field(description="Non-deleted documents in this project.")


def _out(project: Project, document_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        slug=project.slug,
        name=project.name,
        description=project.description,
        archived=project.archived_at is not None,
        document_count=document_count,
    )


async def _get_project_or_404(session: AsyncSession, slug: str) -> Project:
    project = (
        await session.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


async def _document_count(session: AsyncSession, project_id: int) -> int:
    """Non-deleted documents grouped under one project."""
    statement = (
        select(func.count(Document.id))
        .select_from(document_projects)
        .join(
            Document,
            (Document.id == document_projects.c.document_id) & Document.deleted_at.is_(None),
        )
        .where(document_projects.c.project_id == project_id)
    )
    return (await session.execute(statement)).scalar_one()


@router.get("/projects", response_model=list[ProjectOut], summary="List projects")
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_session)],
    include_archived: Annotated[
        bool, Query(description="Include archived projects (hidden by default).")
    ] = False,
) -> list[ProjectOut]:
    """All projects ordered by name, with per-project document counts."""
    rows = await projects_service.list_projects(session, include_archived=include_archived)
    archived_ids: set[int] = set()
    if include_archived:
        archived_ids = set(
            (await session.execute(select(Project.id).where(Project.archived_at.is_not(None))))
            .scalars()
            .all()
        )
    return [
        ProjectOut(
            id=row.id,
            slug=row.slug,
            name=row.name,
            description=row.description,
            archived=row.id in archived_ids,
            document_count=row.document_count,
        )
        for row in rows
    ]


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    responses={409: {"description": "A project with this slug already exists"}},
)
async def create_project(
    payload: ProjectCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    """Create a project; the slug defaults to ``slugify(name)`` and is unique."""
    slug = projects_service.slugify(payload.slug or payload.name)
    existing = (
        await session.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"project slug already exists: {slug!r}"
        )
    project = Project(slug=slug, name=payload.name, description=payload.description)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return _out(project, 0)


@router.get(
    "/projects/{slug}",
    response_model=ProjectOut,
    summary="Project detail",
    responses={404: {"description": "Unknown project"}},
)
async def get_project(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    """One project with its document count."""
    project = await _get_project_or_404(session, slug)
    return _out(project, await _document_count(session, project.id))


@router.patch(
    "/projects/{slug}",
    response_model=ProjectOut,
    summary="Edit a project",
    responses={404: {"description": "Unknown project"}},
)
async def update_project(
    slug: str,
    payload: ProjectUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    """Update name/description and/or toggle archived; the slug never changes."""
    project = await _get_project_or_404(session, slug)
    provided = payload.model_dump(exclude_unset=True)
    if "name" in provided and provided["name"] is not None:
        project.name = provided["name"]
    if "description" in provided:
        project.description = provided["description"]
    if "archived" in provided and provided["archived"] is not None:
        project.archived_at = datetime.now(UTC) if provided["archived"] else None
    await session.commit()
    await session.refresh(project)
    return _out(project, await _document_count(session, project.id))


@router.delete(
    "/projects/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project",
    responses={404: {"description": "Unknown project"}},
)
async def delete_project(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Hard-delete a project; memberships cascade, documents are untouched."""
    project = await _get_project_or_404(session, slug)
    await session.delete(project)
    await session.commit()
