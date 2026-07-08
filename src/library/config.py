"""Application settings, loaded from the environment with the LIBRARY_ prefix."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from library.extraction.pricing import MODEL_PRICING_USD_PER_MTOK

# The ``*_model`` knobs whose value must have a row in
# ``MODEL_PRICING_USD_PER_MTOK`` — an unpriced model would silently record a
# cost of 0 and defeat the daily-spend budget gate, so we fail fast at startup.
_PRICED_MODEL_FIELDS: tuple[str, ...] = (
    "extraction_model",
    "extraction_escalation_model",
    "extraction_judge_model",
    "markdown_model",
    "ask_model",
    "email_label_model",
)


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
    # Admin views (see docs/admin.md). Build metadata is injected at image
    # build time; coverage_summary_path points at a JSON file the CI build
    # bakes into the image (absent in dev → the admin coverage view reports
    # "unavailable").
    git_sha: str | None = None
    coverage_summary_path: Path = Path("coverage-summary.json")
    # Markdown docs surfaced read-only in the admin Architecture view. Baked
    # into the image (Dockerfile COPYs docs/); absent in some contexts → the
    # view lists whatever is present and degrades gracefully.
    docs_dir: Path = Path("docs")
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
    ask_model: str = "claude-opus-4-8"
    ask_max_tool_turns: int = 4
    ask_max_answer_tokens: int = 1024
    ask_history_turns: int = 3  # prior turns re-fed into the loop; 0 disables.
    ask_get_document_max_chars: int = 8000  # cap on get_document's returned text
    # Foreign-exchange rate seeding (see docs/admin.md, "FX rates"). The admin
    # "Fetch rate" affordance calls this keyless provider for the live USD-per-unit
    # rate; open.er-api.com returns USD->X rates, inverted to rate_to_base(X).
    fx_api_url: str = "https://open.er-api.com/v6/latest"
    fx_api_timeout_s: float = 10.0
    # Document series + comparative queries (see docs/ask.md, "Document series").
    series_min_documents: int = 3  # min members before stats are reported
    series_typical_pct: float = 0.10  # half-width of the "typical" band vs median
    series_flat_pct: float = 0.05  # |first→last change| at/below which trend is flat
    # Authored-series auto-continue (propose-for-review). A newly-indexed
    # document mechanically matching an authored series' dominant
    # (sender, kind, currency) signature is recorded as a pending suggestion.
    series_autocontinue_enabled: bool = True
    series_autocontinue_min_dominance: float = 0.6  # min signature dominance to match
    series_suggestion_limit: int = 20  # cap on suggested matches returned/considered
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
    # Deterministic noise gate for forwarded-email attachments (see
    # docs/ingestion.md, "Email item selection"). Filters inline signature
    # logos, tracking pixels / icons, and non-document parts (calendar, vCard,
    # PKCS7 signatures, TNEF) before they become documents — recorded in the
    # decision trace, not surfaced. The thresholds are deliberately conservative
    # (bias to ingest); a decode failure never drops. Set enabled=False to ingest
    # every attachment exactly as before.
    email_filter_noise_enabled: bool = True
    email_filter_tiny_image_max_bytes: int = 4096  # images smaller than this are icons/pixels
    email_filter_tiny_image_max_edge_px: int = 64  # or whose longest edge is <= this
    # Optional per-email LLM label pass (see docs/ingestion.md, "Email item
    # selection"). One Anthropic call per email classifies each surviving
    # attachment keep|probably_noise; a probably_noise verdict flags the document
    # needs_review (it is never dropped). Default OFF — it spends money; enabling
    # it also requires anthropic_api_key. Spend is budget-gated like extraction.
    email_label_enabled: bool = False
    email_label_model: str = "claude-haiku-4-5"
    email_label_daily_budget_usd: float = 2.0
    email_label_body_snippet_chars: int = 1000  # body context sent to the labeller
    # Background worker (see docs/ingestion.md, "Job queue").
    # Concurrency: how many jobs the Procrastinate worker runs at once. Default 1
    # (serial) — raising it multiplies peak worker RAM (parallel OCR subprocesses
    # + embedder batches), so tune only with host headroom to spare.
    worker_concurrency: int = 1
    # Crash recovery: a hard-killed worker strands its in-flight process_document
    # job in `doing`. sweep_stalled_jobs re-enqueues such jobs every
    # ``stalled_job_sweep_minutes`` (0 disables) once their worker's heartbeat is
    # older than ``stalled_job_heartbeat_seconds`` (kept well above Procrastinate's
    # ~10 s heartbeat so a live worker mid-stage is never swept).
    stalled_job_sweep_minutes: int = 5
    stalled_job_heartbeat_seconds: float = 60.0
    # How long a dead worker's row survives before Procrastinate prunes it at the
    # next worker startup (``stalled_worker_timeout``). CRITICAL for recovery:
    # get_stalled_jobs finds a stranded job only while its (stale-heartbeat)
    # worker row still exists — once pruned, the orphaned ``doing`` job becomes
    # invisible and never resumes. So this must stay well above the worker's
    # crash→restart gap AND the sweep interval; the default 24 h covers any
    # realistic redeploy/reboot. The only cost of a high value is a few stale
    # rows lingering in procrastinate_workers (cosmetic).
    stalled_worker_prune_seconds: float = 86400.0
    # Daily auto-backfill of budget-skipped documents (see docs/ingestion.md,
    # "Extraction"/"Markdown layer"). When enabled, a daily task re-enqueues
    # extraction/markdown for documents that were skipped because the per-day
    # LLM budget was exhausted — the budget resets daily, so the next tick fills
    # them. Default off: it spends money, so opt in deliberately.
    budget_backfill_enabled: bool = False
    # Recently-Deleted retention (see docs/api.md, "Soft delete" section).
    # DELETE /api/documents/{id} soft-deletes (sets deleted_at); a daily worker
    # task then hard-deletes documents whose deleted_at is older than
    # ``deleted_retention_days`` (rows + cascaded children + on-disk files).
    # ``deleted_purge_enabled`` is the kill switch: off = documents stay in the
    # Recently-Deleted holding area indefinitely (restorable, never purged).
    # Guard against a negative value: it would make the purge cutoff future-dated
    # and delete *every* soft-deleted document on the next run (0 = no holding
    # period, purge on the next daily run).
    deleted_retention_days: int = Field(default=30, ge=0)
    deleted_purge_enabled: bool = True
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

    @model_validator(mode="after")
    def _require_pricing_for_configured_models(self) -> "Settings":
        """Fail fast when a configured ``*_model`` knob has no pricing row.

        Every model we call must appear in ``MODEL_PRICING_USD_PER_MTOK`` so
        its per-call cost is recorded and counts against the daily-spend budget
        gate. An unpriced model would silently cost 0 and never trip the gate,
        so we reject the configuration at startup instead.
        """
        for field in _PRICED_MODEL_FIELDS:
            value = getattr(self, field)
            if value not in MODEL_PRICING_USD_PER_MTOK:
                raise ValueError(
                    f"{field}={value!r} has no pricing row in MODEL_PRICING_USD_PER_MTOK; "
                    f"add it to library.extraction.pricing or set a priced model"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
