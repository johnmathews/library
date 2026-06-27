"""Application settings, loaded from the environment with the LIBRARY_ prefix."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Library backend."""

    model_config = SettingsConfigDict(env_prefix="LIBRARY_")

    database_url: str = "postgresql+asyncpg://library:library@db:5432/library"
    data_dir: Path = Path("/data")
    environment: str = "production"
    max_upload_bytes: int = 100 * 1024 * 1024
    # Public base URL of the web app (e.g. https://library.example.com), used
    # to deep-link push notifications back to a document. Unset = no link.
    public_base_url: str | None = None
    # Built Vue SPA (docs/deployment.md §1.3). Relative to the working
    # directory: resolves to the baked-in build in the Docker image (/app/
    # frontend/dist) and to the local build in a checkout. If the directory
    # has no index.html the API simply does not serve a frontend (dev mode:
    # the Vite dev server proxies /api instead).
    frontend_dist: Path = Path("frontend/dist")
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
    extraction_validation_ocr_floor: float = 50.0
    extraction_judge_model: str = "claude-sonnet-4-6"
    extraction_judge_inline: bool = False  # reserved; judge is batch-only this phase
    # Markdown layer (see docs/ingestion.md, "Markdown layer" section).
    markdown_enabled: bool = True
    markdown_model: str = "claude-haiku-4-5"
    markdown_daily_budget_usd: float = 5.0
    markdown_max_pages: int = 20
    markdown_page_batch: int = 10
    markdown_image_long_side_px: int = 1600
    # Semantic search / embeddings (see docs/ask.md). The embedder is a local
    # text-embeddings-inference sidecar serving bge-m3 (1024-dim); document
    # text never leaves the host for indexing.
    embedding_enabled: bool = True
    embedding_service_url: str = "http://embedder:80"
    embedding_model_name: str = "bge-m3"
    embedding_batch_size: int = 32
    embedding_timeout_s: float = 60.0
    embedding_chunk_chars: int = 1800
    embedding_chunk_overlap: int = 200
    retrieve_top_k: int = 10
    retrieve_chunks_per_doc: int = 3  # passages per doc fed to Ask; 1 = legacy single-chunk
    # Natural-language /ask answering (see docs/ask.md). Cost is recorded per
    # turn in ask_turns but not gated in this release.
    ask_model: str = "claude-sonnet-4-6"
    ask_max_tool_turns: int = 4
    ask_max_answer_tokens: int = 1024
    ask_history_turns: int = 3  # prior turns re-fed into the loop; 0 disables.
    # Document series + comparative queries (see docs/ask.md, "Document series").
    series_min_documents: int = 3  # min members before stats are reported
    series_typical_pct: float = 0.10  # half-width of the "typical" band vs median
    series_flat_pct: float = 0.05  # |first→last change| at/below which trend is flat
    # Consume folder watcher (see docs/ingestion.md, "Consume folder" section).
    consume_dir: Path | None = None  # unset = watcher off
    consume_force_polling: bool = False  # required for NFS/SMB mounts (no inotify)
    consume_poll_interval_s: float = 2.0
    consume_stability_s: float = 3.0
    consume_on_success: Literal["archive", "delete"] = "archive"
    # Email-in ingestion (see docs/ingestion.md, "Email-in" section).
    email_host: str | None = None  # unset = poller off
    email_port: int = 993
    email_username: SecretStr | None = None
    email_password: SecretStr | None = None
    email_folder: str = "INBOX"
    email_processed_folder: str = "Library/Processed"
    email_poll_minutes: int = 10
    # Comma-separated in the env; empty = accept mail from any sender.
    email_allowed_senders: Annotated[list[str], NoDecode] = []
    # Username that owns email-ingested documents whose sender matches no user's
    # forwarding addresses. Unset = such documents stay unowned (and notify no one).
    email_default_owner: str | None = None
    # paperless-ngx importer (see docs/migration.md).
    paperless_url: str | None = None
    paperless_token: SecretStr | None = None

    @field_validator("email_allowed_senders", mode="before")
    @classmethod
    def _split_allowed_senders(cls, value: object) -> object:
        """Parse the comma-separated env value; normalise addresses to lowercase."""
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
