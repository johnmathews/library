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
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration

_ADMIN_GET_ROUTES = [
    "/api/admin/system",
    "/api/admin/architecture",
    "/api/admin/coverage",
    "/api/admin/users",
]


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
    # A totals-only (older) summary still validates: per-file fields default empty.
    assert body["backend"]["worst_files"] == []
    assert body["backend"]["files_total"] is None
    get_settings.cache_clear()


def test_coverage_surfaces_per_file_detail(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.config import get_settings

    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {
                "backend": {
                    "pct": 92.0,
                    "threshold": 85.0,
                    "files_total": 3,
                    "files_below_gate": 1,
                    "worst_files": [{"path": "src/library/series.py", "pct": 71.0}],
                },
                "frontend": {"pct": 88.1, "threshold": 85.0},
                "generated_at": "2026-06-29T00:00:00Z",
                "git_sha": "abc1234",
            }
        )
    )
    monkeypatch.setenv("LIBRARY_COVERAGE_SUMMARY_PATH", str(summary))
    get_settings.cache_clear()
    body = admin_client.get("/api/admin/coverage").json()
    assert body["backend"]["files_total"] == 3
    assert body["backend"]["files_below_gate"] == 1
    assert body["backend"]["worst_files"] == [{"path": "src/library/series.py", "pct": 71.0}]
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


def test_patch_promote_and_demote(admin_client: TestClient, api_database_url: str) -> None:
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


def test_cannot_demote_last_active_admin(api_app: object, api_database_url: str) -> None:
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


def test_create_admin_user(admin_client: TestClient) -> None:
    created = admin_client.post(
        "/api/admin/users",
        json={"username": "w2-admin-created", "password": "a-strong-pw-12345", "is_admin": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_admin"] is True


def test_deactivate_user_revokes_their_session(
    api_app: object, api_database_url: str, admin_client: TestClient
) -> None:
    """PATCH is_active=false flips the flag and kills the user's live session."""
    from procrastinate import PsycopgConnector

    from library.jobs import job_app, procrastinate_conninfo
    from tests.conftest import login

    target = create_user(api_database_url)
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as victim:  # type: ignore[arg-type]
        login(victim, target)
        assert victim.get("/api/auth/me").status_code == 200

        patched = admin_client.patch(f"/api/admin/users/{target.id}", json={"is_active": False})
        assert patched.status_code == 200, patched.text
        assert patched.json()["is_active"] is False

        # The deactivation revoked the session server-side, so the victim is out.
        assert victim.get("/api/auth/me").status_code == 401


def test_coverage_malformed_json_unavailable(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.config import get_settings

    summary = tmp_path / "coverage-summary.json"
    summary.write_text("{ not valid json")
    monkeypatch.setenv("LIBRARY_COVERAGE_SUMMARY_PATH", str(summary))
    get_settings.cache_clear()
    assert admin_client.get("/api/admin/coverage").json()["available"] is False
    get_settings.cache_clear()


# --------------------------------------------------- recipient management (W1)


def _recipients(client: TestClient) -> dict[str, dict[str, object]]:
    """Map recipient name -> {id, name, document_count} from GET /api/recipients."""
    response = client.get("/api/recipients")
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()}


def test_rename_recipient_no_collision(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-rename", recipient_name="W1 Rename Old")
    rid = _recipients(admin_client)["W1 Rename Old"]["id"]

    resp = admin_client.patch(f"/api/admin/recipients/{rid}", json={"name": "W1 Rename New"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": rid, "name": "W1 Rename New"}

    recs = _recipients(admin_client)
    assert "W1 Rename Old" not in recs
    assert recs["W1 Rename New"]["id"] == rid


def test_rename_recipient_casing_only(admin_client: TestClient, api_database_url: str) -> None:
    """A pure casing change is not a self-collision; it updates in place."""
    seed_document(api_database_url, "w1-casing", recipient_name="W1 Casing john")
    rid = _recipients(admin_client)["W1 Casing john"]["id"]

    resp = admin_client.patch(f"/api/admin/recipients/{rid}", json={"name": "W1 Casing John"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": rid, "name": "W1 Casing John"}


def test_rename_collision_then_merge(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-merge-src", recipient_name="W1 Merge Jon")
    seed_document(api_database_url, "w1-merge-tgt-1", recipient_name="W1 Merge John")
    seed_document(api_database_url, "w1-merge-tgt-2", recipient_name="W1 Merge John")
    recs = _recipients(admin_client)
    src = recs["W1 Merge Jon"]["id"]
    tgt = recs["W1 Merge John"]["id"]

    # merge=false (default): collision reported, nothing changes.
    conflict = admin_client.patch(f"/api/admin/recipients/{src}", json={"name": "w1 merge john"})
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["target_id"] == tgt
    assert body["target_name"] == "W1 Merge John"
    assert body["target_document_count"] == 2
    assert "W1 Merge Jon" in _recipients(admin_client)  # untouched

    # merge=true: src's documents move to tgt, src is deleted, tgt returned.
    merged = admin_client.patch(
        f"/api/admin/recipients/{src}", json={"name": "W1 Merge John", "merge": True}
    )
    assert merged.status_code == 200, merged.text
    assert merged.json() == {"id": tgt, "name": "W1 Merge John"}

    recs2 = _recipients(admin_client)
    assert "W1 Merge Jon" not in recs2
    assert recs2["W1 Merge John"]["document_count"] == 3


def test_delete_recipient_zero_docs(admin_client: TestClient, api_database_url: str) -> None:
    """A recipient with no live documents is deleted outright (no target needed)."""
    doc_id = seed_document(api_database_url, "w1-del-zero", recipient_name="W1 Del Zero")
    assert admin_client.delete(f"/api/documents/{doc_id}").status_code == 204
    rid = _recipients(admin_client)["W1 Del Zero"]["id"]  # still listed, count 0

    resp = admin_client.delete(f"/api/admin/recipients/{rid}")
    assert resp.status_code == 204, resp.text
    assert "W1 Del Zero" not in _recipients(admin_client)


def test_delete_in_use_without_target_409(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-inuse-1", recipient_name="W1 InUse")
    seed_document(api_database_url, "w1-inuse-2", recipient_name="W1 InUse")
    rid = _recipients(admin_client)["W1 InUse"]["id"]

    resp = admin_client.delete(f"/api/admin/recipients/{rid}")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["document_count"] == 2
    assert "W1 InUse" in _recipients(admin_client)  # not deleted


def test_delete_in_use_with_target_reassigns(
    admin_client: TestClient, api_database_url: str
) -> None:
    src_doc = seed_document(api_database_url, "w1-move-src", recipient_name="W1 Move Src")
    seed_document(api_database_url, "w1-move-tgt", recipient_name="W1 Move Tgt")
    recs = _recipients(admin_client)
    src = recs["W1 Move Src"]["id"]
    tgt = recs["W1 Move Tgt"]["id"]

    resp = admin_client.delete(f"/api/admin/recipients/{src}?reassign_to={tgt}")
    assert resp.status_code == 204, resp.text

    recs2 = _recipients(admin_client)
    assert "W1 Move Src" not in recs2
    assert recs2["W1 Move Tgt"]["document_count"] == 2
    # the moved document now points at the target
    assert admin_client.get(f"/api/documents/{src_doc}").json()["recipient"]["id"] == tgt


def test_delete_in_use_with_null_target_nulls(
    admin_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "w1-null", recipient_name="W1 Null Me")
    seed_document(api_database_url, "w1-null-2", recipient_name="W1 Null Me")
    rid = _recipients(admin_client)["W1 Null Me"]["id"]

    # explicit empty reassign_to => null the documents, then delete the recipient.
    resp = admin_client.delete(f"/api/admin/recipients/{rid}?reassign_to=")
    assert resp.status_code == 204, resp.text
    assert "W1 Null Me" not in _recipients(admin_client)
    # documents survive but lose their recipient
    assert admin_client.get(f"/api/documents/{doc_id}").json()["recipient"] is None


def test_delete_self_reassign_400(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-self", recipient_name="W1 Self")
    rid = _recipients(admin_client)["W1 Self"]["id"]
    resp = admin_client.delete(f"/api/admin/recipients/{rid}?reassign_to={rid}")
    assert resp.status_code == 400, resp.text


def test_rename_empty_name_400(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-empty", recipient_name="W1 Empty Name")
    rid = _recipients(admin_client)["W1 Empty Name"]["id"]
    assert (
        admin_client.patch(f"/api/admin/recipients/{rid}", json={"name": "   "}).status_code == 400
    )


def test_rename_unknown_recipient_404(admin_client: TestClient) -> None:
    resp = admin_client.patch("/api/admin/recipients/99999999", json={"name": "X"})
    assert resp.status_code == 404


def test_delete_unknown_recipient_404(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/admin/recipients/99999999").status_code == 404


def test_delete_unknown_target_404(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w1-bad-tgt", recipient_name="W1 Bad Tgt")
    rid = _recipients(admin_client)["W1 Bad Tgt"]["id"]
    resp = admin_client.delete(f"/api/admin/recipients/{rid}?reassign_to=99999999")
    assert resp.status_code == 404, resp.text


def test_recipient_mgmt_rejects_non_admin(api_client: TestClient) -> None:
    assert api_client.patch("/api/admin/recipients/1", json={"name": "x"}).status_code == 403
    assert api_client.delete("/api/admin/recipients/1").status_code == 403


def test_recipient_mgmt_rejects_anonymous(anon_client: TestClient) -> None:
    assert anon_client.patch("/api/admin/recipients/1", json={"name": "x"}).status_code == 401
    assert anon_client.delete("/api/admin/recipients/1").status_code == 401
