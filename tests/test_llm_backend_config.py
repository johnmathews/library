"""Tests for the LLM backend switch in ``Settings`` and its /healthz surface."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from library.config import Settings


def _settings(**overrides: Any) -> Settings:
    """Build Settings without reading the developer's real environment."""
    base: dict[str, Any] = {"database_url": "postgresql+asyncpg://u:p@localhost/db"}
    return Settings(_env_file=None, **{**base, **overrides})


def _write_creds(config_dir: Path, *, hours: float = 5.0, refresh: str | None = "r") -> None:
    block: dict[str, Any] = {
        "accessToken": "a",
        "expiresAt": int((time.time() + hours * 3600) * 1000),
    }
    if refresh is not None:
        block["refreshToken"] = refresh
    (config_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": block}))


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_ask_backend_defaults_to_the_metered_api() -> None:
    """A deploy must be a no-op until credentials are deliberately provisioned."""
    settings = _settings()
    assert settings.ask_llm_backend == "api"


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(ask_llm_backend="subscrpition")  # typo


# --------------------------------------------------------------------------
# Defaults are only defaults
# --------------------------------------------------------------------------


def test_ask_defaults_to_the_subscription() -> None:
    """The shipped default.

    Read off the field rather than an instance: conftest pins the environment
    to "api" for the whole suite (so no test can accidentally bill a real
    subscription), which would mask the declared default here.
    """
    assert Settings.model_fields["ask_llm_backend"].default == "subscription"


def test_a_missing_credentials_dir_does_not_block_startup(tmp_path: Path) -> None:
    """Startup must not validate a value that can change without a restart.

    An earlier version failed fast here. That guard became wrong once the
    backend was runtime-editable: the setting can become "subscription" long
    after boot, so a startup check both misses the real case and bricks a
    container whose credentials are provisioned a moment later. The guard now
    lives at write time (test_llm_backends.py) and on /healthz (below).
    """
    settings = _settings(ask_llm_backend="subscription", claude_config_dir=tmp_path / "not-mounted")
    assert settings.ask_llm_backend == "subscription"


# --------------------------------------------------------------------------
# /healthz
# --------------------------------------------------------------------------


def test_healthz_omits_credentials_on_the_api_backend(client: Any) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "claude_credentials" not in body


def test_healthz_reports_healthy_credentials(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.config import get_settings

    _write_creds(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "ask_llm_backend", "subscription")
    monkeypatch.setattr(settings, "claude_config_dir", tmp_path)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert body["claude_credentials"] == "healthy"


def test_healthz_degrades_when_credentials_need_a_human(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead credentials mount must not report "ok" while every question fails."""
    from library.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ask_llm_backend", "subscription")
    monkeypatch.setattr(settings, "claude_config_dir", tmp_path)  # no credentials file

    body = client.get("/healthz").json()

    assert body["status"] == "degraded"
    assert body["claude_credentials"] == "unhealthy"
    assert "claude auth login" in body["claude_credentials_detail"]


def test_healthz_reports_credentials_whenever_they_exist(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The alarm must not depend on the environment default.

    The live backend is an instance setting resolved from the database, but
    /healthz is deliberately DB-free — so keying the check on
    ``settings.ask_llm_backend`` meant that enabling the subscription through
    the Settings UI (the intended path) left the credential alarm permanently
    silent. Observed in production: Ask running on the subscription while
    /healthz reported nothing at all about credentials.

    Presence of the credentials file is the right trigger: they are on disk to
    be used, and the write-time guard means no surface can be switched to
    ``subscription`` without them.
    """
    from library.config import get_settings

    settings = get_settings()
    assert settings.ask_llm_backend == "api"  # the environment default, as in prod
    monkeypatch.setattr(settings, "claude_config_dir", tmp_path)
    _write_creds(tmp_path)

    body = client.get("/healthz").json()

    assert body["claude_credentials"] == "healthy"


def test_healthz_stays_quiet_with_no_credentials_and_no_subscription(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment that never uses OAuth must not carry a permanent warning."""
    from library.config import get_settings

    monkeypatch.setattr(get_settings(), "claude_config_dir", tmp_path)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert "claude_credentials" not in body
