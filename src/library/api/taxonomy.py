"""Taxonomy REST endpoints: kinds, senders, and tags with document counts.

Backed by the shared ``library.taxonomy`` service (also used by the MCP
list tools). Authentication is enforced at include level in app.py
(session cookie or bearer token); see docs/api.md §1.9.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from library import taxonomy
from library.db import get_session

router: APIRouter = APIRouter(tags=["taxonomy"])


class KindWithCount(BaseModel):
    """One row of GET /api/kinds."""

    slug: str
    name: str
    document_count: int = Field(description="Non-deleted documents of this kind.")


class SenderWithCount(BaseModel):
    """One row of GET /api/senders."""

    id: int
    name: str
    document_count: int = Field(description="Non-deleted documents from this sender.")


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


@router.get("/senders", response_model=list[SenderWithCount], summary="List senders")
async def list_senders(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SenderWithCount]:
    """All known senders, ordered by name, with document counts."""
    return [
        SenderWithCount.model_validate(sender, from_attributes=True)
        for sender in await taxonomy.list_senders(session)
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
