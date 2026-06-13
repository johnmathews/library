"""Integration tests for W8 authentication: sessions, CSRF, API tokens.

Uses the shared API test database; usernames are randomized per test
(see ``create_user`` in conftest), so tests do not collide.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from library.auth.deps import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from tests.conftest import AuthUser, create_user, fetch_all, login

pytestmark = pytest.mark.integration


async def _execute_sql(database_url: str, query: str, **params: object) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(query), params)
    finally:
        await engine.dispose()


def execute_sql(database_url: str, query: str, **params: object) -> None:
    asyncio.run(_execute_sql(database_url, query, **params))


def _set_cookie_header(response_headers: list[str], cookie_name: str) -> str:
    for header in response_headers:
        if header.startswith(f"{cookie_name}="):
            return header
    raise AssertionError(f"no Set-Cookie for {cookie_name}")


def make_token(client: TestClient, name: str = "test-token") -> dict[str, object]:
    response = client.post("/api/auth/tokens", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- login/logout


def test_login_sets_cookies_and_returns_user(anon_client: TestClient, auth_user: AuthUser) -> None:
    response = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": auth_user.password}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == auth_user.username
    assert body["id"] == auth_user.id
    assert "password" not in body and "password_hash" not in body

    set_cookies = response.headers.get_list("set-cookie")
    session_cookie = _set_cookie_header(set_cookies, SESSION_COOKIE)
    csrf_cookie = _set_cookie_header(set_cookies, CSRF_COOKIE)
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()  # JS must read it
    assert "samesite=lax" in csrf_cookie.lower()
    # cookie_secure=false in tests, so no Secure flag here.
    assert "secure" not in session_cookie.lower()


def test_login_cookie_secure_flag_follows_settings(
    api_app: FastAPI,
    api_database_url: str,
    auth_user: AuthUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from procrastinate.testing import InMemoryConnector

    from library.config import get_settings
    from library.jobs import job_app

    monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        with job_app.replace_connector(InMemoryConnector()), TestClient(api_app) as client:
            response = client.post(
                "/api/auth/login",
                json={"username": auth_user.username, "password": auth_user.password},
            )
            assert response.status_code == 200
            session_cookie = _set_cookie_header(
                response.headers.get_list("set-cookie"), SESSION_COOKIE
            )
            assert "secure" in session_cookie.lower()
    finally:
        monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "false")
        get_settings.cache_clear()


def test_login_wrong_password_and_disabled_user_same_401(
    anon_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    disabled = create_user(api_database_url, is_active=False)
    wrong = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": "nope"}
    )
    unknown = anon_client.post(
        "/api/auth/login", json={"username": "no-such-user", "password": "nope"}
    )
    off = anon_client.post(
        "/api/auth/login", json={"username": disabled.username, "password": disabled.password}
    )
    assert wrong.status_code == unknown.status_code == off.status_code == 401
    # Identical bodies: no account enumeration.
    assert wrong.json() == unknown.json() == off.json()
    assert SESSION_COOKIE not in anon_client.cookies


def test_session_token_stored_hashed(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    raw = api_client.cookies[SESSION_COOKIE]
    rows = fetch_all(
        api_database_url,
        "SELECT token_hash FROM sessions WHERE user_id = :uid",
        uid=auth_user.id,
    )
    hashes = {row[0] for row in rows}
    assert raw not in hashes
    assert hashlib.sha256(raw.encode()).hexdigest() in hashes


def test_me_returns_current_user(api_client: TestClient, auth_user: AuthUser) -> None:
    body = api_client.get("/api/auth/me").json()
    assert body["id"] == auth_user.id
    assert body["username"] == auth_user.username
    assert "password" not in body and "password_hash" not in body


def test_me_includes_default_preferences(api_client: TestClient) -> None:
    body = api_client.get("/api/auth/me").json()
    assert body["preferences"]["dashboard_fields"] == [
        "kind",
        "sender",
        "tags",
        "date",
        "language",
        "status",
    ]


def test_logout_deletes_session_and_clears_cookies(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    raw = api_client.cookies[SESSION_COOKIE]
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    response = api_client.post("/api/auth/logout")
    assert response.status_code == 204
    rows = fetch_all(api_database_url, "SELECT 1 FROM sessions WHERE token_hash = :h", h=token_hash)
    assert rows == []
    # The cookie value is dead server-side even if a client kept it.
    api_client.cookies.set(SESSION_COOKIE, raw)
    assert api_client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    execute_sql(
        api_database_url,
        "UPDATE sessions SET expires_at = now() - interval '1 minute' WHERE user_id = :uid",
        uid=auth_user.id,
    )
    assert api_client.get("/api/auth/me").status_code == 401


def test_sliding_expiry_extends_stale_session(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    # Make the session look 10 days old and last touched 10 days ago.
    execute_sql(
        api_database_url,
        "UPDATE sessions SET expires_at = now() + interval '20 days',"
        " last_seen_at = now() - interval '10 days' WHERE user_id = :uid",
        uid=auth_user.id,
    )
    assert api_client.get("/api/auth/me").status_code == 200
    [(expires_at, last_seen_at)] = fetch_all(
        api_database_url,
        "SELECT expires_at, last_seen_at FROM sessions WHERE user_id = :uid",
        uid=auth_user.id,
    )
    assert isinstance(expires_at, datetime) and isinstance(last_seen_at, datetime)
    now = datetime.now(UTC)
    assert expires_at > now + timedelta(days=29)  # pushed out to ~30 days again
    assert last_seen_at > now - timedelta(minutes=1)


def test_sliding_expiry_write_is_throttled(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    # Fresh session, last_seen_at just set by login: a use within the
    # throttle window must not rewrite the row.
    [(before,)] = fetch_all(
        api_database_url,
        "SELECT expires_at FROM sessions WHERE user_id = :uid",
        uid=auth_user.id,
    )
    assert api_client.get("/api/auth/me").status_code == 200
    [(after,)] = fetch_all(
        api_database_url,
        "SELECT expires_at FROM sessions WHERE user_id = :uid",
        uid=auth_user.id,
    )
    assert after == before


# ------------------------------------------------------------------------ CSRF


def test_cookie_post_without_csrf_header_403(anon_client: TestClient, auth_user: AuthUser) -> None:
    response = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": auth_user.password}
    )
    assert response.status_code == 200
    # Logged in via cookie, but no X-CSRF-Token header set.
    denied = anon_client.post("/api/auth/tokens", json={"name": "x"})
    assert denied.status_code == 403


def test_cookie_post_with_mismatched_csrf_header_403(
    anon_client: TestClient, auth_user: AuthUser
) -> None:
    login(anon_client, auth_user)
    denied = anon_client.post(
        "/api/auth/tokens", json={"name": "x"}, headers={CSRF_HEADER: "wrong-value"}
    )
    assert denied.status_code == 403


def test_cookie_post_with_correct_csrf_header_succeeds(api_client: TestClient) -> None:
    # api_client carries the header by default (see conftest.login).
    assert api_client.post("/api/auth/tokens", json={"name": "ok"}).status_code == 201


def test_cookie_get_needs_no_csrf_header(anon_client: TestClient, auth_user: AuthUser) -> None:
    response = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": auth_user.password}
    )
    assert response.status_code == 200
    assert anon_client.get("/api/auth/me").status_code == 200


def test_bearer_post_is_csrf_exempt(api_client: TestClient, anon_client: TestClient) -> None:
    secret = make_token(api_client)["token"]
    response = anon_client.post(
        "/api/auth/tokens",
        json={"name": "made-via-bearer"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 201


# ------------------------------------------------------------------ API tokens


def test_create_token_returns_secret_once(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    body = make_token(api_client, "ios-shortcut")
    secret = body["token"]
    assert isinstance(secret, str) and secret.startswith("library_")
    assert set(body) == {"id", "name", "token", "created_at"}

    # Only the hash is stored.
    [(stored_hash,)] = fetch_all(
        api_database_url, "SELECT token_hash FROM api_tokens WHERE id = :id", id=body["id"]
    )
    assert stored_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert stored_hash != secret

    # And the list never shows it again.
    listed = api_client.get("/api/auth/tokens").json()
    [item] = [entry for entry in listed if entry["id"] == body["id"]]
    assert set(item) == {"id", "name", "created_at", "last_used_at", "revoked_at"}
    assert secret not in str(listed) and stored_hash not in str(listed)


def test_bearer_token_works_on_documents_api(
    api_client: TestClient, anon_client: TestClient
) -> None:
    secret = make_token(api_client)["token"]
    response = anon_client.get("/api/documents", headers={"Authorization": f"Bearer {secret}"})
    assert response.status_code == 200


def test_revoked_token_401(api_client: TestClient, anon_client: TestClient) -> None:
    body = make_token(api_client)
    deleted = api_client.delete(f"/api/auth/tokens/{body['id']}")
    assert deleted.status_code == 204
    response = anon_client.get(
        "/api/documents", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert response.status_code == 401
    # The revoked token stays listed, with revoked_at set.
    [item] = [t for t in api_client.get("/api/auth/tokens").json() if t["id"] == body["id"]]
    assert item["revoked_at"] is not None


def test_garbage_bearer_token_401(anon_client: TestClient) -> None:
    response = anon_client.get(
        "/api/documents", headers={"Authorization": "Bearer library_not-a-real-token"}
    )
    assert response.status_code == 401


def test_cross_user_token_delete_404(
    api_client: TestClient,
    anon_client: TestClient,
    api_database_url: str,
) -> None:
    token_id = make_token(api_client)["id"]
    other = create_user(api_database_url)
    login(anon_client, other)
    assert anon_client.delete(f"/api/auth/tokens/{token_id}").status_code == 404
    # And the other user's list does not contain it.
    assert all(t["id"] != token_id for t in anon_client.get("/api/auth/tokens").json())


def test_uploads_record_uploader(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    content = f"uploader test {auth_user.username}".encode()
    response = api_client.post(
        "/api/documents", files={"file": ("note.txt", content, "text/plain")}
    )
    assert response.status_code == 201
    [(uploader_id,)] = fetch_all(
        api_database_url,
        "SELECT uploader_id FROM documents WHERE id = :id",
        id=response.json()["id"],
    )
    assert uploader_id == auth_user.id


# ------------------------------------------------------------- protection sweep


def test_every_api_route_requires_auth(api_app: FastAPI, anon_client: TestClient) -> None:
    """Every /api route except POST /api/auth/login must 401 anonymously.

    Iterates the live route table so routes added later are covered
    automatically.
    """
    checked = 0
    for route in api_app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        path = route.path.format(
            **dict.fromkeys(route.param_convertors, "1")  # any path params -> "1"
        )
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if method == "POST" and route.path == "/api/auth/login":
                continue
            response = anon_client.request(method, path)
            assert response.status_code == 401, (
                f"{method} {path} returned {response.status_code}, expected 401"
            )
            checked += 1
    assert checked >= 12  # documents (8) + jobs + logout + me + tokens(3)
