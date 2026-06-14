import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from library.jobs import job_app, procrastinate_conninfo
from tests.conftest import AuthUser, create_user, fetch_all, login


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
