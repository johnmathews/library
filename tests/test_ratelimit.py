"""Rate limiting: login failures, /mcp/token, /mcp/register (library.auth.ratelimit)."""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector

from library.auth.ratelimit import SlidingWindowLimiter, limiter
from library.config import get_settings
from library.jobs import job_app, procrastinate_conninfo
from tests.conftest import AuthUser, build_test_app, create_user

BASE = "https://testserver"


def _fail(client: TestClient, username: str, **headers: str) -> int:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "nope"}, headers=headers
    )
    return response.status_code


# ------------------------------------------------------------------ login


def test_login_throttled_after_failures(anon_client: TestClient, auth_user: AuthUser) -> None:
    assert [_fail(anon_client, auth_user.username) for _ in range(5)] == [401] * 5
    response = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": auth_user.password}
    )
    # Even the right password is refused once the username is locked.
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_login_throttled_per_client_key(anon_client: TestClient, auth_user: AuthUser) -> None:
    # Twenty different usernames: none trips the per-username limit.
    assert {_fail(anon_client, f"nobody-{i}") for i in range(20)} == {401}
    response = anon_client.post(
        "/api/auth/login", json={"username": auth_user.username, "password": auth_user.password}
    )
    assert response.status_code == 429


def test_successful_login_not_counted(anon_client: TestClient, auth_user: AuthUser) -> None:
    for _ in range(30):
        ok = anon_client.post(
            "/api/auth/login",
            json={"username": auth_user.username, "password": auth_user.password},
        )
        assert ok.status_code == 200
    # Nothing was counted: all five failures are still available...
    assert [_fail(anon_client, auth_user.username) for _ in range(5)] == [401] * 5
    # ...and the successes did not reset the counter either.
    assert _fail(anon_client, auth_user.username) == 429


def test_throttled_login_same_response_for_unknown_user(
    anon_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    disabled = create_user(api_database_url, is_active=False)
    names = [auth_user.username, "no-such-user", disabled.username]
    for name in names:
        for _ in range(5):
            _fail(anon_client, name)
    responses = [
        anon_client.post("/api/auth/login", json={"username": name, "password": "nope"})
        for name in names
    ]
    assert {r.status_code for r in responses} == {429}
    assert len({r.text for r in responses}) == 1
    assert len({r.headers["retry-after"] for r in responses}) == 1


def test_trusted_header_used_only_when_configured(
    anon_client: TestClient, auth_user: AuthUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhaust_from_first_address() -> None:
        for i in range(20):
            _fail(anon_client, f"nobody-{i}", **{"CF-Connecting-IP": "203.0.113.1"})

    def login_from(address: str) -> int:
        return anon_client.post(
            "/api/auth/login",
            json={"username": auth_user.username, "password": auth_user.password},
            headers={"CF-Connecting-IP": address},
        ).status_code

    # Not configured: the header is spoofable noise, everyone is the socket peer.
    exhaust_from_first_address()
    assert login_from("203.0.113.2") == 429

    limiter.reset()
    monkeypatch.setenv("LIBRARY_TRUSTED_CLIENT_IP_HEADER", "CF-Connecting-IP")
    get_settings.cache_clear()
    exhaust_from_first_address()
    assert login_from("203.0.113.1") == 429
    assert login_from("203.0.113.2") == 200


def test_window_slides() -> None:
    now = [0.0]
    sliding = SlidingWindowLimiter(clock=lambda: now[0])
    for t in (0.0, 10.0, 20.0):
        now[0] = t
        assert sliding.hit("b", "k", 3, 60.0) == 0
    now[0] = 30.0
    # Full; the oldest hit (t=0) leaves the window at t=60.
    assert sliding.hit("b", "k", 3, 60.0) == 30
    now[0] = 59.0
    assert sliding.hit("b", "k", 3, 60.0) == 1
    now[0] = 60.0  # t=0 slid out; one slot is free, the refusals were not counted
    assert sliding.hit("b", "k", 3, 60.0) == 0
    assert sliding.hit("b", "k", 3, 60.0) > 0
    now[0] = 200.0  # everything expired
    assert sliding.hit("b", "k", 3, 60.0) == 0


def test_limiter_memory_is_bounded() -> None:
    small = SlidingWindowLimiter(clock=lambda: 0.0, max_keys=100)
    for i in range(1_000):
        small.record("b", f"user-{i}", 60.0)
    assert len(small._hits["b"]) == 100


# ------------------------------------------------------------ OAuth endpoints


@pytest.fixture
def oauth_app(
    api_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "false")
    monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", BASE)
    get_settings.cache_clear()
    yield build_test_app(api_database_url)
    get_settings.cache_clear()


@asynccontextmanager
async def _machine(app: FastAPI, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(database_url))
    with job_app.replace_connector(connector):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30
            ) as client:
                yield client


async def test_concurrent_login_burst_cannot_exceed_the_limit(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """A burst sent all at once gets no more password checks than the limit.

    Checking the counter and recording the attempt are one step, before the
    password hash is verified; if the failure were only recorded afterwards,
    every request already in flight would see the old count and get through.
    """
    async with _machine(oauth_app, api_database_url) as machine:

        async def attempt(index: int) -> int:
            response = await machine.post(
                "/api/auth/login",
                json={"username": auth_user.username, "password": f"wrong-{index}"},
            )
            return response.status_code

        codes = await asyncio.gather(*(attempt(index) for index in range(30)))
    assert Counter(codes) == {401: 5, 429: 25}


async def test_concurrent_successes_do_not_consume_failures(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """Successful logins leave no trace once they finish.

    While in flight an attempt holds a slot (it is counted before the
    password check), so up to the limit may run at once; afterwards every
    success has been uncounted and all five failures are still available.
    """
    async with _machine(oauth_app, api_database_url) as machine:
        right = {"username": auth_user.username, "password": auth_user.password}
        responses = await asyncio.gather(
            *(machine.post("/api/auth/login", json=right) for _ in range(5))
        )
        assert {response.status_code for response in responses} == {200}
        wrong = {"username": auth_user.username, "password": "nope"}
        codes = [(await machine.post("/api/auth/login", json=wrong)).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


async def test_token_endpoint_throttled(oauth_app: FastAPI, api_database_url: str) -> None:
    async with _machine(oauth_app, api_database_url) as machine:
        body = {"grant_type": "refresh_token", "refresh_token": "x", "client_id": "nope"}
        for _ in range(60):
            response = await machine.post("/mcp/token", data=body)
            assert response.status_code != 429
        response = await machine.post("/mcp/token", data=body)
        assert response.status_code == 429
        assert response.json()["error"] == "slow_down"
        assert int(response.headers["retry-after"]) > 0
        # /mcp (slashless) and other paths are not caught by the token limit.
        assert (await machine.get("/healthz")).status_code != 429


async def test_register_endpoint_throttled(oauth_app: FastAPI, api_database_url: str) -> None:
    async with _machine(oauth_app, api_database_url) as machine:
        body = {
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "token_endpoint_auth_method": "none",
        }
        for _ in range(10):
            response = await machine.post("/mcp/register", json=body)
            assert response.status_code == 201, response.text
        response = await machine.post("/mcp/register", json=body)
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        # The token endpoint has its own bucket.
        token = await machine.post("/mcp/token", data={"grant_type": "refresh_token"})
        assert token.status_code != 429
