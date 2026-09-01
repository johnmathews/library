"""Tests for subscription OAuth credential refresh (``library.llm.oauth``)."""

import json
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from library.llm import oauth


@pytest.fixture(autouse=True)
def _reset_rejection_state() -> Iterator[None]:
    """The rejection flag is process-global; keep tests independent of order."""
    oauth.reset_rejection_state()
    yield
    oauth.reset_rejection_state()


def _write_creds(
    config_dir: Path,
    *,
    expires_in_hours: float,
    refresh_token: str | None = "refresh-abc",
    extra: dict[str, Any] | None = None,
) -> Path:
    block: dict[str, Any] = {
        "accessToken": "access-old",
        "expiresAt": int((time.time() + expires_in_hours * 3600) * 1000),
        **(extra or {}),
    }
    if refresh_token is not None:
        block["refreshToken"] = refresh_token
    path = oauth.credentials_path(config_dir)
    path.write_text(json.dumps({"claudeAiOauth": block}))
    return path


def _stub_post(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, calls: list[dict[str, Any]]
) -> None:
    """Replace httpx.AsyncClient.post with a recorder returning ``response``."""

    async def fake_post(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://x"))


def _err(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x"))


# --------------------------------------------------------------------------
# Refresh decisions
# --------------------------------------------------------------------------


async def test_valid_token_is_not_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token with hours left must not spend a single-use refresh token."""
    _write_creds(tmp_path, expires_in_hours=5)
    calls: list[dict[str, Any]] = []
    _stub_post(monkeypatch, _ok({}), calls)

    await oauth.ensure_valid_token(tmp_path)

    assert calls == []


async def test_token_inside_buffer_is_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh fires *before* expiry, so a token cannot lapse mid-query."""
    _write_creds(tmp_path, expires_in_hours=0.05)  # ~3 min — inside the 5 min buffer
    calls: list[dict[str, Any]] = []
    _stub_post(monkeypatch, _ok({"access_token": "access-new", "expires_in": 28800}), calls)

    await oauth.ensure_valid_token(tmp_path)

    assert len(calls) == 1
    assert calls[0]["json"]["grant_type"] == "refresh_token"
    assert calls[0]["json"]["refresh_token"] == "refresh-abc"


async def test_refresh_persists_new_tokens_and_preserves_other_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rotated refresh token must land on disk, or the credentials die."""
    path = _write_creds(
        tmp_path, expires_in_hours=-1, extra={"subscriptionType": "max", "scopes": ["a", "b"]}
    )
    _stub_post(
        monkeypatch,
        _ok(
            {
                "access_token": "access-new",
                "refresh_token": "refresh-rotated",
                "expires_in": 28800,
            }
        ),
        [],
    )

    await oauth.ensure_valid_token(tmp_path)

    block = json.loads(path.read_text())["claudeAiOauth"]
    assert block["accessToken"] == "access-new"
    assert block["refreshToken"] == "refresh-rotated"
    assert block["expiresAt"] > int(time.time() * 1000)
    # Fields we don't understand must survive — the CLI needs them.
    assert block["subscriptionType"] == "max"
    assert block["scopes"] == ["a", "b"]


async def test_refresh_without_rotation_keeps_existing_refresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A response omitting refresh_token must not blank the stored one."""
    path = _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _ok({"access_token": "access-new", "expires_in": 28800}), [])

    await oauth.ensure_valid_token(tmp_path)

    assert json.loads(path.read_text())["claudeAiOauth"]["refreshToken"] == "refresh-abc"


async def test_missing_credentials_file_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    _stub_post(monkeypatch, _ok({}), calls)

    await oauth.ensure_valid_token(tmp_path)  # must not raise

    assert calls == []


async def test_network_failure_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh is best-effort: it must never become a second way for ask to fail."""
    _write_creds(tmp_path, expires_in_hours=-1)

    async def boom(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)

    await oauth.ensure_valid_token(tmp_path)  # must not raise


# --------------------------------------------------------------------------
# Health semantics — anything but "healthy" means a human must act
# --------------------------------------------------------------------------


def test_health_valid_token(tmp_path: Path) -> None:
    _write_creds(tmp_path, expires_in_hours=5)
    status, detail = oauth.token_health(tmp_path)
    assert status == "healthy"
    assert "refresh token present" in detail


def test_health_expired_but_refreshable_is_healthy(tmp_path: Path) -> None:
    """Expired-with-refresh-token is self-healing and must not read as an outage."""
    _write_creds(tmp_path, expires_in_hours=-2)
    status, detail = oauth.token_health(tmp_path)
    assert status == "healthy"
    assert "refreshes on next call" in detail


def test_health_expired_without_refresh_token_is_unhealthy(tmp_path: Path) -> None:
    _write_creds(tmp_path, expires_in_hours=-2, refresh_token=None)
    status, _ = oauth.token_health(tmp_path)
    assert status == "unhealthy"


def test_health_valid_without_refresh_token_is_degraded(tmp_path: Path) -> None:
    _write_creds(tmp_path, expires_in_hours=5, refresh_token=None)
    status, _ = oauth.token_health(tmp_path)
    assert status == "degraded"


def test_health_missing_file_is_unhealthy_and_says_what_to_run(tmp_path: Path) -> None:
    status, detail = oauth.token_health(tmp_path)
    assert status == "unhealthy"
    # `auth login`, not `setup-token`: the latter writes nothing and does not
    # log the CLI in, so it never produces the file this checks for.
    assert "claude auth login" in detail


def test_health_unreadable_credentials_is_unhealthy(tmp_path: Path) -> None:
    oauth.credentials_path(tmp_path).write_text("{not json")
    status, _ = oauth.token_health(tmp_path)
    assert status == "unhealthy"


async def test_invalid_grant_makes_health_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this module exists for: a dead refresh token must be loud.

    Before sre-agent added this, a revoked refresh token left the service hard
    down while /health still reported healthy, because "a refresh token exists"
    was read as "we can self-heal".
    """
    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(
        monkeypatch,
        _err(400, {"error": "invalid_grant", "error_description": "Refresh token not found"}),
        [],
    )

    assert oauth.token_health(tmp_path)[0] == "healthy"  # before the rejection

    await oauth.ensure_valid_token(tmp_path)

    status, detail = oauth.token_health(tmp_path)
    assert status == "unhealthy"
    assert "claude auth login" in detail


async def test_transient_error_does_not_flag_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx recovers on the next call and must not demand re-authentication."""
    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _err(503, {"error": "overloaded"}), [])

    await oauth.ensure_valid_token(tmp_path)

    assert oauth.token_health(tmp_path)[0] == "healthy"


async def test_reauthentication_self_clears_the_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health keys on token *identity*, so a fresh token clears the alarm at once.

    Without this, an operator who re-authenticated would keep seeing "unhealthy"
    until the next refresh ~8h later.
    """
    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _err(400, {"error": "invalid_grant"}), [])
    await oauth.ensure_valid_token(tmp_path)
    assert oauth.token_health(tmp_path)[0] == "unhealthy"

    # Operator runs `claude auth login`, writing a different refresh token.
    _write_creds(tmp_path, expires_in_hours=8, refresh_token="refresh-fresh")

    assert oauth.token_health(tmp_path)[0] == "healthy"


async def test_successful_refresh_clears_a_prior_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _err(400, {"error": "invalid_grant"}), [])
    await oauth.ensure_valid_token(tmp_path)
    assert oauth.token_health(tmp_path)[0] == "unhealthy"

    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _ok({"access_token": "a", "expires_in": 28800}), [])
    await oauth.ensure_valid_token(tmp_path)

    assert oauth.token_health(tmp_path)[0] == "healthy"


async def test_concurrent_refreshes_are_serialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent asks must not both spend the same single-use refresh token."""
    import asyncio

    _write_creds(tmp_path, expires_in_hours=-1)
    in_flight = 0
    peak = 0

    async def slow_post(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _ok({"access_token": "a", "refresh_token": "r", "expires_in": 28800})

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)

    await asyncio.gather(*(oauth.ensure_valid_token(tmp_path) for _ in range(4)))

    assert peak == 1


# --------------------------------------------------------------------------
# Credential file permissions
# --------------------------------------------------------------------------


async def test_refresh_writes_credentials_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh must not widen the permissions of the credentials file.

    ``Path.write_text`` creates with ``0o666 & ~umask`` (usually ``0o644``), and
    the atomic rename makes the new file's mode win — so the naive version
    quietly relaxed a file holding a live access token *and* a refresh token,
    every ~8 hours, undoing the ``0o600`` the Claude CLI sets.
    """
    path = _write_creds(tmp_path, expires_in_hours=-1)
    path.chmod(0o600)
    _stub_post(monkeypatch, _ok({"access_token": "new", "expires_in": 28800}), [])

    await oauth.ensure_valid_token(tmp_path)

    assert path.read_text()  # the refresh really happened
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_permissions_hold_even_with_a_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mode must come from the open() call, not from the process umask.

    Under umask 0 a write-then-chmod would still briefly expose the tokens, and
    a plain write would leave them world-readable outright.
    """
    path = _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _ok({"access_token": "new", "expires_in": 28800}), [])

    previous = os.umask(0)
    try:
        await oauth.ensure_valid_token(tmp_path)
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_a_failed_write_leaves_no_credentials_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file holds the same secrets; a crash must not orphan it."""
    _write_creds(tmp_path, expires_in_hours=-1)
    _stub_post(monkeypatch, _ok({"access_token": "new", "expires_in": 28800}), [])

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(oauth.json, "dump", boom)

    # ensure_valid_token swallows failures by design; the point is the cleanup.
    await oauth.ensure_valid_token(tmp_path)

    assert not (tmp_path / ".credentials.tmp").exists()
