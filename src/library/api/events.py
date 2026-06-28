"""Server-Sent Events: stream document pipeline lifecycle to the browser.

The worker emits a Postgres NOTIFY on ``library.jobs.EVENTS_CHANNEL`` as
documents move through the pipeline (see ``library.jobs.notify_document_event``).
A single process-wide ``EventsBroker`` (``library.events_broker``) holds *one*
asyncpg connection LISTENing on that channel and fans each notification out to
every connected client in-process — this endpoint just drains a per-client
queue and relays each payload as an SSE ``document`` event. No per-client
Postgres connection: SSE usage is capped at one connection per process.

Authentication and CSRF-exemption come from the ``/api`` include-level
dependencies in ``app.py``: a GET is CSRF-safe, so ``EventSource`` — which can
only send cookies, not headers — authenticates with the session cookie exactly
like any other GET. Unauthenticated requests get a 401 before the stream opens.
"""

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse

from library.events_broker import EventsBroker

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["events"])

# How often sse-starlette emits a keep-alive comment when no event is flowing,
# so proxies and the browser don't time the idle connection out.
_PING_SECONDS = 15


async def document_event_stream(broker: EventsBroker) -> AsyncIterator[dict[str, str]]:
    """Yield one SSE event per document NOTIFY, for the lifetime of the stream.

    Registers a per-client queue with the shared ``broker`` and relays each
    fanned-out payload as an SSE ``document`` event whose data is the raw JSON
    (``{document_id, event, status, title}``). The queue is always unregistered
    in the ``finally`` — when the client disconnects, sse-starlette cancels this
    generator, which drops the queue; the shared LISTEN connection is untouched.
    """
    queue = broker.register()
    try:
        while True:
            payload = await queue.get()
            yield {"event": "document", "data": payload}
    finally:
        broker.unregister(queue)
        logger.debug("SSE client disconnected; queue unregistered")


@router.get("/events")
async def stream_events(request: Request) -> EventSourceResponse:
    """Stream document-pipeline lifecycle events to the client over SSE."""
    broker: EventsBroker = request.app.state.events_broker
    return EventSourceResponse(
        document_event_stream(broker),
        ping=_PING_SECONDS,
        # Defensive: stop any reverse proxy (nginx) from buffering the stream.
        headers={"X-Accel-Buffering": "no"},
    )
