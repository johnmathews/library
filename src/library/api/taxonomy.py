"""Taxonomy REST endpoints: kinds, senders, recipients, and tags with document counts.

Backed by the shared ``library.taxonomy`` service (also used by the MCP
list tools). Authentication is enforced at include level in app.py
(session cookie or bearer token); see docs/api.md §1.9.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from library import taxonomy
from library.db import get_session

router: APIRouter = APIRouter(tags=["taxonomy"])


class KindWithCount(BaseModel):
    """One row of GET /api/kinds."""

    slug: str
    name: str
    document_count: int = Field(description="Non-deleted documents of this kind.")


class KindCreate(BaseModel):
    """Body of POST /api/kinds."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=255)] = Field(
        description="Human display name for the new kind (e.g. 'Quote')."
    )


class KindOut(BaseModel):
    """A created (or deduped existing) kind."""

    slug: str
    name: str


class KindNearDuplicate(BaseModel):
    """409 body when a new kind name is too similar to an existing one."""

    detail: str
    existing_slug: str
    existing_name: str


class SenderWithCount(BaseModel):
    """One row of GET /api/senders."""

    id: int
    name: str
    document_count: int = Field(description="Non-deleted documents from this sender.")


class RecipientWithCount(BaseModel):
    """One row of GET /api/recipients."""

    id: int
    name: str
    document_count: int = Field(description="Non-deleted documents addressed to this recipient.")


class TagWithCount(BaseModel):
    """One row of GET /api/tags."""

    slug: str
    name: str
    document_count: int = Field(description="Non-deleted documents carrying this tag.")


@router.get("/kinds", response_model=list[KindWithCount], summary="List document kinds")
async def list_kinds(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KindWithCount]:
    """All kinds (the seeded fixed set), ordered by slug, with document counts."""
    return [
        KindWithCount.model_validate(kind, from_attributes=True)
        for kind in await taxonomy.list_kinds(session)
    ]


@router.post(
    "/kinds",
    response_model=KindOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document kind",
    responses={
        409: {
            "model": KindNearDuplicate,
            "description": "Name is too similar to an existing kind",
        },
    },
)
async def create_kind(
    payload: KindCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KindOut | JSONResponse:
    """Create a kind from a display name, deduping and standardising casing.

    An exact (case/whitespace-insensitive) match returns the existing kind with
    ``200`` rather than creating a duplicate; a brand-new kind is created with
    ``201``; a near-duplicate name is refused with a flat ``409`` carrying the
    existing kind so the client can point the user at it.
    """
    result = await taxonomy.create_kind(session, payload.name)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind name must not be empty",
        )
    if result.status == "near_duplicate":
        assert result.existing is not None
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"a similar kind named {result.existing.name!r} already exists; "
                    "select it instead of creating a near-duplicate"
                ),
                "existing_slug": result.existing.slug,
                "existing_name": result.existing.name,
            },
        )
    assert result.kind is not None
    if result.status == "exists":
        response.status_code = status.HTTP_200_OK
    return KindOut(slug=result.kind.slug, name=result.kind.name)


@router.get("/senders", response_model=list[SenderWithCount], summary="List senders")
async def list_senders(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SenderWithCount]:
    """All known senders, ordered by name, with document counts."""
    return [
        SenderWithCount.model_validate(sender, from_attributes=True)
        for sender in await taxonomy.list_senders(session)
    ]


@router.get("/recipients", response_model=list[RecipientWithCount], summary="List recipients")
async def list_recipients(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[RecipientWithCount]:
    """All known recipients, ordered by name, with document counts."""
    return [
        RecipientWithCount.model_validate(recipient, from_attributes=True)
        for recipient in await taxonomy.list_recipients(session)
    ]


@router.get("/tags", response_model=list[TagWithCount], summary="List tags")
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TagWithCount]:
    """All tags, ordered by name, with document counts."""
    return [
        TagWithCount.model_validate(tag, from_attributes=True)
        for tag in await taxonomy.list_tags(session)
    ]
