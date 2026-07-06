"""Content-addressed file storage.

Originals live at ``{data_dir}/originals/<sha[0:2]>/<sha[2:4]>/<sha256>``
with no extension (mime type and original filename live in the database).
Derived artifacts (conversions, thumbnails, searchable PDFs, ...) live under
``{data_dir}/derived/<sha[0:2]>/<sha[2:4]>/<sha256>/``.

Writes are atomic (temporary file in the target directory + ``os.replace``)
and idempotent: storing already-present content is a no-op.
"""

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, NamedTuple

from library.config import get_settings

_SHA256_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")


class StoreResult(NamedTuple):
    """Outcome of storing content: its digest, on-disk path, and whether it was new."""

    sha256: str
    path: Path
    created: bool


def _validate_sha256(sha256: str) -> str:
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"not a sha256 hex digest: {sha256!r}")
    return sha256


def _resolve_data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else get_settings().data_dir


def path_for(sha256: str, *, data_dir: Path | None = None) -> Path:
    """The content-addressed path of an original (whether or not it exists)."""
    digest = _validate_sha256(sha256)
    root = _resolve_data_dir(data_dir)
    return root / "originals" / digest[0:2] / digest[2:4] / digest


def derived_path(sha256: str, *, data_dir: Path | None = None) -> Path:
    """The derived-artifacts directory of a document (whether or not it exists).

    Read paths (e.g. "does this document have a thumbnail?") use this to
    avoid creating empty directories as a side effect.
    """
    digest = _validate_sha256(sha256)
    root = _resolve_data_dir(data_dir)
    return root / "derived" / digest[0:2] / digest[2:4] / digest


def derived_dir(sha256: str, *, data_dir: Path | None = None) -> Path:
    """Create (if needed) and return the derived-artifacts directory for a document."""
    directory = derived_path(sha256, data_dir=data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def store(content: bytes, *, data_dir: Path | None = None) -> StoreResult:
    """Persist content under its sha256; idempotent for already-stored bytes."""
    digest = hashlib.sha256(content).hexdigest()
    target = path_for(digest, data_dir=data_dir)
    if target.exists():
        return StoreResult(digest, target, created=False)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file in the same directory, then atomically rename:
    # a crash mid-write never leaves a truncated file under the final name.
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{digest}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_name, target)
    except BaseException:
        os.unlink(tmp_name)
        raise
    return StoreResult(digest, target, created=True)


def open_original(sha256: str, *, data_dir: Path | None = None) -> BinaryIO:
    """Open a stored original for reading; raises FileNotFoundError if absent."""
    return path_for(sha256, data_dir=data_dir).open("rb")


def remove(sha256: str, *, data_dir: Path | None = None) -> None:
    """Delete a document's stored original and every derived artifact.

    Idempotent: absent files/directories are ignored, so a partially-purged or
    never-materialised document deletes cleanly. Safe to call per document
    because ``documents.sha256`` is unique — exactly one row ever references
    these paths, so removing them can never affect another document.
    """
    path_for(sha256, data_dir=data_dir).unlink(missing_ok=True)
    shutil.rmtree(derived_path(sha256, data_dir=data_dir), ignore_errors=True)
