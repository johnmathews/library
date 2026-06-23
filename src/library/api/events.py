"""Server-Sent Events: stream document pipeline lifecycle to the browser.

The worker emits a Postgres NOTIFY on ``library.jobs.EVENTS_CHANNEL`` as
documents move through the pipeline (see ``library.jobs.notify_document_event``).
This endpoint holds one dedicated asyncpg connection LISTENing on that channel
and relays each notification to the client as an SSE ``document`` event.

Authentication and CSRF-exemption come from the ``/api`` include-level
dependencies in ``app.py``: a GET is CSRF-safe, so ``EventSource`` — which can
only send cookies, not headers — authenticates with the session cookie exactly
like any other GET. Unauthenticated requests get a 401 before the stream opens.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import asyncpg
from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from library.config import get_settings
from library.jobs import EVENTS_CHANNEL, procrastinate_conninfo

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["events"])

# How often sse-starlette emits a keep-alive comment when no event is flowing,
# so proxies and the browser don't time the idle connection out.
_PING_SECONDS = 15


async def document_event_stream(dsn: str) -> AsyncIterator[dict[str, str]]:
    """Yield one SSE event per document NOTIFY, for the lifetime of the stream.

    Opens a dedicated asyncpg connection, LISTENs on the events channel, and
    relays each payload as an SSE ``document`` event whose data is the raw JSON
    (``{document_id, event, status, title}``). The connection is always torn
    down in the ``finally`` — when the client disconnects, sse-starlette cancels
    this generator, which runs cleanup.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    connection = await asyncpg.connect(dsn)

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        queue.put_nowait(payload)

    await connection.add_listener(EVENTS_CHANNEL, _on_notify)
    try:
        while True:
            payload = await queue.get()
            yield {"event": "document", "data": payload}
    finally:
        try:
            await connection.remove_listener(EVENTS_CHANNEL, _on_notify)
        finally:
            await connection.close()
        logger.debug("SSE client disconnected; LISTEN connection closed")


@router.get("/events")
async def stream_events() -> EventSourceResponse:
    """Stream document-pipeline lifecycle events to the client over SSE."""
    dsn = procrastinate_conninfo(get_settings().database_url)
    return EventSourceResponse(
        document_event_stream(dsn),
        ping=_PING_SECONDS,
        # Defensive: stop any reverse proxy (nginx) from buffering the stream.
        headers={"X-Accel-Buffering": "no"},
    )
