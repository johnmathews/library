import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from library import notifications
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
