"""Tests for application settings."""

from pathlib import Path

import pytest

from library.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://library:library@db:5432/library"
    assert settings.data_dir == Path("/data")
    assert settings.environment == "production"


def test_env_prefix_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_ENVIRONMENT", "test")
    monkeypatch.setenv("LIBRARY_DATA_DIR", "/tmp/library-data")
    settings = Settings()
    assert settings.environment == "test"
    assert settings.data_dir == Path("/tmp/library-data")


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
