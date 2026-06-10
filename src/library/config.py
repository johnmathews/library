"""Application settings, loaded from the environment with the LIBRARY_ prefix."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Library backend."""

    model_config = SettingsConfigDict(env_prefix="LIBRARY_")

    database_url: str = "postgresql+asyncpg://library:library@db:5432/library"
    data_dir: Path = Path("/data")
    environment: str = "production"
    max_upload_bytes: int = 100 * 1024 * 1024
    # Auth (see docs/api.md §1.9).
    session_ttl_days: int = 30
    cookie_secure: bool = True
    # OCR (see docs/ingestion.md, "OCR" section).
    ocr_languages: str = "nld+eng"
    ocr_confidence_threshold: float = 65.0
    text_layer_min_chars_per_page: int = 50
    # Claude metadata extraction (see docs/ingestion.md, "Extraction" section).
    anthropic_api_key: SecretStr | None = None
    extraction_enabled: bool = True
    extraction_model: str = "claude-haiku-4-5"
    extraction_escalation_model: str = "claude-sonnet-4-6"
    extraction_daily_budget_usd: float = 5.0
    # Consume folder watcher (see docs/ingestion.md, "Consume folder" section).
    consume_dir: Path | None = None  # unset = watcher off
    consume_force_polling: bool = False  # required for NFS/SMB mounts (no inotify)
    consume_poll_interval_s: float = 2.0
    consume_stability_s: float = 3.0
    consume_on_success: Literal["archive", "delete"] = "archive"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
