"""Procrastinate worker entrypoint: ``python -m library.worker``.

Runs the worker programmatically against the job app defined in
``library.jobs`` (this is the command of the ``worker`` service in
docker-compose). When ``LIBRARY_CONSUME_DIR`` is set, the consume
folder watcher (``library.consume``) runs as a sibling asyncio task in
the same process; unset, behavior is unchanged.
"""

import asyncio
import contextlib
import logging

from library.config import Settings, get_settings
from library.consume import ConsumeWatcher
from library.db import get_sessionmaker
from library.jobs import job_app

logger = logging.getLogger(__name__)


def _log_watcher_exit(task: asyncio.Task[None]) -> None:
    """Surface a watcher crash immediately (the worker itself keeps running)."""
    if task.cancelled() or task.exception() is None:
        return
    logger.error("consume watcher crashed", exc_info=task.exception())


async def run_worker_with_consume(settings: Settings) -> None:
    """Run the Procrastinate worker and the consume watcher together.

    The watcher catches per-file errors internally; if it crashes
    outright the failure is logged and the worker keeps consuming jobs.
    When the worker stops (signal/cancellation), the watcher is asked to
    stop via its stop event and awaited for a clean shutdown.
    """
    watcher = ConsumeWatcher.from_settings(settings, get_sessionmaker())
    stop_event = asyncio.Event()
    async with job_app.open_async():
        watcher_task = asyncio.create_task(watcher.run(stop_event))
        watcher_task.add_done_callback(_log_watcher_exit)
        try:
            await job_app.run_worker_async(
                concurrency=settings.worker_concurrency,
                stalled_worker_timeout=settings.stalled_worker_prune_seconds,
            )
        finally:
            stop_event.set()
            # A watcher crash was already logged by the done callback.
            with contextlib.suppress(Exception):
                await watcher_task


def main() -> None:
    """Open the job app and run the worker (plus watcher) until interrupted."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    if settings.consume_dir is None:
        with job_app.open():
            job_app.run_worker(
                concurrency=settings.worker_concurrency,
                stalled_worker_timeout=settings.stalled_worker_prune_seconds,
            )
        return
    asyncio.run(run_worker_with_consume(settings))


if __name__ == "__main__":
    main()
