"""User settings: per-user display preferences (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.models import User
from library.schemas import DashboardPreferences, resolve_dashboard_preferences

router: APIRouter = APIRouter(tags=["settings"])


@router.get("/settings", response_model=DashboardPreferences, summary="Your display preferences")
async def get_settings(
    user: Annotated[User, Depends(current_user)],
) -> DashboardPreferences:
    """Resolved dashboard field preferences (defaults filled when unset)."""
    return resolve_dashboard_preferences(user.preferences)


@router.put("/settings", response_model=DashboardPreferences, summary="Update your preferences")
async def put_settings(
    payload: DashboardPreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> DashboardPreferences:
    """Persist the dashboard field list. Unknown keys are dropped (200)."""
    # Reassign the whole dict so SQLAlchemy detects the JSONB change.
    user.preferences = {
        **(user.preferences or {}),
        "dashboard_fields": [field.value for field in payload.dashboard_fields],
    }
    await db.commit()
    return payload
