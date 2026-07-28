"""Tests for application settings."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from library.config import Settings, get_settings

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


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


def test_extraction_vision_density_threshold_default() -> None:
    # Calibration: prod thin scans ran 321-460 chars/page (must trigger);
    # scanned letters ~2700 and contracts ~10000 must not. See docs/ingestion.md.
    assert Settings(_env_file=None).extraction_vision_min_chars_per_page == 800


def test_extraction_vision_density_threshold_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIBRARY_EXTRACTION_VISION_MIN_CHARS_PER_PAGE", "0")
    settings = Settings(_env_file=None)
    assert settings.extraction_vision_min_chars_per_page == 0


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


def test_deleted_retention_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.deleted_retention_days == 30
    assert settings.deleted_purge_enabled is True


def test_deleted_retention_days_rejects_negative() -> None:
    # A negative retention would future-date the purge cutoff and delete every
    # soft-deleted document on the next run — the ge=0 bound forbids it.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deleted_retention_days=-1)


def test_pdf_unlock_passwords_default() -> None:
    """No password may ship as a committed default.

    This previously defaulted to a real four-digit personal document password,
    in a public repository. Empty means only the empty password is tried, so a
    genuinely encrypted PDF is rejected with a clear PdfLockedError rather than
    being opened with a credential nobody configured.

    ``_env_file=None`` matters: without it a developer's real ``.env`` is read
    and the assertion becomes about their machine rather than the default.
    """
    assert Settings(_env_file=None).pdf_unlock_passwords == []


def test_pdf_unlock_passwords_env_split_is_case_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Comma-separated, whitespace-trimmed, blanks dropped, case preserved
    # (unlike email senders, passwords must not be lowercased).
    monkeypatch.setenv("LIBRARY_PDF_UNLOCK_PASSWORDS", "openSesame, Hunter2 ,, letmeIN")
    assert Settings(_env_file=None).pdf_unlock_passwords == ["openSesame", "Hunter2", "letmeIN"]


def test_env_example_documents_every_setting() -> None:
    """`.env.example` must list every setting the app reads.

    `.env.example:3-4` promises "every setting the application reads
    (src/library/config.py) is listed here with its default", and 21 were
    missing — including the whole Recently-Deleted purge lifecycle and the
    Smart Groups thresholds. An operator reading the file could not discover
    them, which is the same class of defect as an undocumented flag.

    A bare subset check with **no exemption list**, deliberately. The three
    build-injected settings (`git_sha`, `docs_dir`, `coverage_summary_path`) are
    documented under an explicit "do not set" heading rather than exempted here:
    they are real `LIBRARY_`-prefixed fields an operator can set and break, so
    the alternative — narrowing the file's claim and carrying an
    `_INTERNAL_FIELDS` set in this test — would trade one unenforced list for
    another.
    """
    documented = {
        match.group(1)
        for match in re.finditer(
            r"^#?\s*(LIBRARY_[A-Z0-9_]+)\s*=", _ENV_EXAMPLE.read_text(), re.MULTILINE
        )
    }
    expected = {f"LIBRARY_{name.upper()}" for name in Settings.model_fields}

    missing = expected - documented
    assert missing == set(), (
        f".env.example does not document {len(missing)} setting(s): {sorted(missing)}"
    )
    # The other direction: a renamed or deleted setting leaves a stale line that
    # tells operators to set something the app no longer reads.
    stray = documented - expected
    assert stray == set(), f".env.example documents unknown setting(s): {sorted(stray)}"


def test_env_example_has_no_live_values() -> None:
    """Every line must stay commented out.

    `.env.example` is copied to `.env` and is also read by docker-compose for
    `${...}` interpolation, so a live line silently becomes the deployed default.
    One was: `LIBRARY_PUBLIC_BASE_URL=https://library.example.com`, which pointed
    every Pushover deep-link at a domain the operator does not own *and*
    suppressed the startup warning that would have said so.
    """
    live = [
        line
        for line in _ENV_EXAMPLE.read_text().splitlines()
        if re.match(r"^\s*LIBRARY_[A-Z0-9_]+\s*=", line)
    ]
    assert live == [], f"uncommented assignments in .env.example: {live}"
