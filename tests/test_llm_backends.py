"""The runtime LLM backend switch: resolution, write-time validation, API.

The invariant throughout is that the database is an *override layer*: an empty
``instance_settings`` table must behave exactly as the environment says, which
is what every deployment gets before an admin touches anything.
"""

import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings, get_settings
from library.llm import backends
from library.models import InstanceSetting


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A clean session with no backend overrides stored.

    Truncating here rather than relying on ordering: the override table is the
    thing under test, and a row surviving from a previous test would make
    "falls back to the environment" pass or fail depending on run order.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(InstanceSetting))
        await session.commit()
        yield session


def _settings(**overrides: Any) -> Settings:
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
# Resolution
# --------------------------------------------------------------------------


async def test_no_override_falls_back_to_the_environment(db_session: AsyncSession) -> None:
    """An empty table must behave exactly as the deployed configuration says."""
    settings = _settings(ask_llm_backend="subscription", series_insight_llm_backend="api")

    assert await backends.resolve_backend(db_session, "ask", settings) == "subscription"
    assert await backends.resolve_backend(db_session, "series_insight", settings) == "api"


async def test_a_stored_override_wins(db_session: AsyncSession, tmp_path: Path) -> None:
    _write_creds(tmp_path)
    settings = _settings(ask_llm_backend="api", claude_config_dir=tmp_path)

    await backends.set_backend(db_session, "ask", "subscription", settings)

    assert await backends.resolve_backend(db_session, "ask", settings) == "subscription"


async def test_clearing_an_override_restores_the_environment(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    _write_creds(tmp_path)
    settings = _settings(ask_llm_backend="api", claude_config_dir=tmp_path)
    await backends.set_backend(db_session, "ask", "subscription", settings)

    await backends.clear_backend(db_session, "ask")

    assert await backends.resolve_backend(db_session, "ask", settings) == "api"


async def test_setting_the_same_surface_twice_updates_in_place(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The upsert must not accumulate rows or raise on the primary key."""
    _write_creds(tmp_path)
    # A key too: switching back to "api" is itself validated.
    settings = _settings(claude_config_dir=tmp_path, anthropic_api_key="k")

    await backends.set_backend(db_session, "ask", "subscription", settings)
    await backends.set_backend(db_session, "ask", "api", settings)

    rows = await backends.list_overrides(db_session)
    assert len(rows) == 1
    assert await backends.resolve_backend(db_session, "ask", settings) == "api"


async def test_an_unrecognised_stored_value_degrades_to_the_default(
    db_session: AsyncSession,
) -> None:
    """A row left by a downgrade must not take the surface down."""
    db_session.add(InstanceSetting(key="llm_backend.ask", value="quantum"))
    await db_session.commit()
    settings = _settings(ask_llm_backend="api")

    assert await backends.resolve_backend(db_session, "ask", settings) == "api"


async def test_unknown_surfaces_are_rejected(db_session: AsyncSession) -> None:
    settings = _settings()
    with pytest.raises(backends.UnknownSurfaceError):
        await backends.resolve_backend(db_session, "telepathy", settings)
    with pytest.raises(backends.UnknownSurfaceError):
        await backends.set_backend(db_session, "telepathy", "api", settings)


async def test_the_writer_is_recorded(
    db_session: AsyncSession, tmp_path: Path, auth_user: Any
) -> None:
    """An operational toggle needs an audit trail."""
    _write_creds(tmp_path)
    settings = _settings(claude_config_dir=tmp_path)

    await backends.set_backend(db_session, "ask", "subscription", settings, user_id=auth_user.id)

    row = await db_session.get(InstanceSetting, "llm_backend.ask")
    assert row is not None
    assert row.updated_by_id == auth_user.id


# --------------------------------------------------------------------------
# Write-time validation — the guard that replaced the startup check
# --------------------------------------------------------------------------


async def test_enabling_the_subscription_without_credentials_is_refused(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The admin who flips the toggle is the one who should hear about it.

    Startup validation could not survive a runtime-editable setting, so the
    check moved here — where it reaches the person making the change instead of
    the next person to ask a question.
    """
    settings = _settings(claude_config_dir=tmp_path)  # mounted but empty

    with pytest.raises(backends.BackendUnavailableError, match="claude auth login"):
        await backends.set_backend(db_session, "ask", "subscription", settings)

    # And nothing was stored.
    assert await backends.list_overrides(db_session) == []


async def test_selecting_the_api_without_a_key_is_refused(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Symmetry: switching *back* to a backend that cannot authenticate is also a trap."""
    settings = _settings(anthropic_api_key=None, claude_config_dir=tmp_path)

    with pytest.raises(backends.BackendUnavailableError, match="API key"):
        await backends.set_backend(db_session, "ask", "api", settings)


async def test_an_expired_token_with_a_refresh_token_is_still_allowed(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Expiry is self-healing and must not block the toggle."""
    _write_creds(tmp_path, hours=-3)
    settings = _settings(claude_config_dir=tmp_path)

    await backends.set_backend(db_session, "ask", "subscription", settings)

    assert await backends.resolve_backend(db_session, "ask", settings) == "subscription"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_get_reports_every_surface(api_client: TestClient, with_api_key: None) -> None:
    response = api_client.get("/api/settings/llm-backends")
    assert response.status_code == 200

    body = response.json()
    surfaces = {s["surface"]: s for s in body["surfaces"]}
    assert set(surfaces) == {"ask", "series_insight"}
    assert surfaces["ask"]["backend"] == "api"  # conftest pins the suite
    assert surfaces["ask"]["overridden"] is False
    assert surfaces["ask"]["label"]
    assert surfaces["ask"]["description"]
    assert body["credentials_status"] in {"healthy", "degraded", "unhealthy"}


def test_get_never_leaks_the_api_key(api_client: TestClient, with_api_key: None) -> None:
    """The payload reports *whether* a key exists, never the key."""
    body = api_client.get("/api/settings/llm-backends").json()
    assert body["api_key_configured"] is True
    assert "test-key" not in json.dumps(body)


def test_get_requires_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/settings/llm-backends").status_code == 401


def test_non_admin_cannot_change_the_backend(api_client: TestClient, with_api_key: None) -> None:
    """Instance-wide operational config is not a per-user preference."""
    response = api_client.put("/api/settings/llm-backends/ask", json={"backend": "subscription"})
    assert response.status_code == 403


def test_non_admin_sees_editable_false(api_client: TestClient, with_api_key: None) -> None:
    """So the client renders read-only controls instead of discovering a 403."""
    assert api_client.get("/api/settings/llm-backends").json()["editable"] is False


def test_admin_can_switch_and_revert(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_creds(tmp_path)
    monkeypatch.setattr(get_settings(), "claude_config_dir", tmp_path)

    switched = admin_client.put("/api/settings/llm-backends/ask", json={"backend": "subscription"})
    assert switched.status_code == 200
    ask = {s["surface"]: s for s in switched.json()["surfaces"]}["ask"]
    assert ask["backend"] == "subscription"
    assert ask["overridden"] is True
    assert ask["default"] == "api"  # conftest's environment

    reverted = admin_client.delete("/api/settings/llm-backends/ask")
    assert reverted.status_code == 200
    ask = {s["surface"]: s for s in reverted.json()["surfaces"]}["ask"]
    assert ask["backend"] == "api"
    assert ask["overridden"] is False


def test_admin_switching_without_credentials_gets_409(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Well-formed request, legal value, server not in a state to honour it."""
    monkeypatch.setattr(get_settings(), "claude_config_dir", tmp_path)  # empty

    response = admin_client.put("/api/settings/llm-backends/ask", json={"backend": "subscription"})

    assert response.status_code == 409
    assert "claude auth login" in response.json()["detail"]


def test_unknown_surface_is_404(admin_client: TestClient) -> None:
    response = admin_client.put("/api/settings/llm-backends/telepathy", json={"backend": "api"})
    assert response.status_code == 404


def test_unknown_backend_is_422(admin_client: TestClient) -> None:
    """Schema-level rejection, before anything reaches the store."""
    response = admin_client.put(
        "/api/settings/llm-backends/ask", json={"backend": "carrier-pigeon"}
    )
    assert response.status_code == 422
