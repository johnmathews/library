"""Tests for the worker entrypoint (``library.worker``).

Focused on configuration wiring — that the Procrastinate worker is started with
the configured concurrency — not on running a real worker loop.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator

import pytest

from library import worker
from library.config import get_settings
from library.jobs import job_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_main_passes_worker_concurrency_to_run_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synchronous (no consume-dir) path forwards ``worker_concurrency``."""
    monkeypatch.delenv("LIBRARY_CONSUME_DIR", raising=False)
    monkeypatch.setenv("LIBRARY_WORKER_CONCURRENCY", "3")
    captured: dict[str, object] = {}

    def fake_run_worker(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(job_app, "open", lambda: contextlib.nullcontext())
    monkeypatch.setattr(job_app, "run_worker", fake_run_worker)

    worker.main()

    assert captured["concurrency"] == 3
    # A high prune timeout keeps a crashed worker's row alive long enough for the
    # stalled-job sweep to find and re-enqueue its orphaned job (review fix).
    assert captured["stalled_worker_timeout"] == 86400.0


def test_default_worker_concurrency_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default stays serial (1) so raising it is a deliberate, RAM-aware choice."""
    monkeypatch.delenv("LIBRARY_WORKER_CONCURRENCY", raising=False)
    assert get_settings().worker_concurrency == 1


async def test_consume_path_passes_worker_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """The consume-watcher path forwards ``worker_concurrency`` to the async worker."""
    monkeypatch.setenv("LIBRARY_WORKER_CONCURRENCY", "4")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    class FakeWatcher:
        @classmethod
        def from_settings(cls, *args: object, **kwargs: object) -> "FakeWatcher":
            return cls()

        async def run(self, stop_event: asyncio.Event) -> None:
            await stop_event.wait()

    async def fake_run_worker_async(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(worker, "ConsumeWatcher", FakeWatcher)
    monkeypatch.setattr(job_app, "open_async", lambda: _null_async_cm())
    monkeypatch.setattr(job_app, "run_worker_async", fake_run_worker_async)

    await worker.run_worker_with_consume(get_settings())

    assert captured["concurrency"] == 4
    assert captured["stalled_worker_timeout"] == 86400.0


@contextlib.asynccontextmanager
async def _null_async_cm() -> AsyncIterator[None]:
    yield
