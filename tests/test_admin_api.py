"""Integration tests for the admin-only API (W2): /api/admin/*.

Gated by ``require_admin``: anonymous → 401, non-admin → 403, admin → 200.
Uses the shared API test database; assertions target seeded markers and
shapes rather than exact table contents.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AuthUser, create_user

pytestmark = pytest.mark.integration

_ADMIN_GET_ROUTES = ["/api/admin/system", "/api/admin/architecture", "/api/admin/coverage",
                     "/api/admin/users"]


# ----------------------------------------------------------------- gating


def test_admin_routes_reject_anonymous(anon_client: TestClient) -> None:
    for route in _ADMIN_GET_ROUTES:
        assert anon_client.get(route).status_code == 401, route


def test_admin_routes_reject_non_admin(api_client: TestClient) -> None:
    for route in _ADMIN_GET_ROUTES:
        assert api_client.get(route).status_code == 403, route
    # Mutations too.
    assert (
        api_client.post("/api/admin/users", json={"username": "x", "password": "y"}).status_code
        == 403
    )
    assert api_client.patch("/api/admin/users/1", json={"is_admin": True}).status_code == 403


def test_admin_routes_allow_admin(admin_client: TestClient) -> None:
    for route in _ADMIN_GET_ROUTES:
        assert admin_client.get(route).status_code == 200, route


# ----------------------------------------------------------------- system


def test_system_info_shape_and_no_secrets(admin_client: TestClient) -> None:
    body = admin_client.get("/api/admin/system").json()
    assert body["version"]  # library.__version__
    assert isinstance(body["deployment"], list) and body["deployment"]
    assert {"documents_total", "users_total", "jobs_total", "extraction_cost_usd_total"} <= set(
        body["stats"]
    )
    # The redacted config must never leak secrets or internal URLs.
    config_blob = json.dumps(body["config"]).lower()
    for secret_marker in ("password", "api_key", "token", "database_url", "://"):
        assert secret_marker not in config_blob, secret_marker
    assert body["config"]["extraction_model"]  # a useful operational field is present


def test_system_stats_count_active_admin(admin_client: TestClient) -> None:
    stats = admin_client.get("/api/admin/system").json()["stats"]
    assert stats["users_total"] >= 1
    assert stats["users_active"] >= 1


# --------------------------------------------------------------- coverage


def test_coverage_unavailable_when_file_missing(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.config import get_settings

    monkeypatch.setenv("LIBRARY_COVERAGE_SUMMARY_PATH", str(tmp_path / "nope.json"))
    get_settings.cache_clear()
    body = admin_client.get("/api/admin/coverage").json()
    assert body["available"] is False
    get_settings.cache_clear()


def test_coverage_reads_baked_summary(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.config import get_settings

    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {
                "backend": {"pct": 95.2, "threshold": 85.0},
                "frontend": {"pct": 88.1, "threshold": 85.0},
                "generated_at": "2026-06-28T12:00:00Z",
                "git_sha": "abc1234",
            }
        )
    )
    monkeypatch.setenv("LIBRARY_COVERAGE_SUMMARY_PATH", str(summary))
    get_settings.cache_clear()
    body = admin_client.get("/api/admin/coverage").json()
    assert body["available"] is True
    assert body["backend"]["pct"] == 95.2
    assert body["frontend"]["pct"] == 88.1
    assert body["git_sha"] == "abc1234"
    get_settings.cache_clear()


# ----------------------------------------------------------- architecture


def test_architecture_returns_docs(admin_client: TestClient) -> None:
    # Tests run from the repo root, so the default docs_dir resolves to docs/.
    body = admin_client.get("/api/admin/architecture").json()
    names = [doc["name"] for doc in body["docs"]]
    assert "architecture.md" in names
    arch = next(doc for doc in body["docs"] if doc["name"] == "architecture.md")
    assert arch["markdown"].strip()
    assert arch["title"]


# -------------------------------------------------------- user management


def test_list_users_includes_seeded(admin_client: TestClient, admin_user: AuthUser) -> None:
    users = admin_client.get("/api/admin/users").json()
    me = next(u for u in users if u["username"] == admin_user.username)
    assert me["is_admin"] is True
    assert me["is_active"] is True
    assert "password" not in json.dumps(users).lower()


def test_create_user_then_login(admin_client: TestClient) -> None:
    payload = {"username": "w2-created", "password": "a-strong-pw-12345", "is_admin": False}
    created = admin_client.post("/api/admin/users", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "w2-created"
    assert created.json()["is_admin"] is False


def test_create_user_duplicate_409(admin_client: TestClient, auth_user: AuthUser) -> None:
    response = admin_client.post(
        "/api/admin/users", json={"username": auth_user.username, "password": "whatever-pw"}
    )
    assert response.status_code == 409


def test_patch_promote_and_demote(
    admin_client: TestClient, api_database_url: str
) -> None:
    target = create_user(api_database_url)
    promote = admin_client.patch(f"/api/admin/users/{target.id}", json={"is_admin": True})
    assert promote.status_code == 200, promote.text
    assert promote.json()["is_admin"] is True

    demote = admin_client.patch(f"/api/admin/users/{target.id}", json={"is_admin": False})
    assert demote.status_code == 200
    assert demote.json()["is_admin"] is False


def test_patch_unknown_user_404(admin_client: TestClient) -> None:
    response = admin_client.patch("/api/admin/users/99999999", json={"is_admin": True})
    assert response.status_code == 404


def test_cannot_demote_last_active_admin(
    api_app: object, api_database_url: str
) -> None:
    """In a DB with exactly one active admin, demoting them is rejected (409)."""
    from procrastinate import PsycopgConnector

    from library.jobs import job_app, procrastinate_conninfo
    from tests.conftest import login

    # Make this admin the *only* active admin: demote any others first.
    sole = create_user(api_database_url, is_admin=True)
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as client:  # type: ignore[arg-type]
        login(client, sole)
        users = client.get("/api/admin/users").json()
        for other in users:
            if other["id"] != sole.id and other["is_admin"] and other["is_active"]:
                client.patch(f"/api/admin/users/{other['id']}", json={"is_admin": False})
        # Now the sole admin demoting themselves must fail.
        response = client.patch(f"/api/admin/users/{sole.id}", json={"is_admin": False})
        assert response.status_code == 409, response.text
        # And they remain admin.
        assert client.get("/api/admin/users").json()  # still authorized → still admin
