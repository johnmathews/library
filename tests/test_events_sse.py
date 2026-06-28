"""Integration tests for the SSE endpoint GET /api/events and its broker.

The fan-out logic (one shared LISTEN connection → many in-process SSE client
queues) is exercised directly against the test database via ``EventsBroker``
and ``document_event_stream``; the route's auth gate and lifespan wiring are
checked through the app. Driving the open-ended stream through httpx's
ASGITransport is unreliable (it buffers infinite responses), so we test the
generator and broker directly.
"""

import asyncio
import contextlib
import json

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from procrastinate import PsycopgConnector
from starlette.testclient import TestClient

from library import events_broker
from library.api.events import document_event_stream
from library.events_broker import EventsBroker
from library.jobs import EVENTS_CHANNEL, job_app, procrastinate_conninfo

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
    broker = EventsBroker(procrastinate_conninfo(api_database_url))
    await broker.start()
    stream = document_event_stream(broker)
    # Starting the first __anext__ registers the client queue, then blocks on
    # it. Once registered, the emitted NOTIFY fans out and resolves the event.
    first = asyncio.ensure_future(stream.__anext__())
    try:
        await asyncio.sleep(0.2)
        await _emit(api_database_url, payload)
        event = await asyncio.wait_for(asyncio.shield(first), timeout=5.0)
        assert event == {"event": "document", "data": json.dumps(payload)}
    finally:
        await _shutdown(first, stream)
        await broker.stop()


async def test_single_connection_serves_many_clients(
    api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N concurrent clients are served by exactly ONE Postgres LISTEN connection.

    This is the whole point of the fix: per-client ``asyncpg.connect`` exhausted
    Postgres ``max_connections`` under the e2e workload. Spy on ``asyncpg.connect``
    to prove the broker opens it once regardless of client count, and that a
    single NOTIFY fans out to every registered client.
    """
    payload = {"document_id": 1, "event": "status_changed", "status": "ocr", "title": "x"}
    connect_calls = 0
    real_connect = asyncpg.connect

    async def counting_connect(*args: object, **kwargs: object) -> asyncpg.Connection:
        nonlocal connect_calls
        connect_calls += 1
        return await real_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(events_broker.asyncpg, "connect", counting_connect)

    broker = EventsBroker(procrastinate_conninfo(api_database_url))
    await broker.start()
    streams = [document_event_stream(broker) for _ in range(3)]
    firsts = [asyncio.ensure_future(s.__anext__()) for s in streams]
    try:
        await asyncio.sleep(0.2)  # let all three register their queues
        assert broker.subscriber_count == 3
        # Three concurrent clients opened exactly one Postgres connection — the
        # broker's shared LISTEN. Checked before _emit, which opens its own
        # throwaway connection (counted too, since the spy is module-level).
        assert connect_calls == 1
        await _emit(api_database_url, payload)
        events = await asyncio.wait_for(asyncio.gather(*firsts), timeout=5.0)
        assert all(e == {"event": "document", "data": json.dumps(payload)} for e in events)
    finally:
        for first, stream in zip(firsts, streams, strict=True):
            await _shutdown(first, stream)
        await broker.stop()


async def test_disconnect_unregisters_without_closing_shared_connection(
    api_database_url: str,
) -> None:
    """A client disconnect drops only its queue; the shared connection lives on."""
    broker = EventsBroker(procrastinate_conninfo(api_database_url))
    await broker.start()
    s1 = document_event_stream(broker)
    s2 = document_event_stream(broker)
    f1 = asyncio.ensure_future(s1.__anext__())
    f2 = asyncio.ensure_future(s2.__anext__())
    try:
        await asyncio.sleep(0.2)
        assert broker.subscriber_count == 2

        # Disconnect the first client.
        await _shutdown(f1, s1)
        assert broker.subscriber_count == 1
        assert broker.running  # shared LISTEN connection is NOT closed

        # The surviving client still receives fanned-out events.
        payload = {"document_id": 2, "event": "status_changed", "status": "embed", "title": "y"}
        await _emit(api_database_url, payload)
        event = await asyncio.wait_for(asyncio.shield(f2), timeout=5.0)
        assert event == {"event": "document", "data": json.dumps(payload)}
    finally:
        await _shutdown(f2, s2)
        await broker.stop()
    assert not broker.running  # stop() closes the shared connection


async def test_document_event_stream_ignores_other_channels(
    api_database_url: str,
) -> None:
    """A NOTIFY on an unrelated channel must not surface as a document event."""
    broker = EventsBroker(procrastinate_conninfo(api_database_url))
    await broker.start()
    stream = document_event_stream(broker)
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
        await broker.stop()


def test_lifespan_starts_and_stops_shared_broker(api_app: FastAPI, api_database_url: str) -> None:
    """The FastAPI lifespan opens one shared broker and tears it down on exit."""
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app):
        broker = api_app.state.events_broker
        assert isinstance(broker, EventsBroker)
        assert broker.running
    # Leaving the TestClient context runs lifespan shutdown.
    assert not broker.running


async def test_events_requires_authentication(api_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_app), base_url="http://testserver"
    ) as client:
        response = await asyncio.wait_for(client.get("/api/events"), timeout=5.0)
        assert response.status_code == 401
