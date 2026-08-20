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


def test_both_backends_default_to_the_metered_api() -> None:
    """A deploy must be a no-op until credentials are deliberately provisioned."""
    settings = _settings()
    assert settings.ask_llm_backend == "api"
    assert settings.series_insight_llm_backend == "api"


def test_series_insight_default_is_independent_of_ask() -> None:
    """Per-surface knobs: the trade is genuinely different per call site.

    Ask is one large infrequent call on the priciest model; series-insight is a
    small bounded call on the cheapest model, once per ingested document. A
    single global switch would force the bad half of that trade.
    """
    settings = _settings(ask_llm_backend="subscription", claude_config_dir=Path("/"))
    assert settings.ask_llm_backend == "subscription"
    assert settings.series_insight_llm_backend == "api"


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(ask_llm_backend="subscrpition")  # typo


# --------------------------------------------------------------------------
# Fail-fast on a missing credentials mount
# --------------------------------------------------------------------------


def test_subscription_without_a_credentials_dir_is_rejected(tmp_path: Path) -> None:
    """The forgotten-mount case must fail at startup, not on the first question."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(ask_llm_backend="subscription", claude_config_dir=tmp_path / "nope")

    message = str(excinfo.value)
    assert "ask_llm_backend" in message
    assert "claude_config_dir" in message


def test_series_insight_subscription_is_validated_too(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(series_insight_llm_backend="subscription", claude_config_dir=tmp_path / "nope")
    assert "series_insight_llm_backend" in str(excinfo.value)


def test_subscription_with_a_mounted_dir_is_accepted(tmp_path: Path) -> None:
    settings = _settings(ask_llm_backend="subscription", claude_config_dir=tmp_path)
    assert settings.ask_llm_backend == "subscription"


def test_an_empty_mounted_dir_is_accepted(tmp_path: Path) -> None:
    """Recovery must stay possible.

    An operator whose refresh token was revoked re-runs `claude setup-token`
    against a mounted directory. Requiring the credentials *file* at startup
    would brick the container in exactly that window.
    """
    assert _settings(ask_llm_backend="subscription", claude_config_dir=tmp_path)


def test_api_backend_ignores_the_credentials_dir(tmp_path: Path) -> None:
    """No mount is needed when nothing uses OAuth."""
    assert _settings(claude_config_dir=tmp_path / "does-not-exist")


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
    assert "claude setup-token" in body["claude_credentials_detail"]
