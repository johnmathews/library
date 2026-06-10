"""Job-queue visibility endpoint (reads Procrastinate's tables directly).

Authentication is enforced at include level in app.py; see docs/api.md §1.9.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.schemas import JobInfo

router: APIRouter = APIRouter(tags=["jobs"])

_JOBS_QUERY = text(
    """
    SELECT id,
           status,
           task_name,
           attempts,
           scheduled_at,
           (args ->> 'document_id')::bigint AS document_id
    FROM procrastinate_jobs
    ORDER BY id DESC
    LIMIT :limit
    """
)


@router.get("/jobs", response_model=list[JobInfo])
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[JobInfo]:
    """The most recent background jobs, newest first."""
    result = await session.execute(_JOBS_QUERY, {"limit": limit})
    return [JobInfo.model_validate(row, from_attributes=True) for row in result.all()]
