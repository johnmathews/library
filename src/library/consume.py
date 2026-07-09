"""Consume folder watcher: ingest files dropped into a watched directory.

Anything placed in ``LIBRARY_CONSUME_DIR`` (a Syncthing-synced folder in
the primary iOS-Notes-scan flow) is ingested through the same
``ingest_file`` service as an upload (``source=consume``, no uploader).
Files may arrive incrementally (partial copies, Syncthing temp files +
renames), so each candidate must be size/mtime-stable before ingest.
Successes are archived under ``consumed/YYYY/MM/`` (or deleted),
rejections move to ``failed/``. See docs/ingestion.md, "Consume folder".

The watcher runs as an asyncio task inside the worker process (see
``library.worker``); per-file errors are caught and logged so a bad file
can never take the Procrastinate worker down with it.
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchfiles import Change, awatch

from library.config import Settings
from library.ingest import IngestError, ingest_file, resolve_owner_id
from library.models import DocumentSource

logger = logging.getLogger(__name__)

#: Extensions worth ingesting (must resolve to ALLOWED_MIME_TYPES content).
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".tif",
        ".tiff",
        ".txt",
        ".md",
        ".markdown",
    }
)

CONSUMED_DIR_NAME: str = "consumed"
FAILED_DIR_NAME: str = "failed"

#: Give up waiting for a still-growing file after this long; it is *not*
#: force-ingested (a partial copy would be stored under its own hash as a
#: junk document) — the next write event or startup sweep retries it.
DEFAULT_STABILITY_TIMEOUT_S: float = 300.0

_RELEVANT_CHANGES: frozenset[Change] = frozenset({Change.added, Change.modified})


class ConsumeWatcher:
    """Watch one directory tree and ingest every stable candidate file."""

    def __init__(
        self,
        root: Path,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stability_s: float = 3.0,
        poll_interval_s: float = 2.0,
        stability_timeout_s: float = DEFAULT_STABILITY_TIMEOUT_S,
        force_polling: bool = False,
        on_success: Literal["archive", "delete"] = "archive",
        max_bytes: int = 100 * 1024 * 1024,
        default_owner_username: str | None = None,
    ) -> None:
        self._root = root
        self._session_factory = session_factory
        self._stability_s = stability_s
        self._poll_interval_s = poll_interval_s
        self._stability_timeout_s = stability_timeout_s
        self._force_polling = force_polling
        self._on_success: Literal["archive", "delete"] = on_success
        self._max_bytes = max_bytes
        self._default_owner_username = default_owner_username
        self._in_flight: set[Path] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(
        cls, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> Self:
        """Build a watcher from application settings (consume_dir must be set)."""
        if settings.consume_dir is None:
            raise ValueError("LIBRARY_CONSUME_DIR is not configured")
        return cls(
            settings.consume_dir,
            session_factory,
            stability_s=settings.consume_stability_s,
            poll_interval_s=settings.consume_poll_interval_s,
            force_polling=settings.consume_force_polling,
            on_success=settings.consume_on_success,
            max_bytes=settings.max_upload_bytes,
            default_owner_username=settings.import_default_owner,
        )

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Sweep pre-existing files, then watch until ``stop_event`` is set."""
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info(
            "consume: watching %s (force_polling=%s, stability=%.1fs)",
            self._root,
            self._force_polling,
            self._stability_s,
        )
        await self.sweep()
        async for changes in awatch(
            self._root,
            stop_event=stop_event,
            force_polling=self._force_polling,
            poll_delay_ms=int(self._poll_interval_s * 1000),
        ):
            for change, raw_path in changes:
                path = Path(raw_path)
                if change in _RELEVANT_CHANGES and self._is_candidate(path):
                    self._schedule(path)
        if self._tasks:
            # process_path never raises, so a plain gather drains cleanly.
            await asyncio.gather(*self._tasks)
        logger.info("consume: watcher for %s stopped", self._root)

    async def sweep(self) -> None:
        """Process every candidate file already present (drop-while-down case)."""
        for path in sorted(self._root.rglob("*")):
            if self._is_candidate(path):
                logger.info("consume: sweeping pre-existing file %s", path)
                await self.process_path(path)

    async def process_path(self, path: Path) -> None:
        """Ingest one file: single-flight per path, never raises.

        Events for a path already in flight are dropped — the stability
        wait inside the in-flight run observes any further writes, and a
        skipped file is retried on its next event or the next sweep.
        """
        if path in self._in_flight:
            return
        self._in_flight.add(path)
        try:
            await self._process_one(path)
        except Exception:
            # Transient failure (database down, I/O): leave the file in
            # place; a later event or the next sweep retries it.
            logger.exception("consume: error processing %s; leaving file for retry", path)
        finally:
            self._in_flight.discard(path)

    def _schedule(self, path: Path) -> None:
        if path in self._in_flight:
            return
        task = asyncio.create_task(self.process_path(path))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _is_candidate(self, path: Path) -> bool:
        """Is this a file we should try to ingest?"""
        try:
            relative = path.relative_to(self._root)
        except ValueError:
            return False
        if relative.parts and relative.parts[0] in (CONSUMED_DIR_NAME, FAILED_DIR_NAME):
            return False
        # Dotfile components cover Syncthing's `.syncthing.<name>.tmp`
        # temps (and `.stfolder` etc.); `~syncthing~` is its legacy
        # temp prefix on filesystems that reject leading dots.
        if any(part.startswith((".", "~syncthing~")) for part in relative.parts):
            return False
        name = path.name.lower()
        if name.endswith(".part"):
            return False
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return False
        return path.is_file()

    async def _process_one(self, path: Path) -> None:
        if not await self._wait_until_stable(path):
            logger.info("consume: %s still changing or gone; will retry later", path)
            return
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return  # renamed away under us (e.g. Syncthing temp promotion)
        if len(content) > self._max_bytes:
            target = self._move_into(path, self._root / FAILED_DIR_NAME)
            logger.warning(
                "consume: %s is %d bytes (limit %d); moved to %s",
                path,
                len(content),
                self._max_bytes,
                target,
            )
            return
        try:
            async with self._session_factory() as session:
                owner_id = await resolve_owner_id(session, self._default_owner_username)
                result = await ingest_file(
                    session,
                    content=content,
                    filename=path.name,
                    source=DocumentSource.CONSUME,
                    uploader_id=owner_id,
                )
        except IngestError as exc:
            # Content-level rejection (unsupported MIME, soft-deleted
            # duplicate): retrying cannot help, so park it in failed/.
            # No ingestion event exists — these paths never create a
            # document row and events require one (see docs/ingestion.md).
            target = self._move_into(path, self._root / FAILED_DIR_NAME)
            logger.warning("consume: %s rejected (%s); moved to %s", path, exc, target)
            return
        self._finish(path)
        logger.info(
            "consume: ingested %s as document %s (duplicate=%s)",
            path,
            result.document.id,
            result.duplicate,
        )

    async def _wait_until_stable(self, path: Path) -> bool:
        """True once size+mtime are unchanged for ``stability_s`` seconds.

        False when the file disappears or is still changing after
        ``stability_timeout_s`` — the caller skips it (never ingest a
        growing file: a truncated copy would be stored under its own
        content hash) and a later event or sweep retries.
        """
        deadline = time.monotonic() + self._stability_timeout_s
        previous = self._snapshot(path)
        if previous is None:
            return False
        while True:
            await asyncio.sleep(self._stability_s)
            current = self._snapshot(path)
            if current is None:
                return False
            if current == previous:
                return True
            if time.monotonic() >= deadline:
                return False
            previous = current

    @staticmethod
    def _snapshot(path: Path) -> tuple[int, float] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime)

    def _finish(self, path: Path) -> None:
        """Apply the on-success policy: archive to consumed/YYYY/MM or delete."""
        if self._on_success == "delete":
            path.unlink(missing_ok=True)
            return
        now = datetime.now(tz=UTC)
        target_dir = self._root / CONSUMED_DIR_NAME / f"{now.year:04d}" / f"{now.month:02d}"
        self._move_into(path, target_dir)

    @staticmethod
    def _move_into(path: Path, target_dir: Path) -> Path:
        """Move a file into a directory, suffixing the name on collision."""
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        counter = 1
        while target.exists():
            target = target_dir / f"{path.stem}-{counter}{path.suffix}"
            counter += 1
        os.replace(path, target)  # same filesystem: atomic rename
        return target
