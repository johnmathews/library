from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector

from library.jobs import job_app, procrastinate_conninfo
from tests.conftest import create_user, login


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
