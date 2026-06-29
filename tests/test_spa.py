"""Tests for production SPA serving (W17): static files + index.html fallback."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector

from library.app import create_app, warn_if_no_public_base_url
from library.config import get_settings
from library.jobs import job_app

INDEX_HTML = '<!DOCTYPE html><html><body><div id="app"></div></body></html>'


@pytest.fixture
def spa_dist(tmp_path: Path) -> Path:
    """A minimal Vite-build-shaped dist directory."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (dist / "assets" / "index-abc123.js").write_text("console.log('library')")
    (dist / "manifest.webmanifest").write_text('{"name": "Library"}')
    return dist


def _client_with_dist(dist: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LIBRARY_FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    try:
        with (
            job_app.replace_connector(InMemoryConnector()),
            TestClient(create_app()) as client,
        ):
            yield client
    finally:
        get_settings.cache_clear()


@pytest.fixture
def spa_client(spa_dist: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client against an app that found a built frontend."""
    yield from _client_with_dist(spa_dist, monkeypatch)


@pytest.fixture
def no_dist_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client against an app whose frontend_dist does not exist (dev mode)."""
    yield from _client_with_dist(tmp_path / "missing", monkeypatch)


def test_root_serves_spa_shell(spa_client: TestClient) -> None:
    response = spa_client.get("/")
    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert response.headers["content-type"].startswith("text/html")
    # index.html must revalidate so deploys take effect immediately.
    assert response.headers["cache-control"] == "no-cache"


def test_client_side_route_serves_spa_shell(spa_client: TestClient) -> None:
    response = spa_client.get("/documents/42")
    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_forever(spa_client: TestClient) -> None:
    response = spa_client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log('library')"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_real_static_file_is_served_not_index(spa_client: TestClient) -> None:
    response = spa_client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.json() == {"name": "Library"}


def test_unknown_api_path_is_json_404_not_index(spa_client: TestClient) -> None:
    response = spa_client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}


def test_unknown_mcp_and_docs_paths_are_not_index(spa_client: TestClient) -> None:
    for path in ("/healthz/nope", "/redoc/nope"):
        response = spa_client.get(path)
        assert response.status_code == 404, path
        assert '<div id="app">' not in response.text, path


def test_healthz_unaffected(spa_client: TestClient) -> None:
    response = spa_client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_docs_unaffected(spa_client: TestClient) -> None:
    assert spa_client.get("/docs").status_code == 200
    assert spa_client.get("/openapi.json").status_code == 200


def test_no_dist_means_no_spa(no_dist_client: TestClient) -> None:
    response = no_dist_client.get("/")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize("unset_value", [None, ""])
def test_warns_when_public_base_url_unset(
    unset_value: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """An unset base URL means linkless pushes: say so loudly, once, at startup."""
    with caplog.at_level("WARNING", logger="library.app"):
        warn_if_no_public_base_url(unset_value)
    assert any(
        "LIBRARY_PUBLIC_BASE_URL is unset" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    )


def test_no_warning_when_public_base_url_set(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="library.app"):
        warn_if_no_public_base_url("https://library.example.com")
    assert not [r for r in caplog.records if "LIBRARY_PUBLIC_BASE_URL" in r.message]
