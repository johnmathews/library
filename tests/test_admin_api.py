"""Integration tests for the admin-only API (W2): /api/admin/*.

Gated by ``require_admin``: anonymous → 401, non-admin → 403, admin → 200.
Uses the shared API test database; assertions target seeded markers and
shapes rather than exact table contents.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library import taxonomy
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
    # An older summary with no test_types list still validates (defaults empty).
    assert body["test_types"] == []
    get_settings.cache_clear()


def test_coverage_surfaces_ci_test_types(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coverage endpoint passes through the CI test-type enumeration."""
    from library.config import get_settings

    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {
                "backend": {"pct": 95.0, "threshold": 85.0},
                "frontend": {"pct": 88.0, "threshold": 85.0},
                "test_types": [
                    {
                        "key": "e2e",
                        "label": "End-to-end",
                        "runner": "Playwright",
                        "has_coverage": False,
                        "description": "Browser flows; pass/fail gate.",
                    },
                ],
                "generated_at": None,
                "git_sha": None,
            }
        )
    )
    monkeypatch.setenv("LIBRARY_COVERAGE_SUMMARY_PATH", str(summary))
    get_settings.cache_clear()
    body = admin_client.get("/api/admin/coverage").json()
    assert body["test_types"][0]["key"] == "e2e"
    assert body["test_types"][0]["has_coverage"] is False
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


# --------------------------------------------------- user ↔ recipient (W13)


def test_create_user_creates_linked_recipient(admin_client: TestClient) -> None:
    """Creating a user auto-creates a recipient named by the display name."""
    created = admin_client.post(
        "/api/admin/users",
        json={
            "username": "w13-display",
            "password": "a-strong-pw-12345",
            "display_name": "Wanda Display",
        },
    )
    assert created.status_code == 201, created.text
    assert "Wanda Display" in _recipients(admin_client)


def test_create_user_recipient_falls_back_to_username(admin_client: TestClient) -> None:
    """With no display name, the auto-created recipient is named by username."""
    created = admin_client.post(
        "/api/admin/users",
        json={"username": "w13-nodisplay", "password": "a-strong-pw-12345"},
    )
    assert created.status_code == 201, created.text
    assert "w13-nodisplay" in _recipients(admin_client)


def test_delete_normal_user(admin_client: TestClient, api_database_url: str) -> None:
    target = create_user(api_database_url)
    resp = admin_client.delete(f"/api/admin/users/{target.id}")
    assert resp.status_code == 204, resp.text
    users = admin_client.get("/api/admin/users").json()
    assert all(u["id"] != target.id for u in users)


def test_delete_non_last_admin(admin_client: TestClient, api_database_url: str) -> None:
    """An admin can delete another admin while an active admin still remains."""
    other_admin = create_user(api_database_url, is_admin=True)
    resp = admin_client.delete(f"/api/admin/users/{other_admin.id}")
    assert resp.status_code == 204, resp.text


def test_delete_user_unknown_404(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/admin/users/99999999").status_code == 404


def test_cannot_delete_self(
    admin_client: TestClient, admin_user: AuthUser, api_database_url: str
) -> None:
    """Deleting your own account is rejected (400), even with other admins present."""
    create_user(api_database_url, is_admin=True)  # ensure the caller is not the last admin
    resp = admin_client.delete(f"/api/admin/users/{admin_user.id}")
    assert resp.status_code == 400, resp.text
    assert admin_client.get("/api/admin/users").status_code == 200  # still authorized


def test_cannot_delete_last_active_admin(api_app: object, api_database_url: str) -> None:
    """In a DB with exactly one active admin, deleting them is rejected (409)."""
    from procrastinate import PsycopgConnector

    from library.jobs import job_app, procrastinate_conninfo
    from tests.conftest import login

    sole = create_user(api_database_url, is_admin=True)
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as client:  # type: ignore[arg-type]
        login(client, sole)
        users = client.get("/api/admin/users").json()
        for other in users:
            if other["id"] != sole.id and other["is_admin"] and other["is_active"]:
                client.patch(f"/api/admin/users/{other['id']}", json={"is_admin": False})
        # The last active admin cannot delete themselves (last-admin guard, 409).
        response = client.delete(f"/api/admin/users/{sole.id}")
        assert response.status_code == 409, response.text
        assert client.get("/api/admin/users").json()  # still authorized → still admin


def test_deleted_user_recipient_survives_unlinked(
    admin_client: TestClient, api_database_url: str
) -> None:
    """Deleting a user keeps their recipient (FK SET NULL), so docs stay addressed."""
    created = admin_client.post(
        "/api/admin/users",
        json={
            "username": "w13-survivor",
            "password": "a-strong-pw-12345",
            "display_name": "Survivor Rec",
        },
    ).json()
    assert "Survivor Rec" in _recipients(admin_client)
    resp = admin_client.delete(f"/api/admin/users/{created['id']}")
    assert resp.status_code == 204, resp.text
    # The recipient row outlives the user (merely unlinked).
    assert "Survivor Rec" in _recipients(admin_client)


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


# ------------------------------------------------ recipient create (W4)


def test_create_recipient_new_then_dedupes(admin_client: TestClient, api_database_url: str) -> None:
    resp = admin_client.post("/api/admin/recipients", json={"name": "W4 New Recipient"})
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    assert resp.json()["name"] == "W4 New Recipient"
    assert _recipients(admin_client)["W4 New Recipient"]["id"] == rid

    # Case-insensitive match returns the existing row with 200 (no duplicate).
    again = admin_client.post("/api/admin/recipients", json={"name": "w4 new recipient"})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == rid


def test_create_recipient_empty_422(admin_client: TestClient) -> None:
    assert admin_client.post("/api/admin/recipients", json={"name": "   "}).status_code == 422


# ------------------------------------------------------- sender management (W4)


def _senders(client: TestClient) -> dict[str, dict[str, object]]:
    """Map sender name -> {id, name, document_count} from GET /api/senders."""
    response = client.get("/api/senders")
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()}


def test_create_sender_new_then_dedupes(admin_client: TestClient) -> None:
    resp = admin_client.post("/api/admin/senders", json={"name": "W4 Sender Co"})
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    again = admin_client.post("/api/admin/senders", json={"name": "w4 sender co"})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == sid


def test_create_sender_empty_422(admin_client: TestClient) -> None:
    assert admin_client.post("/api/admin/senders", json={"name": ""}).status_code == 422


def test_rename_sender_no_collision(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w4-s-rename", sender_name="W4 S Old")
    sid = _senders(admin_client)["W4 S Old"]["id"]
    resp = admin_client.patch(f"/api/admin/senders/{sid}", json={"name": "W4 S New"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": sid, "name": "W4 S New"}
    senders = _senders(admin_client)
    assert "W4 S Old" not in senders
    assert senders["W4 S New"]["id"] == sid


def test_rename_sender_collision_then_merge(
    admin_client: TestClient, api_database_url: str
) -> None:
    seed_document(api_database_url, "w4-s-src", sender_name="W4 S Src")
    seed_document(api_database_url, "w4-s-tgt-1", sender_name="W4 S Tgt")
    seed_document(api_database_url, "w4-s-tgt-2", sender_name="W4 S Tgt")
    senders = _senders(admin_client)
    src = senders["W4 S Src"]["id"]
    tgt = senders["W4 S Tgt"]["id"]

    conflict = admin_client.patch(f"/api/admin/senders/{src}", json={"name": "w4 s tgt"})
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert body["target_id"] == tgt
    assert body["target_name"] == "W4 S Tgt"
    assert body["target_document_count"] == 2
    assert "W4 S Src" in _senders(admin_client)  # untouched

    merged = admin_client.patch(
        f"/api/admin/senders/{src}", json={"name": "W4 S Tgt", "merge": True}
    )
    assert merged.status_code == 200, merged.text
    assert merged.json() == {"id": tgt, "name": "W4 S Tgt"}
    senders2 = _senders(admin_client)
    assert "W4 S Src" not in senders2
    assert senders2["W4 S Tgt"]["document_count"] == 3


def test_delete_sender_zero_docs(admin_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "w4-s-del-zero", sender_name="W4 S Del Zero")
    assert admin_client.delete(f"/api/documents/{doc_id}").status_code == 204
    sid = _senders(admin_client)["W4 S Del Zero"]["id"]
    assert admin_client.delete(f"/api/admin/senders/{sid}").status_code == 204
    assert "W4 S Del Zero" not in _senders(admin_client)


def test_delete_sender_in_use_without_target_409(
    admin_client: TestClient, api_database_url: str
) -> None:
    seed_document(api_database_url, "w4-s-inuse-1", sender_name="W4 S InUse")
    seed_document(api_database_url, "w4-s-inuse-2", sender_name="W4 S InUse")
    sid = _senders(admin_client)["W4 S InUse"]["id"]
    resp = admin_client.delete(f"/api/admin/senders/{sid}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["document_count"] == 2
    assert "W4 S InUse" in _senders(admin_client)


def test_delete_sender_in_use_with_target_reassigns(
    admin_client: TestClient, api_database_url: str
) -> None:
    src_doc = seed_document(api_database_url, "w4-s-move-src", sender_name="W4 S Move Src")
    seed_document(api_database_url, "w4-s-move-tgt", sender_name="W4 S Move Tgt")
    senders = _senders(admin_client)
    src = senders["W4 S Move Src"]["id"]
    tgt = senders["W4 S Move Tgt"]["id"]
    resp = admin_client.delete(f"/api/admin/senders/{src}?reassign_to={tgt}")
    assert resp.status_code == 204, resp.text
    senders2 = _senders(admin_client)
    assert "W4 S Move Src" not in senders2
    assert senders2["W4 S Move Tgt"]["document_count"] == 2
    assert admin_client.get(f"/api/documents/{src_doc}").json()["sender"]["id"] == tgt


def test_delete_sender_in_use_with_null_target_nulls(
    admin_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "w4-s-null", sender_name="W4 S Null")
    sid = _senders(admin_client)["W4 S Null"]["id"]
    resp = admin_client.delete(f"/api/admin/senders/{sid}?reassign_to=")
    assert resp.status_code == 204, resp.text
    assert "W4 S Null" not in _senders(admin_client)
    assert admin_client.get(f"/api/documents/{doc_id}").json()["sender"] is None


def test_sender_mgmt_errors(admin_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w4-s-self", sender_name="W4 S Self")
    sid = _senders(admin_client)["W4 S Self"]["id"]
    assert admin_client.delete(f"/api/admin/senders/{sid}?reassign_to={sid}").status_code == 400
    assert admin_client.patch(f"/api/admin/senders/{sid}", json={"name": "  "}).status_code == 400
    assert admin_client.patch("/api/admin/senders/99999999", json={"name": "X"}).status_code == 404
    assert admin_client.delete("/api/admin/senders/99999999").status_code == 404


def test_sender_mgmt_rejects_non_admin_and_anon(
    api_client: TestClient, anon_client: TestClient
) -> None:
    assert api_client.post("/api/admin/senders", json={"name": "x"}).status_code == 403
    assert api_client.patch("/api/admin/senders/1", json={"name": "x"}).status_code == 403
    assert api_client.delete("/api/admin/senders/1").status_code == 403
    assert anon_client.post("/api/admin/senders", json={"name": "x"}).status_code == 401


# --------------------------------------------------------- kind management (W4)


def _kinds(client: TestClient) -> dict[str, dict[str, object]]:
    """Map kind slug -> {slug, name, document_count} from GET /api/kinds."""
    response = client.get("/api/kinds")
    assert response.status_code == 200, response.text
    return {item["slug"]: item for item in response.json()}


def _make_kind(client: TestClient, name: str) -> str:
    """Create a kind via POST /api/kinds and return its slug."""
    resp = client.post("/api/kinds", json={"name": name})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["slug"]


def test_rename_kind_name_only_slug_immutable(admin_client: TestClient) -> None:
    slug = _make_kind(admin_client, "W4 Kind Alpha")
    resp = admin_client.patch(f"/api/admin/kinds/{slug}", json={"name": "W4 Kind Renamed"})
    assert resp.status_code == 200, resp.text
    # Slug is unchanged; only the display name updated. Names are standardised to
    # sentence case (matching create_kind and the seeded convention).
    assert resp.json() == {"slug": slug, "name": "W4 kind renamed"}
    assert _kinds(admin_client)[slug]["name"] == "W4 kind renamed"


def test_rename_kind_name_collision_409(admin_client: TestClient) -> None:
    slug_a = _make_kind(admin_client, "W4 Kind Bravo")
    _make_kind(admin_client, "W4 Kind Charlie")
    # Renaming Bravo to Charlie's name collides (no kind-merge). Names are
    # standardised, so the stored/target name is sentence-cased.
    resp = admin_client.patch(f"/api/admin/kinds/{slug_a}", json={"name": "W4 Kind Charlie"})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["target_name"] == "W4 kind charlie"
    assert isinstance(body["target_slug"], str)


def test_rename_kind_errors(admin_client: TestClient) -> None:
    slug = _make_kind(admin_client, "W4 Kind Delta")
    assert admin_client.patch(f"/api/admin/kinds/{slug}", json={"name": " "}).status_code == 400
    assert (
        admin_client.patch("/api/admin/kinds/no-such-kind", json={"name": "X"}).status_code == 404
    )


def test_delete_kind_zero_docs(admin_client: TestClient) -> None:
    slug = _make_kind(admin_client, "W4 Kind Echo")
    assert admin_client.delete(f"/api/admin/kinds/{slug}").status_code == 204
    assert slug not in _kinds(admin_client)


def test_delete_kind_in_use_flows(admin_client: TestClient, api_database_url: str) -> None:
    src_slug = _make_kind(admin_client, "W4 Kind Foxtrot")
    tgt_slug = _make_kind(admin_client, "W4 Kind Golf")
    doc_id = seed_document(api_database_url, "w4-k-move", kind_slug=src_slug)

    # In use without a target -> 409.
    conflict = admin_client.delete(f"/api/admin/kinds/{src_slug}")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["document_count"] == 1

    # Reassign to another kind by slug -> 204, doc moves.
    resp = admin_client.delete(f"/api/admin/kinds/{src_slug}?reassign_to={tgt_slug}")
    assert resp.status_code == 204, resp.text
    assert src_slug not in _kinds(admin_client)
    assert admin_client.get(f"/api/documents/{doc_id}").json()["kind"]["slug"] == tgt_slug


def test_delete_kind_null_target_and_self_and_unknown(
    admin_client: TestClient, api_database_url: str
) -> None:
    slug = _make_kind(admin_client, "W4 Kind Hotel")
    doc_id = seed_document(api_database_url, "w4-k-null", kind_slug=slug)
    # self-reassign -> 400
    assert admin_client.delete(f"/api/admin/kinds/{slug}?reassign_to={slug}").status_code == 400
    # unknown reassignment target -> 404
    assert (
        admin_client.delete(f"/api/admin/kinds/{slug}?reassign_to=no-such-kind").status_code == 404
    )
    # explicit null -> nulls the doc's kind, then deletes
    resp = admin_client.delete(f"/api/admin/kinds/{slug}?reassign_to=")
    assert resp.status_code == 204, resp.text
    assert admin_client.get(f"/api/documents/{doc_id}").json()["kind"] is None
    assert admin_client.delete("/api/admin/kinds/no-such-kind").status_code == 404


def test_kind_mgmt_rejects_non_admin_and_anon(
    api_client: TestClient, anon_client: TestClient
) -> None:
    assert api_client.patch("/api/admin/kinds/invoice", json={"name": "x"}).status_code == 403
    assert api_client.delete("/api/admin/kinds/invoice").status_code == 403
    assert anon_client.delete("/api/admin/kinds/invoice").status_code == 401


# ------------------------------------- taxonomy service-layer branches (W1)
#
# The admin routes above exercise these mutations end-to-end, but the FastAPI
# handlers run inside TestClient's own event-loop thread, which the coverage
# tracer does not follow — so the merge-execution and reassign-and-delete
# branches of ``library.taxonomy`` show as uncovered even though the HTTP tests
# pass. These cases call the async services directly in the main thread (the
# same ``asyncio.run`` pattern conftest uses for seeding) so the bulk-reassign
# code is genuinely exercised, and assert the observable outcome the way the
# HTTP tests do: the seeded document's FK, read back through the API, actually
# moved (or was nulled) and the source entity is gone.


def _run_service[T](api_database_url: str, op: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run a taxonomy service against the API test DB in the main thread.

    A short-lived NullPool engine (mirroring the app's per-request session and
    conftest's own seeding helpers) so the service commits against the same
    database the ``admin_client`` reads from.
    """

    async def _body() -> T:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await op(session)
        finally:
            await engine.dispose()

    return asyncio.run(_body())


def test_sender_merge_reassigns_docs_and_deletes_source(
    admin_client: TestClient, api_database_url: str
) -> None:
    """rename_sender with merge=True re-points the source's docs then deletes it."""
    src_doc = seed_document(
        api_database_url, "w1-svc-s-merge-src", sender_name="W1 Svc S Merge Src"
    )
    seed_document(api_database_url, "w1-svc-s-merge-tgt", sender_name="W1 Svc S Merge Tgt")
    senders = _senders(admin_client)
    src = senders["W1 Svc S Merge Src"]["id"]
    tgt = senders["W1 Svc S Merge Tgt"]["id"]

    result = _run_service(
        api_database_url,
        lambda s: taxonomy.rename_sender(s, src, "W1 Svc S Merge Tgt", merge=True),
    )
    assert result.status == "merged"
    assert result.sender is not None and result.sender.id == tgt

    assert "W1 Svc S Merge Src" not in _senders(admin_client)
    # The source's document now belongs to the surviving target sender.
    assert admin_client.get(f"/api/documents/{src_doc}").json()["sender"]["id"] == tgt


def test_service_reassign_and_delete_sender_to_target(
    admin_client: TestClient, api_database_url: str
) -> None:
    src_doc = seed_document(api_database_url, "w1-svc-s-move-src", sender_name="W1 Svc S Move Src")
    seed_document(api_database_url, "w1-svc-s-move-tgt", sender_name="W1 Svc S Move Tgt")
    senders = _senders(admin_client)
    src = senders["W1 Svc S Move Src"]["id"]
    tgt = senders["W1 Svc S Move Tgt"]["id"]

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_sender(s, src, tgt)
    )
    assert result.status == "deleted"
    assert "W1 Svc S Move Src" not in _senders(admin_client)
    assert admin_client.get(f"/api/documents/{src_doc}").json()["sender"]["id"] == tgt


def test_service_reassign_and_delete_sender_null_clears(
    admin_client: TestClient, api_database_url: str
) -> None:
    doc = seed_document(api_database_url, "w1-svc-s-null", sender_name="W1 Svc S Null")
    sid = _senders(admin_client)["W1 Svc S Null"]["id"]

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_sender(s, sid, None)
    )
    assert result.status == "deleted"
    assert "W1 Svc S Null" not in _senders(admin_client)
    assert admin_client.get(f"/api/documents/{doc}").json()["sender"] is None


def test_service_reassign_and_delete_sender_error_branches(
    admin_client: TestClient, api_database_url: str
) -> None:
    seed_document(api_database_url, "w1-svc-s-err", sender_name="W1 Svc S Err")
    sid = _senders(admin_client)["W1 Svc S Err"]["id"]

    # Self-reassign and an unknown target are both refused before any mutation.
    self_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_sender(s, sid, sid)
    )
    assert self_res.status == "self_reassign"
    missing_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_sender(s, sid, 99999999)
    )
    assert missing_res.status == "target_not_found"
    assert "W1 Svc S Err" in _senders(admin_client)  # survives both refusals


def test_service_reassign_and_delete_kind_to_target(
    admin_client: TestClient, api_database_url: str
) -> None:
    src_slug = _make_kind(admin_client, "W1 Svc Kind Src")
    tgt_slug = _make_kind(admin_client, "W1 Svc Kind Tgt")
    doc = seed_document(api_database_url, "w1-svc-k-move", kind_slug=src_slug)

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_kind(s, src_slug, tgt_slug)
    )
    assert result.status == "deleted"
    assert src_slug not in _kinds(admin_client)
    assert admin_client.get(f"/api/documents/{doc}").json()["kind"]["slug"] == tgt_slug


def test_service_reassign_and_delete_kind_null_clears(
    admin_client: TestClient, api_database_url: str
) -> None:
    slug = _make_kind(admin_client, "W1 Svc Kind Null")
    doc = seed_document(api_database_url, "w1-svc-k-null", kind_slug=slug)

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_kind(s, slug, None)
    )
    assert result.status == "deleted"
    assert slug not in _kinds(admin_client)
    assert admin_client.get(f"/api/documents/{doc}").json()["kind"] is None


def test_service_reassign_and_delete_kind_error_branches(
    admin_client: TestClient, api_database_url: str
) -> None:
    slug = _make_kind(admin_client, "W1 Svc Kind Err")
    seed_document(api_database_url, "w1-svc-k-err", kind_slug=slug)

    self_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_kind(s, slug, slug)
    )
    assert self_res.status == "self_reassign"
    missing_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_kind(s, slug, "no-such-kind-xyz")
    )
    assert missing_res.status == "target_not_found"
    assert slug in _kinds(admin_client)


def test_service_reassign_and_delete_recipient_to_target(
    admin_client: TestClient, api_database_url: str
) -> None:
    src_doc = seed_document(
        api_database_url, "w1-svc-r-move-src", recipient_name="W1 Svc R Move Src"
    )
    seed_document(api_database_url, "w1-svc-r-move-tgt", recipient_name="W1 Svc R Move Tgt")
    recs = _recipients(admin_client)
    src = recs["W1 Svc R Move Src"]["id"]
    tgt = recs["W1 Svc R Move Tgt"]["id"]

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_recipient(s, src, tgt)
    )
    assert result.status == "deleted"
    assert "W1 Svc R Move Src" not in _recipients(admin_client)
    assert admin_client.get(f"/api/documents/{src_doc}").json()["recipient"]["id"] == tgt


def test_service_reassign_and_delete_recipient_null_clears(
    admin_client: TestClient, api_database_url: str
) -> None:
    doc = seed_document(api_database_url, "w1-svc-r-null", recipient_name="W1 Svc R Null")
    rid = _recipients(admin_client)["W1 Svc R Null"]["id"]

    result = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_recipient(s, rid, None)
    )
    assert result.status == "deleted"
    assert "W1 Svc R Null" not in _recipients(admin_client)
    assert admin_client.get(f"/api/documents/{doc}").json()["recipient"] is None


def test_service_reassign_and_delete_recipient_error_branches(
    admin_client: TestClient, api_database_url: str
) -> None:
    seed_document(api_database_url, "w1-svc-r-err", recipient_name="W1 Svc R Err")
    rid = _recipients(admin_client)["W1 Svc R Err"]["id"]

    self_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_recipient(s, rid, rid)
    )
    assert self_res.status == "self_reassign"
    missing_res = _run_service(
        api_database_url, lambda s: taxonomy.reassign_and_delete_recipient(s, rid, 99999999)
    )
    assert missing_res.status == "target_not_found"
    assert "W1 Svc R Err" in _recipients(admin_client)
