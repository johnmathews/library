"""OAuth access-token refresh for the subscription LLM backend.

The Claude CLI's OAuth access tokens expire roughly every 8 hours, and in
headless environments (our containers) the CLI does not refresh them itself
(anthropics/claude-code#12447). So we refresh before each SDK query.

Ported from ``homelab-sre/sre-agent/src/agent/oauth_refresh.py``, which learned
each of the following the hard way:

* **Refresh tokens are single-use.** Consuming one without persisting its
  replacement invalidates the credentials outright, so refreshes are serialised
  behind a lock and the write is atomic (temp file, then rename).
* **A rejected refresh token is not self-healing.** ``400 invalid_grant`` means
  a human must re-authenticate; treating "a refresh token is present" as
  "we're fine" left a deployed service hard-down while reporting healthy. We
  track the rejection keyed on a hash of the token so re-authentication clears
  the flag by itself.
* **Transient failures are not rejections.** 5xx and network errors recover on
  the next call and must not raise the alarm.

Refresh is best-effort: any failure is logged and swallowed so a query still
gets attempted and surfaces its own auth error, rather than this module
becoming a second way for ask to fail.
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_OAUTH_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"

# The Claude Code CLI's public OAuth client id. Not a secret — it identifies
# the application to the token endpoint, and the refresh token is the credential.
_CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Refresh this long before the stated expiry, so a token cannot lapse midway
# through a query that has already started.
_REFRESH_BUFFER_MS = 5 * 60 * 1000

_CREDENTIALS_FILENAME = ".credentials.json"

# Serialises refresh attempts. Without it two concurrent ask requests can both
# spend the same single-use refresh token; the loser's response is discarded and
# the credentials are dead.
_refresh_lock = asyncio.Lock()

# SHA-256 of a refresh token the server answered with ``invalid_grant`` — i.e.
# permanently dead. Stored as a hash so the raw secret is never held or logged.
# Compared against the on-disk token by ``token_health`` so that re-running
# ``claude setup-token`` clears the flag with no restart.
_rejected_refresh_token_hash: str | None = None


def _hash_token(token: str) -> str:
    """Return a stable, non-reversible fingerprint of a refresh token."""
    return hashlib.sha256(token.encode()).hexdigest()


def reset_rejection_state() -> None:
    """Clear the remembered rejection. Test seam; not called in production."""
    global _rejected_refresh_token_hash
    _rejected_refresh_token_hash = None


def credentials_path(config_dir: Path) -> Path:
    """Return the credentials file inside a Claude config directory."""
    return config_dir / _CREDENTIALS_FILENAME


def _read_oauth(config_dir: Path) -> dict[str, Any] | None:
    """Read the ``claudeAiOauth`` block, or None when it isn't usable."""
    path = credentials_path(config_dir)
    if not path.exists():
        return None
    try:
        creds: dict[str, Any] = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read Claude credentials at %s", path, exc_info=True)
        return None
    oauth = creds.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) else None


async def ensure_valid_token(config_dir: Path) -> None:
    """Refresh the OAuth access token if it has expired or is about to.

    Best-effort by design: every failure is logged and swallowed so the caller
    still attempts its query.
    """
    try:
        async with _refresh_lock:
            await _refresh_if_needed(config_dir)
    except Exception:
        logger.warning("OAuth token refresh failed", exc_info=True)


async def _refresh_if_needed(config_dir: Path) -> None:
    oauth = await asyncio.to_thread(_read_oauth, config_dir)
    if oauth is None:
        logger.debug("No OAuth credentials under %s — nothing to refresh", config_dir)
        return

    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, int | float):
        logger.debug("Credentials carry no expiresAt — skipping refresh")
        return

    now_ms = int(time.time() * 1000)
    if now_ms < expires_at - _REFRESH_BUFFER_MS:
        return

    refresh_token = oauth.get("refreshToken")
    if not isinstance(refresh_token, str) or not refresh_token:
        logger.warning("Access token is expiring but no refresh token is present")
        return

    logger.info("OAuth access token expired or near expiry — refreshing")
    await _do_refresh(config_dir, oauth, refresh_token)


def _is_invalid_grant(response: httpx.Response) -> bool:
    """True when an error response means the refresh token itself is dead."""
    try:
        return bool(response.json().get("error") == "invalid_grant")
    except ValueError:
        return "invalid_grant" in response.text


async def _do_refresh(config_dir: Path, oauth: dict[str, Any], refresh_token: str) -> None:
    """Exchange the refresh token and persist the new credentials."""
    global _rejected_refresh_token_hash

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            _OAUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLAUDE_CODE_CLIENT_ID,
            },
            headers={"Content-Type": "application/json"},
            follow_redirects=True,
        )

    if response.status_code != 200:
        # 400 invalid_grant is terminal: the token is dead and retrying cannot
        # revive it, so remember it and let token_health raise the alarm.
        # Anything else (5xx, network) recovers on the next call — leave it
        # unflagged so a blip doesn't read as "re-authenticate".
        if response.status_code == 400 and _is_invalid_grant(response):
            _rejected_refresh_token_hash = _hash_token(refresh_token)
            logger.error(
                "OAuth refresh token rejected (invalid_grant) — re-authenticate on the "
                "host with `claude setup-token` and restart the container"
            )
        else:
            logger.warning(
                "OAuth refresh returned HTTP %d: %s", response.status_code, response.text[:200]
            )
        return

    data: dict[str, Any] = response.json()
    new_access = data.get("access_token")
    expires_in = data.get("expires_in")
    if not isinstance(new_access, str) or not isinstance(expires_in, int | float):
        logger.warning("OAuth refresh response missing expected fields: %s", sorted(data))
        return

    # Preserve every other field (scopes, subscriptionType, ...) — we only know
    # about the three we rotate, and dropping the rest breaks the CLI.
    oauth["accessToken"] = new_access
    new_refresh = data.get("refresh_token")
    if isinstance(new_refresh, str) and new_refresh:
        oauth["refreshToken"] = new_refresh
    oauth["expiresAt"] = int((time.time() + float(expires_in)) * 1000)

    await asyncio.to_thread(_write_credentials, config_dir, oauth)

    # A refresh that succeeds proves the on-disk token is live again.
    _rejected_refresh_token_hash = None
    logger.info("OAuth token refreshed (expires in %ds)", int(float(expires_in)))


def _write_credentials(config_dir: Path, oauth: dict[str, Any]) -> None:
    """Write credentials atomically so a crash cannot leave a half-file.

    A torn credentials file is unrecoverable without a human, which is exactly
    the failure this whole module exists to avoid.
    """
    path = credentials_path(config_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"claudeAiOauth": oauth}))
    tmp.replace(path)


def token_health(config_dir: Path) -> tuple[str, str]:
    """Report subscription credential status as ``(status, detail)``.

    ``status`` is one of ``healthy`` / ``degraded`` / ``unhealthy``, and the
    semantics are deliberately actionable: anything other than ``healthy`` means
    **a human needs to do something**. An access token that has expired while a
    refresh token is present is ``healthy`` — it refreshes on the next call, and
    reporting that as degraded made an entirely self-healing state look like an
    outage for an hour out of every eight in sre-agent.
    """
    path = credentials_path(config_dir)
    if not path.exists():
        return ("unhealthy", f"no credentials at {path} — run `claude setup-token` on the host")

    oauth = _read_oauth(config_dir)
    if oauth is None:
        return ("unhealthy", f"credentials at {path} are unreadable or carry no OAuth block")

    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, int | float):
        return ("unhealthy", "credentials carry no expiresAt")

    remaining_hours = (int(expires_at) - int(time.time() * 1000)) / (1000 * 3600)
    refresh_token = oauth.get("refreshToken")

    if isinstance(refresh_token, str) and refresh_token:
        if (
            _rejected_refresh_token_hash is not None
            and _hash_token(refresh_token) == _rejected_refresh_token_hash
        ):
            return (
                "unhealthy",
                "refresh token rejected by Anthropic (invalid_grant) — re-authenticate "
                "on the host with `claude setup-token` and restart the container",
            )
        if remaining_hours < 0:
            return (
                "healthy",
                f"access token expired {-remaining_hours:.1f}h ago, refreshes on next call",
            )
        return ("healthy", f"access token valid ({remaining_hours:.1f}h), refresh token present")

    if remaining_hours < 0:
        return ("unhealthy", f"access token expired {-remaining_hours:.1f}h ago, no refresh token")
    return ("degraded", f"access token valid ({remaining_hours:.1f}h) but no refresh token")
