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

_BASE_QUERY = """
    SELECT j.id,
           j.status,
           j.task_name,
           j.attempts,
           j.scheduled_at,
           (j.args ->> 'document_id')::bigint AS document_id,
           j.status IN ('todo', 'doing') AS active,
           d.title AS document_title,
           d.status AS document_status,
           err.detail ->> 'error' AS error,
           (d.extra -> 'extraction' ->> 'cost_usd')::float8 AS cost_usd,
           CASE
               WHEN d.extra ? 'extraction' THEN
                   COALESCE((d.extra -> 'extraction' ->> 'input_tokens')::int, 0)
                   + COALESCE((d.extra -> 'extraction' ->> 'output_tokens')::int, 0)
               ELSE NULL
           END AS tokens
    FROM procrastinate_jobs j
    LEFT JOIN documents d ON d.id = (j.args ->> 'document_id')::bigint
    LEFT JOIN LATERAL (
        SELECT detail
        FROM ingestion_events e
        WHERE e.document_id = d.id AND e.event = 'failed'
        ORDER BY e.id DESC
        LIMIT 1
    ) err ON true
    {where}
    ORDER BY j.id DESC
    LIMIT :limit
"""

# System/periodic tasks (e.g. the scheduled email poll) carry no document_id and
# fire constantly, so their routine successes bury actual document work. Hide
# them by default; keep any that failed or are still running, so a broken poller
# stays visible. `?include_system=true` opts back into the full list.
_HIDE_SYSTEM = "WHERE (j.args ->> 'document_id') IS NOT NULL OR j.status <> 'succeeded'"

_JOBS_QUERY = text(_BASE_QUERY.format(where=""))
_JOBS_QUERY_NO_SYSTEM = text(_BASE_QUERY.format(where=_HIDE_SYSTEM))


@router.get("/jobs", response_model=list[JobInfo])
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_system: Annotated[bool, Query()] = False,
) -> list[JobInfo]:
    """The most recent background jobs, newest first.

    Document-less system/periodic jobs (the email poll) are hidden unless they
    failed or are still running; pass ``include_system=true`` to list them all.
    """
    query = _JOBS_QUERY if include_system else _JOBS_QUERY_NO_SYSTEM
    result = await session.execute(query, {"limit": limit})
    return [JobInfo.model_validate(row, from_attributes=True) for row in result.all()]
