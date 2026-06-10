"""Tests for the /healthz container healthcheck endpoint."""

from fastapi.testclient import TestClient

import library


def test_healthz_returns_200(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_body_shape(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert set(body.keys()) == {"status", "version"}
    assert body["status"] == "ok"


def test_healthz_version_matches_package(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert body["version"] == library.__version__


def test_main_module_exposes_app() -> None:
    from fastapi import FastAPI

    from library.main import app

    assert isinstance(app, FastAPI)
