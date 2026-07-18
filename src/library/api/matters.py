"""Matters REST endpoints: CRUD over evergreen business-matter categories.

Backed by the shared ``library.matters`` service (also behind the MCP
``list_matters`` tool). Authentication is enforced at include level in app.py
(session cookie or bearer token); see docs/api.md.

Slugs are stable: ``POST`` derives one from the name (or accepts an explicit,
normalised override) and ``PATCH`` never changes it, so inbound links and the
``?matter=`` document filter stay valid across renames. The ``hint`` is the
text the LLM classifier reads to decide which documents belong to the matter.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library import matters as matters_service
from library.auth.deps import require_admin
from library.db import get_session
from library.models import Document, Matter, User, document_matters

router: APIRouter = APIRouter(tags=["matters"])


class MatterCreate(BaseModel):
    """Body of POST /api/matters."""

    name: str
    slug: str | None = Field(
        default=None, description="Optional explicit slug; normalised. Defaults to slugify(name)."
    )
    hint: str | None = Field(
        default=None, description="Free-text description the LLM classifier uses for this matter."
    )


class MatterUpdate(BaseModel):
    """Body of PATCH /api/matters/{slug}; only fields present change.

    The slug is immutable — only the name, hint, and archived state can change.
    ``archived`` toggles ``archived_at``.
    """

    name: str | None = None
    hint: str | None = None
    archived: bool | None = None


class MatterOut(BaseModel):
    """One matter with its (non-deleted) document count."""

    id: int
    slug: str
    name: str
    hint: str | None
    archived: bool
    document_count: int = Field(description="Non-deleted documents in this matter.")


def _out(matter: Matter, document_count: int) -> MatterOut:
    return MatterOut(
        id=matter.id,
        slug=matter.slug,
        name=matter.name,
        hint=matter.hint,
        archived=matter.archived_at is not None,
        document_count=document_count,
    )


async def _get_matter_or_404(session: AsyncSession, slug: str) -> Matter:
    matter = (await session.execute(select(Matter).where(Matter.slug == slug))).scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="matter not found")
    return matter


async def _document_count(session: AsyncSession, matter_id: int) -> int:
    """Non-deleted documents grouped under one matter."""
    statement = (
        select(func.count(Document.id))
        .select_from(document_matters)
        .join(
            Document,
            (Document.id == document_matters.c.document_id) & Document.deleted_at.is_(None),
        )
        .where(document_matters.c.matter_id == matter_id)
    )
    return (await session.execute(statement)).scalar_one()


@router.get("/matters", response_model=list[MatterOut], summary="List matters")
async def list_matters(
    session: Annotated[AsyncSession, Depends(get_session)],
    include_archived: Annotated[
        bool, Query(description="Include archived matters (hidden by default).")
    ] = False,
) -> list[MatterOut]:
    """All matters ordered by name, with per-matter document counts."""
    rows = await matters_service.list_matters(session, include_archived=include_archived)
    archived_ids: set[int] = set()
    if include_archived:
        archived_ids = set(
            (await session.execute(select(Matter.id).where(Matter.archived_at.is_not(None))))
            .scalars()
            .all()
        )
    return [
        MatterOut(
            id=row.id,
            slug=row.slug,
            name=row.name,
            hint=row.hint,
            archived=row.id in archived_ids,
            document_count=row.document_count,
        )
        for row in rows
    ]


@router.post(
    "/matters",
    response_model=MatterOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a matter",
    responses={
        403: {"description": "Admin privileges required"},
        409: {"description": "A matter with this slug already exists"},
    },
)
async def create_matter(
    payload: MatterCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> MatterOut:
    """Create a matter; the slug defaults to ``slugify(name)`` and is unique."""
    slug = matters_service.slugify(payload.slug or payload.name)
    existing = (
        await session.execute(select(Matter).where(Matter.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"matter slug already exists: {slug!r}"
        )
    matter = Matter(slug=slug, name=payload.name, hint=payload.hint)
    session.add(matter)
    await session.commit()
    await session.refresh(matter)
    return _out(matter, 0)


@router.get(
    "/matters/{slug}",
    response_model=MatterOut,
    summary="Matter detail",
    responses={404: {"description": "Unknown matter"}},
)
async def get_matter(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MatterOut:
    """One matter with its document count."""
    matter = await _get_matter_or_404(session, slug)
    return _out(matter, await _document_count(session, matter.id))


@router.patch(
    "/matters/{slug}",
    response_model=MatterOut,
    summary="Edit a matter",
    responses={
        403: {"description": "Admin privileges required"},
        404: {"description": "Unknown matter"},
    },
)
async def update_matter(
    slug: str,
    payload: MatterUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> MatterOut:
    """Update name/hint and/or toggle archived; the slug never changes."""
    matter = await _get_matter_or_404(session, slug)
    provided = payload.model_dump(exclude_unset=True)
    if "name" in provided and provided["name"] is not None:
        matter.name = provided["name"]
    if "hint" in provided:
        matter.hint = provided["hint"]
    if "archived" in provided and provided["archived"] is not None:
        matter.archived_at = datetime.now(UTC) if provided["archived"] else None
    await session.commit()
    await session.refresh(matter)
    return _out(matter, await _document_count(session, matter.id))


@router.delete(
    "/matters/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a matter",
    responses={
        403: {"description": "Admin privileges required"},
        404: {"description": "Unknown matter"},
    },
)
async def delete_matter(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> None:
    """Hard-delete a matter; memberships cascade, documents are untouched."""
    matter = await _get_matter_or_404(session, slug)
    await session.delete(matter)
    await session.commit()
