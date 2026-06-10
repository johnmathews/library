"""Application settings, loaded from the environment with the LIBRARY_ prefix."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Library backend."""

    model_config = SettingsConfigDict(env_prefix="LIBRARY_")

    database_url: str = "postgresql+asyncpg://library:library@db:5432/library"
    data_dir: Path = Path("/data")
    environment: str = "production"
    max_upload_bytes: int = 100 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
