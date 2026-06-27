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


def test_markdown_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.markdown_enabled is True
    assert settings.markdown_model == "claude-haiku-4-5"
    assert settings.markdown_daily_budget_usd == 5.0
    assert settings.markdown_max_pages == 20
    assert settings.markdown_page_batch == 10
    assert settings.markdown_image_long_side_px == 1600


def test_markdown_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_MARKDOWN_ENABLED", "false")
    monkeypatch.setenv("LIBRARY_MARKDOWN_MAX_PAGES", "5")
    settings = Settings(_env_file=None)
    assert settings.markdown_enabled is False
    assert settings.markdown_max_pages == 5


def test_ask_history_turns_default() -> None:
    from library.config import Settings

    assert Settings().ask_history_turns == 3


def test_series_defaults() -> None:
    from library.config import Settings

    settings = Settings()
    assert settings.series_min_documents == 3
    assert settings.series_typical_pct == 0.10
    assert settings.series_flat_pct == 0.05


def test_retrieve_chunks_per_doc_default() -> None:
    assert Settings().retrieve_chunks_per_doc == 3
