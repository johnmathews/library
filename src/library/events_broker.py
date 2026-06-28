"""Process-wide Postgres LISTEN broker that fans NOTIFY out to SSE clients.

The worker emits a Postgres NOTIFY on ``library.jobs.EVENTS_CHANNEL`` as
documents move through the pipeline (see ``library.jobs.notify_document_event``).
Historically the SSE endpoint opened *one dedicated asyncpg connection per
connected client* and held it LISTENing for the stream's lifetime — so every
open browser tab (and, under the e2e suite's rapid navigation, every stale
not-yet-reaped stream) consumed a Postgres connection. Those crossed Postgres's
``max_connections`` and surfaced as ``TooManyConnectionsError`` 500s on *any*
authenticated request (``current_user`` runs on the whole ``/api`` surface).

This broker replaces that with a single shared connection per process: it
LISTENs once for the process lifetime and fans each payload out to an in-process
``asyncio.Queue`` per client. SSE Postgres usage is now capped at exactly one
connection regardless of how many clients are streaming.
"""

import asyncio
import contextlib
import logging

import asyncpg

from library.jobs import EVENTS_CHANNEL

logger = logging.getLogger(__name__)

# Per-client queue bound. One slow/stalled consumer must never grow memory
# without limit or block the relay: when a client's queue is full we drop its
# oldest event (it only loses live updates; the pipeline state is in the DB).
_CLIENT_QUEUE_MAXSIZE = 100

# Reconnect backoff bounds for a dropped LISTEN connection.
_RECONNECT_MIN_DELAY = 1.0
_RECONNECT_MAX_DELAY = 30.0


class EventsBroker:
    """One shared Postgres LISTEN connection fanned out to many SSE clients.

    Lifecycle is owned by the FastAPI lifespan: ``start()`` on startup opens the
    shared connection and begins LISTENing; ``stop()`` on shutdown tears it down.
    Each SSE request calls ``register()`` to get its own bounded queue and
    ``unregister()`` (in a ``finally``) on disconnect — neither touches Postgres.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._connection: asyncpg.Connection | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._closing = False
        self._reconnect_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open the shared LISTEN connection and begin relaying. Idempotent.

        Resilient by design: a connect failure at startup (e.g. the database is
        not reachable yet) is logged and a background reconnect is scheduled
        rather than raised, so a transient outage never blocks app startup. SSE
        clients can still register; they just receive no live events until the
        connection comes up.
        """
        self._closing = False
        if self._connection is not None:
            return
        try:
            await self._connect()
            logger.info("events broker listening on %s", EVENTS_CHANNEL)
        except Exception:
            logger.warning(
                "events broker could not open LISTEN connection at startup; "
                "will keep retrying in the background",
                exc_info=True,
            )
            self._reconnect_task = asyncio.ensure_future(self._reconnect())

    async def stop(self) -> None:
        """Close the shared connection, cancel any reconnect, drop subscribers."""
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                await connection.remove_listener(EVENTS_CHANNEL, self._on_notify)
            except Exception:  # already-dead connection: nothing to remove
                logger.debug("events broker: remove_listener on shutdown failed", exc_info=True)
            finally:
                await connection.close()
        self._subscribers.clear()
        logger.info("events broker stopped")

    async def _connect(self) -> None:
        connection = await asyncpg.connect(self._dsn)
        await connection.add_listener(EVENTS_CHANNEL, self._on_notify)
        connection.add_termination_listener(self._on_termination)
        self._connection = connection

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        """asyncpg NOTIFY callback: fan one payload out to every client queue.

        Synchronous and non-blocking so it never stalls the LISTEN connection.
        A full queue means that one client is slow: drop its oldest event and
        enqueue the new one, isolating the slowness to that client.
        """
        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):  # racing put/get
                    queue.get_nowait()
            queue.put_nowait(payload)

    def _on_termination(self, _conn: object) -> None:
        """asyncpg connection-lost callback: log and schedule a reconnect."""
        if self._closing:
            return
        logger.warning("events broker LISTEN connection dropped; reconnecting")
        self._connection = None
        self._reconnect_task = asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        delay = _RECONNECT_MIN_DELAY
        while not self._closing and self._connection is None:
            try:
                await self._connect()
                logger.info("events broker reconnected to %s", EVENTS_CHANNEL)
                return
            except Exception:
                logger.warning(
                    "events broker reconnect failed; retrying in %.0fs", delay, exc_info=True
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    def register(self) -> asyncio.Queue[str]:
        """Register a new SSE client and return its bounded payload queue."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue[str]) -> None:
        """Drop a disconnected client's queue (no effect on the shared conn)."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """Number of currently registered SSE clients."""
        return len(self._subscribers)

    @property
    def running(self) -> bool:
        """True while the shared LISTEN connection is open."""
        return self._connection is not None
