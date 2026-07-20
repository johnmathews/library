import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from library import notifications
from library.config import get_settings
from library.email_ingest import BODY_MIN_CHARS, BODY_MIN_WORDS
from library.email_label import PROMPT_VERSION
from library.jobs import job_app, procrastinate_conninfo
from library.notifications import PushoverValidation
from tests.conftest import AuthUser, create_user, fetch_all, login


@pytest.fixture
def stub_pushover_validate(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the real Pushover validation with a recording stub that passes.

    Returns the list of calls so a test can assert validation ran (or didn't).
    """
    calls: list[dict[str, object]] = []

    async def _fake(
        *, app_token: str, user_key: str, device: str | None = None
    ) -> PushoverValidation:
        calls.append({"app_token": app_token, "user_key": user_key, "device": device})
        return PushoverValidation(valid=True, devices=("iphone",))

    monkeypatch.setattr(notifications, "validate_pushover", _fake)
    return calls


def _seed_raw_preferences(database_url: str, user_id: int, prefs: dict[str, object]) -> None:
    """Write a raw preferences blob for one user, incl. keys the API can't set."""

    async def _run() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE users SET preferences = CAST(:p AS jsonb) WHERE id = :id"),
                    {"p": json.dumps(prefs), "id": user_id},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_settings_defaults(api_client: TestClient) -> None:
    body = api_client.get("/api/settings").json()
    assert body["dashboard_fields"] == [
        "kind",
        "sender",
        "tags",
        "date",
        "language",
        "status",
    ]


def test_get_settings_includes_default_background_tone(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["background_tone"] == "neutral"


def test_put_appearance_round_trips(api_client: TestClient) -> None:
    put = api_client.put("/api/settings/appearance", json={"background_tone": "slate"})
    assert put.status_code == 200, put.text
    assert put.json()["background_tone"] == "slate"
    assert api_client.get("/api/settings").json()["background_tone"] == "slate"
    assert api_client.get("/api/auth/me").json()["preferences"]["background_tone"] == "slate"


def test_put_appearance_unknown_tone_falls_back_to_default(api_client: TestClient) -> None:
    put = api_client.put("/api/settings/appearance", json={"background_tone": "chartreuse"})
    assert put.status_code == 200, put.text
    assert put.json()["background_tone"] == "neutral"


def test_get_settings_includes_default_tile_preview(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["tile_preview"] == "full_width"


def test_get_settings_resolves_unknown_tile_preview_to_default(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    _seed_raw_preferences(api_database_url, auth_user.id, {"tile_preview": "sideways"})
    assert api_client.get("/api/settings").json()["tile_preview"] == "full_width"


def test_put_appearance_round_trips_tile_preview(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "tile_preview": "whole_page"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["tile_preview"] == "whole_page"
    assert api_client.get("/api/settings").json()["tile_preview"] == "whole_page"


def test_put_appearance_unknown_tile_preview_falls_back_to_default(
    api_client: TestClient,
) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "tile_preview": "diagonal"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["tile_preview"] == "full_width"


def test_get_settings_includes_default_dock_position(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["dock_position"] == "top-right"


def test_get_settings_resolves_unknown_dock_position_to_default(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    _seed_raw_preferences(api_database_url, auth_user.id, {"dock_position": "middle-earth"})
    assert api_client.get("/api/settings").json()["dock_position"] == "top-right"


def test_put_appearance_round_trips_dock_position(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={
            "background_tone": "neutral",
            "tile_preview": "full_width",
            "dock_position": "bottom-left",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["dock_position"] == "bottom-left"
    assert api_client.get("/api/settings").json()["dock_position"] == "bottom-left"


def test_put_appearance_unknown_dock_position_falls_back_to_default(
    api_client: TestClient,
) -> None:
    # Mirrors background_tone/tile_preview: the before-validator coerces an
    # unknown value to the default rather than raising a 422.
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "dock_position": "center-of-the-universe"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["dock_position"] == "top-right"


def test_put_appearance_sets_both_tone_and_tile_preview(api_client: TestClient) -> None:
    api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "mist", "tile_preview": "whole_page"},
    )
    body = api_client.get("/api/settings").json()
    assert body["background_tone"] == "mist"
    assert body["tile_preview"] == "whole_page"


def test_appearance_and_dashboard_fields_are_independent(api_client: TestClient) -> None:
    api_client.put("/api/settings", json={"dashboard_fields": ["amount"]})
    api_client.put("/api/settings/appearance", json={"background_tone": "mist"})
    # Each save preserves the other concern.
    body = api_client.get("/api/settings").json()
    assert body["dashboard_fields"] == ["amount"]
    assert body["background_tone"] == "mist"
    # And updating one again does not clobber the other.
    api_client.put("/api/settings", json={"dashboard_fields": ["tags"]})
    body = api_client.get("/api/settings").json()
    assert body["dashboard_fields"] == ["tags"]
    assert body["background_tone"] == "mist"


def test_appearance_requires_auth(anon_client: TestClient) -> None:
    resp = anon_client.put("/api/settings/appearance", json={"background_tone": "slate"})
    assert resp.status_code == 401


def test_get_settings_includes_default_kind_colors(api_client: TestClient) -> None:
    # Unset by default: everyone sees the frontend's built-in palette.
    assert api_client.get("/api/settings").json()["kind_colors"] == {}


def test_put_kind_colors_round_trips_and_normalises(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/kind-colors",
        json={"kind_colors": {"invoice": "#56B1F3", "receipt": "#34bd68"}},
    )
    assert put.status_code == 200, put.text
    # Hex is lower-cased on the way in.
    assert put.json()["kind_colors"] == {"invoice": "#56b1f3", "receipt": "#34bd68"}
    assert api_client.get("/api/settings").json()["kind_colors"] == {
        "invoice": "#56b1f3",
        "receipt": "#34bd68",
    }
    assert api_client.get("/api/auth/me").json()["preferences"]["kind_colors"] == {
        "invoice": "#56b1f3",
        "receipt": "#34bd68",
    }


def test_put_kind_colors_drops_malformed_entries(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/kind-colors",
        json={"kind_colors": {"invoice": "#56b1f3", "bad": "blue", "short": "#fff"}},
    )
    assert put.status_code == 200, put.text
    assert put.json()["kind_colors"] == {"invoice": "#56b1f3"}


def test_put_kind_colors_empty_map_resets_all(api_client: TestClient) -> None:
    api_client.put("/api/settings/kind-colors", json={"kind_colors": {"invoice": "#56b1f3"}})
    put = api_client.put("/api/settings/kind-colors", json={"kind_colors": {}})
    assert put.status_code == 200, put.text
    assert put.json()["kind_colors"] == {}
    assert api_client.get("/api/settings").json()["kind_colors"] == {}


def test_put_kind_colors_preserves_other_preferences(api_client: TestClient) -> None:
    api_client.put("/api/settings/appearance", json={"background_tone": "mist"})
    api_client.put("/api/settings/kind-colors", json={"kind_colors": {"invoice": "#56b1f3"}})
    body = api_client.get("/api/settings").json()
    assert body["background_tone"] == "mist"
    assert body["kind_colors"] == {"invoice": "#56b1f3"}


def test_put_kind_colors_requires_auth(anon_client: TestClient) -> None:
    resp = anon_client.put(
        "/api/settings/kind-colors", json={"kind_colors": {"invoice": "#56b1f3"}}
    )
    assert resp.status_code == 401


def test_put_settings_round_trips(api_client: TestClient) -> None:
    put = api_client.put("/api/settings", json={"dashboard_fields": ["amount", "tags"]})
    assert put.status_code == 200, put.text
    assert put.json()["dashboard_fields"] == ["amount", "tags"]
    assert api_client.get("/api/settings").json()["dashboard_fields"] == ["amount", "tags"]
    assert api_client.get("/api/auth/me").json()["preferences"]["dashboard_fields"] == [
        "amount",
        "tags",
    ]


def test_put_settings_drops_unknown_and_dedupes(api_client: TestClient) -> None:
    put = api_client.put("/api/settings", json={"dashboard_fields": ["kind", "kind", "nope"]})
    assert put.status_code == 200, put.text
    assert put.json()["dashboard_fields"] == ["kind"]


def test_put_settings_empty_list_shows_nothing(api_client: TestClient) -> None:
    put = api_client.put("/api/settings", json={"dashboard_fields": []})
    assert put.status_code == 200, put.text
    assert api_client.get("/api/settings").json()["dashboard_fields"] == []


def test_settings_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/settings").status_code == 401


def test_settings_put_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.put("/api/settings", json={"dashboard_fields": []}).status_code == 401


def test_put_settings_preserves_other_preference_keys(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    # A future preference type already stored on the user. The API can't set
    # this key, so seed it directly; PUT must not clobber it.
    _seed_raw_preferences(api_database_url, auth_user.id, {"theme": "dark"})

    put = api_client.put("/api/settings", json={"dashboard_fields": ["kind"]})
    assert put.status_code == 200, put.text

    rows = fetch_all(
        api_database_url,
        "SELECT preferences::text FROM users WHERE id = :id",
        id=auth_user.id,
    )
    assert json.loads(rows[0][0]) == {"theme": "dark", "dashboard_fields": ["kind"]}


def test_get_settings_includes_notification_defaults(api_client: TestClient) -> None:
    notifs = api_client.get("/api/settings").json()["notifications"]
    assert notifs == {
        "enabled": False,
        "pushover_app_token_set": False,
        "pushover_user_key_set": False,
        "pushover_device": None,
        "events": [],
        "email_forward_addresses": [],
    }


def test_put_notifications_round_trips_without_exposing_secrets(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str, stub_pushover_validate
) -> None:
    put = api_client.put(
        "/api/settings/notifications",
        json={
            "enabled": True,
            "pushover_app_token": "app-secret",
            "pushover_user_key": "user-secret",
            "pushover_device": "iphone",
            "events": ["document_success", "processing_error"],
        },
    )
    assert put.status_code == 200, put.text
    notifs = put.json()["notifications"]
    # Read model exposes configured-state, never the raw secret.
    assert notifs["enabled"] is True
    assert notifs["pushover_app_token_set"] is True
    assert notifs["pushover_user_key_set"] is True
    assert notifs["pushover_device"] == "iphone"
    assert notifs["events"] == ["document_success", "processing_error"]
    assert "pushover_app_token" not in notifs
    assert "pushover_user_key" not in notifs
    assert "app-secret" not in put.text and "user-secret" not in put.text
    # Validation ran exactly once.
    assert len(stub_pushover_validate) == 1
    # /auth/me carries the same secret-safe view.
    me = api_client.get("/api/auth/me").json()["preferences"]["notifications"]
    assert me["pushover_app_token_set"] is True and "pushover_app_token" not in me
    # But the secret IS persisted (so the worker can send) — verify in the DB.
    rows = fetch_all(
        api_database_url, "SELECT preferences::text FROM users WHERE id = :id", id=auth_user.id
    )
    stored = json.loads(rows[0][0])["notifications"]
    assert stored["pushover_app_token"] == "app-secret"
    assert stored["pushover_user_key"] == "user-secret"


def test_put_notifications_partial_update_keeps_credentials(
    api_client: TestClient, stub_pushover_validate
) -> None:
    api_client.put(
        "/api/settings/notifications",
        json={
            "enabled": True,
            "pushover_app_token": "app-secret",
            "pushover_user_key": "user-secret",
            "events": ["document_success"],
        },
    )
    # A second save changes only the event set — no tokens in the body.
    put = api_client.put(
        "/api/settings/notifications",
        json={"enabled": True, "events": ["processing_error"]},
    )
    assert put.status_code == 200, put.text
    notifs = put.json()["notifications"]
    assert notifs["pushover_app_token_set"] is True  # credential preserved
    assert notifs["pushover_user_key_set"] is True
    assert notifs["events"] == ["processing_error"]


def test_put_notifications_enable_without_credentials_422(api_client: TestClient) -> None:
    resp = api_client.put(
        "/api/settings/notifications",
        json={"enabled": True, "events": ["document_success"]},
    )
    assert resp.status_code == 422, resp.text


def test_put_notifications_rejects_invalid_credentials(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _reject(
        *, app_token: str, user_key: str, device: str | None = None
    ) -> PushoverValidation:
        return PushoverValidation(valid=False, errors=("user identifier is invalid",))

    monkeypatch.setattr(notifications, "validate_pushover", _reject)
    resp = api_client.put(
        "/api/settings/notifications",
        json={
            "enabled": True,
            "pushover_app_token": "app",
            "pushover_user_key": "bad",
            "events": ["document_success"],
        },
    )
    assert resp.status_code == 422
    assert "user identifier is invalid" in resp.text


def test_put_notifications_disabled_skips_validation(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def _spy(
        *, app_token: str, user_key: str, device: str | None = None
    ) -> PushoverValidation:
        nonlocal called
        called = True
        return PushoverValidation(valid=True)

    monkeypatch.setattr(notifications, "validate_pushover", _spy)
    put = api_client.put(
        "/api/settings/notifications",
        json={"enabled": False, "events": ["document_success"]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["notifications"]["enabled"] is False
    assert called is False  # disabled saves never hit Pushover


def test_put_notifications_drops_unknown_events(
    api_client: TestClient, stub_pushover_validate
) -> None:
    put = api_client.put(
        "/api/settings/notifications",
        json={
            "enabled": True,
            "pushover_app_token": "a",
            "pushover_user_key": "u",
            "events": ["document_success", "document_success", "nope"],
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["notifications"]["events"] == ["document_success"]


def test_put_notifications_round_trips_forward_addresses(
    api_client: TestClient, stub_pushover_validate
) -> None:
    put = api_client.put(
        "/api/settings/notifications",
        json={
            "enabled": False,
            "events": [],
            "email_forward_addresses": ["John@Example.com", "john@example.com", " jane@x.org "],
        },
    )
    assert put.status_code == 200, put.text
    # Lowercased, de-duplicated, whitespace-stripped.
    assert put.json()["notifications"]["email_forward_addresses"] == [
        "john@example.com",
        "jane@x.org",
    ]
    assert api_client.get("/api/settings").json()["notifications"]["email_forward_addresses"] == [
        "john@example.com",
        "jane@x.org",
    ]


def test_notifications_requires_auth(anon_client: TestClient) -> None:
    resp = anon_client.put("/api/settings/notifications", json={"enabled": False, "events": []})
    assert resp.status_code == 401


def test_notifications_independent_of_other_preferences(
    api_client: TestClient, stub_pushover_validate
) -> None:
    api_client.put("/api/settings", json={"dashboard_fields": ["amount"]})
    api_client.put("/api/settings/appearance", json={"background_tone": "mist"})
    api_client.put(
        "/api/settings/notifications",
        json={"enabled": True, "pushover_app_token": "a", "pushover_user_key": "u", "events": []},
    )
    body = api_client.get("/api/settings").json()
    assert body["dashboard_fields"] == ["amount"]
    assert body["background_tone"] == "mist"
    assert body["notifications"]["pushover_app_token_set"] is True


# --- GET /api/settings/email-triage ------------------------------------------


def _set_email_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str | None]) -> None:
    """Apply email-related env overrides and rebuild the cached Settings.

    ``None`` deletes the variable. The endpoint calls ``get_settings()`` at
    request time, so clearing the cache here makes the next request see these
    values; the ``api_app`` fixture clears the cache again at teardown.
    """
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_email_triage_reflects_instance_config(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_email_env(
        monkeypatch,
        {
            "LIBRARY_EMAIL_HOST": "imap.example.com",
            "LIBRARY_EMAIL_POLL_MINUTES": "7",
            "LIBRARY_EMAIL_HELD_FOLDER": "Custom/Held",
            "LIBRARY_EMAIL_PROCESSED_FOLDER": "Custom/Processed",
            "LIBRARY_EMAIL_HOLD_ENABLED": "true",
            "LIBRARY_EMAIL_HOLD_BELOW_SUBSTANCE": "false",
            "LIBRARY_EMAIL_HOLD_UNKNOWN_SENDERS": "true",
            "LIBRARY_EMAIL_ALLOWED_SENDERS": "Alice@Example.com, bob@example.com",
            "LIBRARY_EMAIL_FILTER_NOISE_ENABLED": "true",
            "LIBRARY_EMAIL_FILTER_TINY_IMAGE_MAX_BYTES": "8192",
            "LIBRARY_EMAIL_FILTER_TINY_IMAGE_MAX_EDGE_PX": "128",
            "LIBRARY_EMAIL_FILTER_DECORATION_MAX_BYTES": "32768",
            "LIBRARY_EMAIL_FILTER_DECORATION_MAX_EDGE_PX": "256",
            "LIBRARY_EMAIL_LABEL_ENABLED": "true",
            "LIBRARY_ANTHROPIC_API_KEY": "sk-ant-fake-triage-test",
            "LIBRARY_EMAIL_LABEL_MODEL": "claude-sonnet-4-6",
            "LIBRARY_EMAIL_LABEL_DAILY_BUDGET_USD": "3.5",
            "LIBRARY_EMAIL_LABEL_BODY_SNIPPET_CHARS": "500",
            "LIBRARY_EMAIL_IMAP_TIMEOUT_SECONDS": "30.5",
        },
    )
    resp = api_client.get("/api/settings/email-triage")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "email_in_configured": True,
        "poll_minutes": 7,
        "held_folder": "Custom/Held",
        "processed_folder": "Custom/Processed",
        "hold": {"enabled": True, "below_substance": False, "unknown_senders": True},
        "allowlist": {"configured": True, "count": 2},
        "noise_filter": {
            "enabled": True,
            "tiny_image_max_bytes": 8192,
            "tiny_image_max_edge_px": 128,
            "decoration_max_bytes": 32768,
            "decoration_max_edge_px": 256,
        },
        "label": {
            "enabled": True,
            "active": True,
            "model": "claude-sonnet-4-6",
            "daily_budget_usd": 3.5,
            "body_snippet_chars": 500,
            "prompt_version": PROMPT_VERSION,
        },
        "body_substance": {"min_words": BODY_MIN_WORDS, "min_chars": BODY_MIN_CHARS},
        "imap_timeout_seconds": 30.5,
    }
    # The allowlisted addresses themselves are never exposed — any
    # authenticated user can read this page.
    assert "alice@example.com" not in resp.text.lower()
    assert "bob@example.com" not in resp.text.lower()


def test_email_triage_label_inactive_without_api_key(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_email_env(
        monkeypatch,
        {
            "LIBRARY_EMAIL_HOST": "imap.example.com",
            "LIBRARY_EMAIL_LABEL_ENABLED": "true",
            "LIBRARY_ANTHROPIC_API_KEY": None,
        },
    )
    label = api_client.get("/api/settings/email-triage").json()["label"]
    assert label["enabled"] is True
    assert label["active"] is False  # flag on, but no key → the pass never runs


def test_email_triage_unconfigured_when_no_host(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_email_env(monkeypatch, {"LIBRARY_EMAIL_HOST": None})
    resp = api_client.get("/api/settings/email-triage")
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_in_configured"] is False


def test_email_triage_never_leaks_secrets(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_email_env(
        monkeypatch,
        {
            "LIBRARY_EMAIL_HOST": "imap.secret-host.example",
            "LIBRARY_EMAIL_USERNAME": "triage-bot@secret-host.example",
            "LIBRARY_EMAIL_PASSWORD": "fake-email-password-do-not-leak",
            "LIBRARY_ANTHROPIC_API_KEY": "sk-ant-fake-key-do-not-leak",
            "LIBRARY_EMAIL_LABEL_ENABLED": "true",
        },
    )
    resp = api_client.get("/api/settings/email-triage")
    assert resp.status_code == 200, resp.text
    # SecretStr values, the username, and the host must never appear in the
    # serialized response — only the configured boolean.
    for secret in (
        "fake-email-password-do-not-leak",
        "sk-ant-fake-key-do-not-leak",
        "triage-bot@secret-host.example",
        "imap.secret-host.example",
    ):
        assert secret not in resp.text


def test_email_triage_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/settings/email-triage").status_code == 401


# --- GET /api/settings/email-triage/recent-skips ------------------------------


def _seed_skip_trace(
    database_url: str,
    *,
    subject: str,
    message_id: str | None = None,
    from_address: str | None = "sender@example.com",
    decisions: list[dict[str, object]],
) -> int:
    """Insert one email_selection_traces row directly; returns its id."""

    async def _run() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "INSERT INTO email_selection_traces "
                        "(message_id, subject, from_address, decisions) "
                        "VALUES (:m, :s, :f, CAST(:d AS jsonb)) RETURNING id"
                    ),
                    {
                        "m": message_id,
                        "s": subject,
                        "f": from_address,
                        "d": json.dumps(decisions),
                    },
                )
                return int(result.scalar_one())
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _decision(**overrides: object) -> dict[str, object]:
    """One SelectionDecision.as_detail()-shaped dict (the stored JSONB shape)."""
    base: dict[str, object] = {
        "kind": "attachment",
        "filename": "image001.png",
        "mime": "image/png",
        "size": 6144,
        "stage": "classify",
        "verdict": "filtered",
        "reason": "decoration_image",
        "detail": "filename, size and shape signals fired",
    }
    return {**base, **overrides}


def test_recent_skips_payload_is_compact_and_skip_only(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    subject = f"skips payload {tag}"
    message_id = f"<{tag}@example.com>"
    row_id = _seed_skip_trace(
        api_database_url,
        subject=subject,
        message_id=message_id,
        decisions=[
            # The ingested sibling and the bookkeeping body decision are stored
            # in the row but must NOT be echoed by the API (skips only).
            _decision(filename="real.pdf", verdict="ingested", reason=None, detail=None),
            _decision(),
            _decision(
                kind="body",
                filename=None,
                mime=None,
                size=None,
                stage="body_substance",
                verdict="filtered",
                reason="not_needed",
                detail=None,
            ),
        ],
    )

    resp = api_client.get("/api/settings/email-triage/recent-skips")
    assert resp.status_code == 200, resp.text
    mine = [row for row in resp.json()["recent_skips"] if row["id"] == row_id]
    assert len(mine) == 1
    row = mine[0]
    assert row["subject"] == subject
    assert row["message_id"] == message_id
    assert row["from_address"] == "sender@example.com"
    assert row["created_at"]  # newest-first ordering key, shown in the UI
    # Only the genuinely skipped item survives, in a compact shape.
    assert row["decisions"] == [
        {
            "kind": "attachment",
            "filename": "image001.png",
            "reason": "decoration_image",
            "detail": "filename, size and shape signals fired",
        }
    ]


def test_recent_skips_newest_first_and_capped_at_20(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = [
        _seed_skip_trace(
            api_database_url,
            subject=f"skips bulk {tag} {index}",
            decisions=[_decision(reason="tiny_image", detail=f"row {index}")],
        )
        for index in range(25)
    ]

    resp = api_client.get("/api/settings/email-triage/recent-skips")
    assert resp.status_code == 200, resp.text
    returned = [row["id"] for row in resp.json()["recent_skips"]]
    assert len(returned) == 20
    # These 25 are the newest rows in the shared DB at this moment, so the
    # response is exactly their newest 20, newest first.
    assert returned == sorted(ids, reverse=True)[:20]


def test_recent_skips_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/settings/email-triage/recent-skips").status_code == 401


def test_put_settings_isolated_per_user(
    api_client: TestClient, api_app: FastAPI, api_database_url: str
) -> None:
    # api_client is logged in as auth_user; change their prefs.
    api_client.put("/api/settings", json={"dashboard_fields": ["amount"]})
    # A second, independent user keeps the defaults.
    other = create_user(api_database_url)
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as other_client:
        login(other_client, other)
        body = other_client.get("/api/settings").json()
    assert body["dashboard_fields"] == ["kind", "sender", "tags", "date", "language", "status"]


def test_get_settings_includes_default_phone_columns(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["phone_columns"] == 2


def test_put_appearance_round_trips_phone_columns(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "phone_columns": 3},
    )
    assert put.status_code == 200, put.text
    assert put.json()["phone_columns"] == 3
    assert api_client.get("/api/settings").json()["phone_columns"] == 3


def test_put_appearance_out_of_range_phone_columns_falls_back_to_default(
    api_client: TestClient,
) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "phone_columns": 9},
    )
    assert put.status_code == 200, put.text
    assert put.json()["phone_columns"] == 2


def test_get_settings_resolves_garbage_phone_columns_to_default(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    _seed_raw_preferences(api_database_url, auth_user.id, {"phone_columns": "lots"})
    assert api_client.get("/api/settings").json()["phone_columns"] == 2
