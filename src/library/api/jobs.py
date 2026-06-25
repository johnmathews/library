"""Job-queue visibility endpoint (reads Procrastinate's tables directly).

Authentication is enforced at include level in app.py; see docs/api.md §1.9.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from library.db import get_session
from library.schemas import JobInfo

router: APIRouter = APIRouter(tags=["jobs"])

# The enriched per-job projection. One document spawns several jobs
# (process_document, generate_thumbnail, and the per-document backfill tasks),
# so the default view collapses to one row per document — its most recent job —
# via DISTINCT ON. Document-less system/periodic jobs (the email poll) have no
# document to group by; key them by `-j.id` so each stays its own row.
#
# `{distinct}`, `{inner_where}` and `{inner_order}` are filled by `_build_query`
# below — they only ever hold fixed SQL fragments (never request values), so the
# composed statement stays injection-safe; every value travels as a bind param.
_INNER_SELECT = """
    SELECT {distinct}
           j.id,
           j.status,
           j.task_name,
           j.attempts,
           j.scheduled_at,
           (SELECT max(at) FROM procrastinate_events ev
            WHERE ev.job_id = j.id AND ev.type = 'started') AS started_at,
           (SELECT max(at) FROM procrastinate_events ev
            WHERE ev.job_id = j.id
              AND ev.type IN ('succeeded', 'failed', 'aborted')) AS finished_at,
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
    {inner_where}
    {inner_order}
"""

# Collapse-to-one-row-per-document fragments (the default, history-less view).
_COLLAPSE_KEY = "COALESCE((j.args ->> 'document_id')::bigint, -j.id)"
_DISTINCT = f"DISTINCT ON ({_COLLAPSE_KEY})"
_COLLAPSE_ORDER = f"ORDER BY {_COLLAPSE_KEY}, j.id DESC"

# System/periodic tasks (e.g. the scheduled email poll) carry no document_id and
# fire constantly, so their routine successes bury actual document work. Hide
# them by default; keep any that failed or are still running, so a broken poller
# stays visible.
_HIDE_SYSTEM = "document_id IS NOT NULL OR status <> 'succeeded'"


def _build_query(
    *, document_id: int | None, task_name: str | None, include_system: bool
) -> TextClause:
    """Compose the jobs query for the given filters.

    Three independent toggles drive the SQL shape:

    * ``document_id`` set → **history mode**: scan only that document's jobs and
      skip the per-document collapse, so the full job history comes back (newest
      first). The hide-system clause is irrelevant here — every row is that one
      document — so it is not applied.
    * ``document_id`` unset → the default collapsed view (one row per document).
    * ``task_name`` set → restrict to that task. The filter is applied **inside**
      the CTE, *before* the per-document collapse — applying it after would drop a
      document whose latest job is a different task (e.g. filtering by
      ``process_document`` would miss a document whose newest job is
      ``embed_document``). A task filter also implies system rows are wanted, so
      it suppresses the hide-system default.
    * ``include_system`` only matters for the collapsed view with no task filter.
    """
    # Inner-scan predicates run before the collapse, so each keeps the right job
    # per document. Both are bind params — never interpolated values.
    inner_conditions: list[str] = []
    if document_id is not None:
        inner_conditions.append("(j.args ->> 'document_id')::bigint = :document_id")
    if task_name is not None:
        inner_conditions.append("j.task_name = :task_name")
    inner_where = f"WHERE {' AND '.join(inner_conditions)}" if inner_conditions else ""

    if document_id is not None:
        inner = _INNER_SELECT.format(distinct="", inner_where=inner_where, inner_order="")
    else:
        inner = _INNER_SELECT.format(
            distinct=_DISTINCT, inner_where=inner_where, inner_order=_COLLAPSE_ORDER
        )

    # Hide succeeded system tasks only in the plain collapsed view — never in
    # history mode, and never alongside a task filter (which would drop the very
    # rows being filtered for).
    outer_where = (
        f"WHERE {_HIDE_SYSTEM}"
        if document_id is None and task_name is None and not include_system
        else ""
    )
    return text(
        f"WITH per_document AS ({inner})\n"
        f"SELECT * FROM per_document {outer_where} ORDER BY id DESC LIMIT :limit"
    )


@router.get("/jobs", response_model=list[JobInfo])
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_system: Annotated[bool, Query()] = False,
    document_id: Annotated[int | None, Query()] = None,
    task_name: Annotated[str | None, Query()] = None,
) -> list[JobInfo]:
    """The most recent jobs, newest first — one row per document (its latest job).

    Document-less system/periodic jobs (the email poll) are hidden unless they
    failed or are still running; pass ``include_system=true`` to list them all.

    ``document_id`` switches to history mode: every job for that one document,
    newest first (not collapsed), so a document's full processing history can be
    traced. ``task_name`` restricts the result to a single (fully-qualified) task
    and implies system rows are shown.
    """
    query = _build_query(
        document_id=document_id, task_name=task_name, include_system=include_system
    )
    params: dict[str, object] = {"limit": limit}
    if document_id is not None:
        params["document_id"] = document_id
    if task_name is not None:
        params["task_name"] = task_name
    result = await session.execute(query, params)
    return [JobInfo.model_validate(row, from_attributes=True) for row in result.all()]


_TASK_NAMES_QUERY = text("SELECT DISTINCT task_name FROM procrastinate_jobs ORDER BY task_name")


@router.get("/jobs/task-names", response_model=list[str])
async def list_job_task_names(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    """The distinct task names present in the queue, for the task-type filter."""
    result = await session.execute(_TASK_NAMES_QUERY)
    return [row[0] for row in result.all()]
