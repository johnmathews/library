"""Unit tests for the content-addressed storage service."""

import hashlib
from pathlib import Path

import pytest

from library.storage import derived_dir, open_original, path_for, store

CONTENT = b"%PDF-1.4 fake invoice"
SHA = hashlib.sha256(CONTENT).hexdigest()


def test_store_layout_and_result(tmp_path: Path) -> None:
    result = store(CONTENT, data_dir=tmp_path)
    assert result.sha256 == SHA
    assert result.created is True
    assert result.path == tmp_path / "originals" / SHA[0:2] / SHA[2:4] / SHA
    assert result.path.read_bytes() == CONTENT


def test_store_is_idempotent(tmp_path: Path) -> None:
    first = store(CONTENT, data_dir=tmp_path)
    second = store(CONTENT, data_dir=tmp_path)
    assert first.sha256 == second.sha256 == SHA
    assert first.path == second.path
    assert second.created is False
    assert second.path.read_bytes() == CONTENT
    # Exactly one file in the originals tree.
    files = [p for p in (tmp_path / "originals").rglob("*") if p.is_file()]
    assert files == [first.path]


def test_path_for_matches_store(tmp_path: Path) -> None:
    result = store(CONTENT, data_dir=tmp_path)
    assert path_for(SHA, data_dir=tmp_path) == result.path


def test_path_for_rejects_bad_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        path_for("not-a-digest", data_dir=tmp_path)
    with pytest.raises(ValueError):
        path_for("../" * 21 + "etc/passwd", data_dir=tmp_path)


def test_open_original_round_trip(tmp_path: Path) -> None:
    store(CONTENT, data_dir=tmp_path)
    with open_original(SHA, data_dir=tmp_path) as handle:
        assert handle.read() == CONTENT


def test_open_original_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_original("0" * 64, data_dir=tmp_path)


def test_derived_dir_layout_and_creation(tmp_path: Path) -> None:
    directory = derived_dir(SHA, data_dir=tmp_path)
    assert directory == tmp_path / "derived" / SHA[0:2] / SHA[2:4] / SHA
    assert directory.is_dir()
    # Calling again is fine and returns the same path.
    assert derived_dir(SHA, data_dir=tmp_path) == directory
