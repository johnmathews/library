"""Tests for the /healthz container healthcheck endpoint."""

from fastapi.testclient import TestClient

import library


def test_healthz_returns_200(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_body_shape(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert set(body.keys()) == {"status", "version", "git_sha"}
    assert body["status"] == "ok"


def test_healthz_version_matches_package(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert body["version"] == library.__version__


def test_healthz_reports_git_sha(client: TestClient, monkeypatch) -> None:
    """git_sha reflects the image's baked-in build commit (settings.git_sha)."""
    import library.app as app_module
    from library.config import get_settings

    pinned = get_settings().model_copy(update={"git_sha": "deadbee"})
    monkeypatch.setattr(app_module, "get_settings", lambda: pinned)

    body: dict[str, str] = client.get("/healthz").json()
    assert body["git_sha"] == "deadbee"


def test_healthz_git_sha_is_none_when_unset(client: TestClient) -> None:
    """Unbuilt/dev contexts (no GIT_SHA build-arg) report a null git_sha, not a crash."""
    body: dict[str, str] = client.get("/healthz").json()
    assert body["git_sha"] is None


def test_main_module_exposes_app() -> None:
    from fastapi import FastAPI

    from library.main import app

    assert isinstance(app, FastAPI)
