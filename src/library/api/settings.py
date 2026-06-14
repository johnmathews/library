"""User settings: per-user display preferences (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.models import User
from library.schemas import (
    AppearancePreferences,
    DashboardPreferences,
    UserPreferences,
    resolve_preferences,
)

router: APIRouter = APIRouter(tags=["settings"])


@router.get("/settings", response_model=UserPreferences, summary="Your display preferences")
async def get_settings(
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Resolved display preferences (defaults filled when unset)."""
    return resolve_preferences(user.preferences)


@router.put("/settings", response_model=UserPreferences, summary="Update your dashboard fields")
async def put_settings(
    payload: DashboardPreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist the dashboard field list. Unknown keys are dropped (200)."""
    # Reassign the whole dict so SQLAlchemy detects the JSONB change; the
    # spread preserves sibling keys (e.g. background_tone).
    user.preferences = {
        **(user.preferences or {}),
        "dashboard_fields": [field.value for field in payload.dashboard_fields],
    }
    await db.commit()
    return resolve_preferences(user.preferences)


@router.put(
    "/settings/appearance",
    response_model=UserPreferences,
    summary="Update your page-canvas tone and tile preview",
)
async def put_appearance(
    payload: AppearancePreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist the page-canvas tone + tile preview. Unknown values default."""
    user.preferences = {
        **(user.preferences or {}),
        "background_tone": payload.background_tone.value,
        "tile_preview": payload.tile_preview.value,
    }
    await db.commit()
    return resolve_preferences(user.preferences)
