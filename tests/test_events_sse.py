"""Integration tests for the SSE endpoint GET /api/events.

The relay logic (LISTEN on the Postgres NOTIFY channel → SSE ``document``
events) is exercised directly against the test database via
``document_event_stream``; the route's auth gate is checked through the app.
Driving the open-ended stream through httpx's ASGITransport is unreliable
(it buffers infinite responses), so we test the generator itself.
"""

import asyncio
import contextlib
import json

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from library.api.events import document_event_stream
from library.jobs import EVENTS_CHANNEL, procrastinate_conninfo

pytestmark = pytest.mark.integration


async def _shutdown(pending: "asyncio.Future[object]", stream: object) -> None:
    """Cancel an in-flight __anext__ and then close the generator cleanly.

    aclose() raises if the generator's __anext__ is still running, so the
    pending task must be cancelled and awaited first.
    """
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await pending
    await stream.aclose()  # type: ignore[attr-defined]


async def _emit(api_database_url: str, payload: dict[str, object]) -> None:
    """Fire one NOTIFY on the events channel from a throwaway connection."""
    connection = await asyncpg.connect(procrastinate_conninfo(api_database_url))
    try:
        await connection.execute("SELECT pg_notify($1, $2)", EVENTS_CHANNEL, json.dumps(payload))
    finally:
        await connection.close()


async def test_document_event_stream_relays_notify(api_database_url: str) -> None:
    payload = {
        "document_id": 7,
        "event": "status_changed",
        "status": "ocr",
        "title": "Energierekening",
    }
    stream = document_event_stream(procrastinate_conninfo(api_database_url))
    # Starting the first __anext__ runs the connect + LISTEN, then blocks on the
    # queue. Once it's listening, the emitted NOTIFY resolves the pending event.
    first = asyncio.ensure_future(stream.__anext__())
    try:
        await asyncio.sleep(0.2)
        await _emit(api_database_url, payload)
        event = await asyncio.wait_for(asyncio.shield(first), timeout=5.0)
        assert event == {"event": "document", "data": json.dumps(payload)}
    finally:
        await _shutdown(first, stream)


async def test_document_event_stream_ignores_other_channels(
    api_database_url: str,
) -> None:
    """A NOTIFY on an unrelated channel must not surface as a document event."""
    stream = document_event_stream(procrastinate_conninfo(api_database_url))
    first = asyncio.ensure_future(stream.__anext__())
    try:
        await asyncio.sleep(0.2)
        connection = await asyncpg.connect(procrastinate_conninfo(api_database_url))
        try:
            await connection.execute("SELECT pg_notify('some_other_channel', 'x')")
        finally:
            await connection.close()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(first), timeout=0.5)
    finally:
        await _shutdown(first, stream)


async def test_events_requires_authentication(api_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_app), base_url="http://testserver"
    ) as client:
        response = await asyncio.wait_for(client.get("/api/events"), timeout=5.0)
        assert response.status_code == 401
