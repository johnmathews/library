"""Saved-views REST endpoints: per-user CRUD over named document-list views.

A saved view is a named snapshot of the homepage filter/search state (the
frontend's canonical URL query, stored verbatim in ``filter_state``). Pinning a
view surfaces it as a custom dashboard in the sidebar. Every view belongs to one
user; all endpoints are scoped to the authenticated user, so one account never
sees or mutates another's views. Authentication is enforced at include level in
app.py (session cookie or bearer token); see docs/api.md §1.9.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.models import SavedView, User

router: APIRouter = APIRouter(tags=["saved-views"])


class SavedViewCreate(BaseModel):
    """Body of POST /api/saved-views."""

    name: str = Field(min_length=1, max_length=255)
    filter_state: dict[str, Any] = Field(
        default_factory=dict,
        description="The homepage URL query to restore (buildDocumentQuery output).",
    )
    pinned: bool = Field(default=False, description="Also show as a sidebar custom dashboard.")


class SavedViewUpdate(BaseModel):
    """Body of PATCH /api/saved-views/{id}; only fields present change."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    filter_state: dict[str, Any] | None = None
    pinned: bool | None = None


class SavedViewReorder(BaseModel):
    """Body of POST /api/saved-views/reorder: the caller's view ids, new order."""

    ids: list[int] = Field(description="All of the caller's saved-view ids, in the desired order.")


class SavedViewOut(BaseModel):
    """One saved view."""

    id: int
    name: str
    filter_state: dict[str, Any]
    pinned: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


def _out(view: SavedView) -> SavedViewOut:
    return SavedViewOut(
        id=view.id,
        name=view.name,
        filter_state=view.filter_state,
        pinned=view.pinned,
        sort_order=view.sort_order,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


async def _list_for_user(session: AsyncSession, user_id: int) -> list[SavedView]:
    """The user's views, ordered for display (sort_order then id)."""
    return list(
        (
            await session.execute(
                select(SavedView)
                .where(SavedView.user_id == user_id)
                .order_by(SavedView.sort_order, SavedView.id)
            )
        )
        .scalars()
        .all()
    )


async def _get_owned_view_or_404(session: AsyncSession, view_id: int, user_id: int) -> SavedView:
    """One of the caller's views, or 404 — a view owned by another user is
    indistinguishable from a missing one."""
    view = (
        await session.execute(
            select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user_id)
        )
    ).scalar_one_or_none()
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="saved view not found")
    return view


@router.get("/saved-views", response_model=list[SavedViewOut], summary="List saved views")
async def list_saved_views(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> list[SavedViewOut]:
    """The authenticated user's saved views, in display order."""
    return [_out(view) for view in await _list_for_user(session, user.id)]


@router.post(
    "/saved-views",
    response_model=SavedViewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved view",
)
async def create_saved_view(
    payload: SavedViewCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> SavedViewOut:
    """Create a view for the caller; it is appended after their existing views."""
    next_order = (
        await session.execute(
            select(func.coalesce(func.max(SavedView.sort_order), -1) + 1).where(
                SavedView.user_id == user.id
            )
        )
    ).scalar_one()
    view = SavedView(
        user_id=user.id,
        name=payload.name,
        filter_state=payload.filter_state,
        pinned=payload.pinned,
        sort_order=next_order,
    )
    session.add(view)
    await session.commit()
    await session.refresh(view)
    return _out(view)


@router.patch(
    "/saved-views/{view_id}",
    response_model=SavedViewOut,
    summary="Edit a saved view",
    responses={404: {"description": "Unknown saved view"}},
)
async def update_saved_view(
    view_id: int,
    payload: SavedViewUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> SavedViewOut:
    """Rename, re-target (``filter_state``), and/or pin/unpin one of the caller's views."""
    view = await _get_owned_view_or_404(session, view_id, user.id)
    provided = payload.model_dump(exclude_unset=True)
    if provided.get("name") is not None:
        view.name = provided["name"]
    if "filter_state" in provided and provided["filter_state"] is not None:
        view.filter_state = provided["filter_state"]
    if provided.get("pinned") is not None:
        view.pinned = provided["pinned"]
    await session.commit()
    await session.refresh(view)
    return _out(view)


@router.delete(
    "/saved-views/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved view",
    responses={404: {"description": "Unknown saved view"}},
)
async def delete_saved_view(
    view_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> None:
    """Delete one of the caller's views."""
    view = await _get_owned_view_or_404(session, view_id, user.id)
    await session.delete(view)
    await session.commit()


@router.post(
    "/saved-views/reorder",
    response_model=list[SavedViewOut],
    summary="Reorder saved views",
    responses={400: {"description": "ids do not match the caller's saved views"}},
)
async def reorder_saved_views(
    payload: SavedViewReorder,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> list[SavedViewOut]:
    """Set ``sort_order`` from the position of each id in ``ids``.

    ``ids`` must be exactly the caller's current view ids (any order); a
    mismatch is a 400 so a stale client can't silently drop or reorder around a
    view it didn't know about.
    """
    views = await _list_for_user(session, user.id)
    if set(payload.ids) != {view.id for view in views} or len(payload.ids) != len(views):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must be exactly the caller's saved-view ids",
        )
    order = {view_id: index for index, view_id in enumerate(payload.ids)}
    for view in views:
        view.sort_order = order[view.id]
    await session.commit()
    return [_out(view) for view in await _list_for_user(session, user.id)]
