"""MCP OAuth wiring: discovery, the consent API, and tokens on ``/mcp``.

Every test here builds its own app with ``LIBRARY_PUBLIC_BASE_URL`` set
(``oauth_app``), because the suite's shared ``api_app`` is the OAuth-off app
(see ``_api_app_instance``). The base is ``https://testserver``, not
``http://``: the MCP SDK refuses a plain-HTTP issuer on a non-loopback host,
and the app keeps OAuth off for one (``test_http_public_base_url_keeps_oauth_off``).

Requests go in-process through ``httpx.ASGITransport``. Two clients play the
two parties of an OAuth flow: ``machine`` (the MCP client: no cookies) and
``browser`` (the user: it follows the client's ``/mcp/authorize`` link, which
sets the request's binding cookie, and answers on the consent page with its
session cookie + CSRF header). As in tests/test_mcp.py, the app lifespan and the clients are
entered inside each test body, because anyio cancel scopes must be exited in
the task that entered them.
"""

import base64
import hashlib
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from procrastinate import PsycopgConnector
from pydantic import AnyUrl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.auth.deps import CSRF_COOKIE, CSRF_HEADER
from library.auth.oauth_service import binding_cookie_name
from library.config import get_settings
from library.jobs import job_app, procrastinate_conninfo
from tests.conftest import AuthUser, _fetch_all, build_test_app, create_user
from tests.test_mcp import (
    INITIALIZE_BODY,
    MCP_HEADERS,
    _insert_token,
    _tool_call_body,
)

pytestmark = pytest.mark.integration

BASE: str = "https://testserver"
RESOURCE: str = f"{BASE}/mcp"
ISSUER: str = f"{BASE}/mcp"
PRM_URL: str = f"{BASE}/.well-known/oauth-protected-resource/mcp"
#: Claude Code's shape: a loopback callback, on whatever port it picked.
REDIRECT_URI: str = "http://localhost:53682/callback"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def oauth_app(
    api_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """A fresh app with OAuth on (``public_base_url`` = ``https://testserver``)."""
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "false")
    monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", BASE)
    get_settings.cache_clear()
    yield build_test_app(api_database_url)
    get_settings.cache_clear()


@pytest.fixture
def other_user(api_database_url: str) -> AuthUser:
    return create_user(api_database_url)


@asynccontextmanager
async def running(app: FastAPI, database_url: str) -> AsyncIterator[FastAPI]:
    """``app`` with its lifespan entered and a real Procrastinate connector."""
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(database_url))
    with job_app.replace_connector(connector):
        async with app.router.lifespan_context(app):
            yield app


def asgi_client(app: FastAPI, token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE,
        headers=headers,
        follow_redirects=False,
        timeout=30,
    )


async def log_in(browser: httpx.AsyncClient, user: AuthUser) -> None:
    """Sign the browser in (session cookie) and arm it with the CSRF header."""
    response = await browser.post(
        "/api/auth/login", json={"username": user.username, "password": user.password}
    )
    assert response.status_code == 200, response.text
    browser.headers[CSRF_HEADER] = browser.cookies[CSRF_COOKIE]


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def register(machine: httpx.AsyncClient, **metadata: Any) -> str:
    """Dynamic client registration as a public client, without a scope; the client id."""
    body = {
        "redirect_uris": [REDIRECT_URI],
        "client_name": "Test connector",
        "token_endpoint_auth_method": "none",
        **metadata,
    }
    response = await machine.post("/mcp/register", json=body)
    assert response.status_code == 201, response.text
    client_id: str = response.json()["client_id"]
    return client_id


async def authorize(
    browser: httpx.AsyncClient,
    client_id: str,
    challenge: str,
    *,
    state: str = "client-state",
    scope: str | None = None,
) -> httpx.Response:
    """The browser follows the client's ``/mcp/authorize`` link; the raw 302."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
    }
    if scope is not None:
        params["scope"] = scope
    return await browser.get("/mcp/authorize", params=params)


async def start_authorization(
    browser: httpx.AsyncClient,
    client_id: str,
    challenge: str,
    *,
    state: str = "client-state",
    scope: str | None = None,
) -> str:
    """GET /mcp/authorize in ``browser``; the consent request id it redirects to.

    The redirect also sets the request's binding cookie in ``browser``, so
    only that browser can answer the request.
    """
    response = await authorize(browser, client_id, challenge, state=state, scope=scope)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{BASE}/oauth/consent?"), location
    return query_of(location)["request"]


async def exchange_code(
    machine: httpx.AsyncClient, client_id: str, code: str, verifier: str
) -> httpx.Response:
    return await machine.post(
        "/mcp/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )


@dataclass(frozen=True)
class Connected:
    client_id: str
    access_token: str
    refresh_token: str


async def connect(
    machine: httpx.AsyncClient, browser: httpx.AsyncClient, *, scope: str | None = None
) -> Connected:
    """The whole flow by hand: register → authorize → approve → token.

    ``browser`` must already be signed in as the approving user.
    """
    client_id = await register(machine)
    verifier, challenge = pkce_pair()
    request_id = await start_authorization(browser, client_id, challenge, scope=scope)
    approved = await browser.post(f"/api/oauth/requests/{request_id}/approve")
    assert approved.status_code == 200, approved.text
    code = query_of(approved.json()["redirect_to"])["code"]
    tokens = await exchange_code(machine, client_id, code, verifier)
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    return Connected(client_id, body["access_token"], body["refresh_token"])


async def call_tool(
    client: httpx.AsyncClient, token: str, name: str, arguments: dict[str, Any] | None = None
) -> httpx.Response:
    return await client.post(
        "/mcp",
        json=_tool_call_body(1, name, arguments or {}),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
    )


def tool_ok(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    structured: dict[str, Any] = result["structuredContent"]
    return structured


# -------------------------------------------------------------------- discovery


async def test_401_carries_resource_metadata(oauth_app: FastAPI, api_database_url: str) -> None:
    """A token-less request points the client at the RFC 9728 document for
    exactly ``…/mcp`` — both spellings of the endpoint."""
    async with running(oauth_app, api_database_url), asgi_client(oauth_app) as machine:
        slashless = await machine.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
        slashed = await machine.post("/mcp/", json=INITIALIZE_BODY, headers=MCP_HEADERS)
    for response in (slashless, slashed):
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == f'Bearer resource_metadata="{PRM_URL}"'


async def test_protected_resource_metadata_json(oauth_app: FastAPI, api_database_url: str) -> None:
    async with running(oauth_app, api_database_url), asgi_client(oauth_app) as machine:
        path_aware = await machine.get(PRM_URL)
        root = await machine.get("/.well-known/oauth-protected-resource")
    for response in (path_aware, root):
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {
            "resource": RESOURCE,
            "authorization_servers": [ISSUER],
            "scopes_supported": ["library"],
            "bearer_methods_supported": ["header"],
            "resource_name": "Library",
        }


AS_METADATA_PATHS: tuple[str, ...] = (
    "/.well-known/oauth-authorization-server/mcp",  # RFC 8414 path-aware
    "/.well-known/oauth-authorization-server",  # legacy root
    "/.well-known/openid-configuration/mcp",  # RFC 8414 §5
    "/.well-known/openid-configuration",
    "/mcp/.well-known/openid-configuration",  # OIDC: appended to the issuer
    "/mcp/.well-known/oauth-authorization-server",
)


async def test_as_metadata_path_aware_and_root(oauth_app: FastAPI, api_database_url: str) -> None:
    """One document at every discovery URL a client may derive from the
    ``/mcp`` issuer, advertising endpoints that are all under ``/mcp/``."""
    async with running(oauth_app, api_database_url), asgi_client(oauth_app) as machine:
        responses = {path: await machine.get(path) for path in AS_METADATA_PATHS}
    documents = []
    for path, response in responses.items():
        assert response.status_code == 200, (path, response.text)
        documents.append(response.json())
    assert all(document == documents[0] for document in documents)
    metadata = documents[0]
    assert metadata["issuer"] == ISSUER
    assert metadata["authorization_endpoint"] == f"{BASE}/mcp/authorize"
    assert metadata["token_endpoint"] == f"{BASE}/mcp/token"
    assert metadata["registration_endpoint"] == f"{BASE}/mcp/register"
    assert metadata["revocation_endpoint"] == f"{BASE}/mcp/revoke"
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert metadata["scopes_supported"] == ["library"]
    assert set(metadata["grant_types_supported"]) == {"authorization_code", "refresh_token"}


# ---------------------------------------------------------------- full flows


class InMemoryTokenStorage:
    """The SDK client's ``TokenStorage``, kept in memory for one test."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


async def test_full_flow_with_sdk_oauth_client(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """The official MCP client, given only ``https://testserver/mcp``, discovers
    the server, registers, sends the user to consent, exchanges the code, and
    then calls tools as the user who approved."""
    content = b"%PDF-1.4\n% mcp oauth flow " + secrets.token_hex(8).encode() + b"\n%%EOF\n"
    seen: dict[str, Any] = {}

    async with running(oauth_app, api_database_url), asgi_client(oauth_app) as browser:
        await log_in(browser, auth_user)

        async def redirect_handler(authorization_url: str) -> None:
            seen["authorization_url"] = authorization_url
            authorize = await browser.get(authorization_url)
            assert authorize.status_code == 302, authorize.text
            request_id = query_of(authorize.headers["location"])["request"]
            info = await browser.get(f"/api/oauth/requests/{request_id}")
            assert info.status_code == 200, info.text
            seen["info"] = info.json()
            approved = await browser.post(f"/api/oauth/requests/{request_id}/approve")
            assert approved.status_code == 200, approved.text
            seen["redirect"] = query_of(approved.json()["redirect_to"])

        async def callback_handler() -> tuple[str, str | None]:
            return seen["redirect"]["code"], seen["redirect"].get("state")

        storage = InMemoryTokenStorage()
        auth = OAuthClientProvider(
            server_url=RESOURCE,
            client_metadata=OAuthClientMetadata(
                client_name="SDK test client", redirect_uris=[AnyUrl(REDIRECT_URI)]
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=oauth_app), auth=auth, timeout=30
            ) as http_client,
            streamable_http_client(RESOURCE, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            stats = await session.call_tool("library_stats", {})
            ingested = await session.call_tool(
                "ingest_document",
                {
                    "filename": "oauth.pdf",
                    "content_base64": base64.b64encode(content).decode(),
                },
            )

    assert urlsplit(seen["authorization_url"]).path == "/mcp/authorize"
    assert query_of(seen["authorization_url"])["resource"] == RESOURCE
    assert seen["info"]["client_name"] == "SDK test client"
    assert seen["info"]["redirect_host"] == "localhost"
    assert seen["info"]["scopes"] == ["library"]
    assert seen["info"]["expired"] is False
    assert seen["redirect"]["iss"] == ISSUER
    assert storage.tokens is not None
    assert storage.tokens.access_token.startswith("libmcp_at_")

    assert not stats.isError, stats.content
    assert not ingested.isError, ingested.content
    assert ingested.structuredContent is not None
    rows = await _fetch_all(
        api_database_url,
        "SELECT uploader_id FROM documents WHERE id = :id",
        id=ingested.structuredContent["id"],
    )
    assert rows == [(auth_user.id,)]


async def test_client_without_scope_connects(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """Registering and authorizing with no scope at all still yields a working
    token — and an /authorize asking for ``library`` after a scope-less
    registration is not refused as unregistered."""
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        no_scope = await connect(machine, browser)
        with_scope = await connect(machine, browser, scope="library")
        for connected in (no_scope, with_scope):
            tool_ok(await call_tool(machine, connected.access_token, "library_stats"))


async def test_static_token_still_works_with_oauth_enabled(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    raw, _ = await _insert_token(api_database_url, auth_user.id, "static-with-oauth")
    async with running(oauth_app, api_database_url), asgi_client(oauth_app) as machine:
        stats = tool_ok(await call_tool(machine, raw, "library_stats"))
        garbage = await call_tool(machine, "library_not-a-real-token", "library_stats")
    assert "total_documents" in stats
    assert garbage.status_code == 401


async def test_static_token_claims_name_their_kind(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """Static tokens say ``auth: api_token`` (OAuth ones say ``oauth``) and
    hold the one scope, which no route requires."""
    from library.mcp_server import LibraryTokenVerifier

    raw, _ = await _insert_token(api_database_url, auth_user.id, "claims")
    async with running(oauth_app, api_database_url):
        token = await LibraryTokenVerifier().verify_token(raw)
    assert token is not None
    assert token.claims == {
        "user_id": auth_user.id,
        "username": auth_user.username,
        "auth": "api_token",
    }
    assert token.scopes == ["library"]


# --------------------------------------------------------------- consent API


async def test_approve_requires_cookie_session(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """A valid ``library_…`` token cannot approve (or deny) a request: 403,
    and the request is still pending for the real browser session."""
    raw, _ = await _insert_token(api_database_url, auth_user.id, "script")
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app, token=raw) as script,
        asgi_client(oauth_app) as browser,
    ):
        client_id = await register(machine)
        _, challenge = pkce_pair()
        request_id = await start_authorization(browser, client_id, challenge)
        # The script even holds the request's binding cookie: the token alone decides.
        script.cookies.update(browser.cookies)

        by_token = await script.post(f"/api/oauth/requests/{request_id}/approve")
        denied_by_token = await script.post(f"/api/oauth/requests/{request_id}/deny")
        # The token can still read the request (a GET changes nothing).
        readable = await script.get(f"/api/oauth/requests/{request_id}")

        await log_in(browser, auth_user)
        browser.headers.pop(CSRF_HEADER)
        without_csrf = await browser.post(f"/api/oauth/requests/{request_id}/approve")
        browser.headers[CSRF_HEADER] = browser.cookies[CSRF_COOKIE]
        by_browser = await browser.post(f"/api/oauth/requests/{request_id}/approve")

    assert by_token.status_code == 403, by_token.text
    assert "browser session" in by_token.json()["detail"]
    assert denied_by_token.status_code == 403
    assert readable.status_code == 200
    assert without_csrf.status_code == 403
    assert by_browser.status_code == 200, by_browser.text


async def test_deny_redirects_access_denied(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        client_id = await register(machine)
        _, challenge = pkce_pair()
        request_id = await start_authorization(browser, client_id, challenge, state="s-123")
        denied = await browser.post(f"/api/oauth/requests/{request_id}/deny")
        again = await browser.post(f"/api/oauth/requests/{request_id}/deny")
        approve_after = await browser.post(f"/api/oauth/requests/{request_id}/approve")
        lookup = await browser.get(f"/api/oauth/requests/{request_id}")

    assert denied.status_code == 200, denied.text
    redirect_to = denied.json()["redirect_to"]
    assert redirect_to.startswith(f"{REDIRECT_URI}?")
    assert query_of(redirect_to) == {"error": "access_denied", "state": "s-123", "iss": ISSUER}
    # Consumed: nothing more can be done with it.
    assert again.status_code == 404
    assert approve_after.status_code == 404
    assert lookup.status_code == 404


async def test_redirect_contains_iss(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """The approve redirect carries ``iss`` equal to the advertised issuer
    (RFC 9207), next to the code and the client's own state."""
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        metadata = (await machine.get("/.well-known/oauth-authorization-server/mcp")).json()
        client_id = await register(machine)
        _, challenge = pkce_pair()
        request_id = await start_authorization(browser, client_id, challenge, state="s-456")
        approved = await browser.post(f"/api/oauth/requests/{request_id}/approve")

    assert approved.status_code == 200, approved.text
    redirect_to = approved.json()["redirect_to"]
    assert redirect_to.startswith(f"{REDIRECT_URI}?")
    params = query_of(redirect_to)
    assert set(params) == {"code", "state", "iss"}
    assert params["state"] == "s-456"
    assert params["iss"] == metadata["issuer"] == ISSUER


async def test_authorize_sets_a_per_request_binding_cookie(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """``/mcp/authorize`` binds the parked request to the browser that followed
    it: an HttpOnly, SameSite=Lax, Secure (https issuer), ``Path=/`` cookie
    named for that request and living as long as it. Two flows in one browser
    each get their own and both work; answering one clears its cookie."""
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        client_id = await register(machine)
        first = await authorize(browser, client_id, pkce_pair()[1])
        second_id = await start_authorization(browser, client_id, pkce_pair()[1])
        first_id = query_of(first.headers["location"])["request"]
        approved = await browser.post(f"/api/oauth/requests/{first_id}/approve")
        denied = await browser.post(f"/api/oauth/requests/{second_id}/deny")

    assert first.status_code == 302, first.text
    [set_cookie] = first.headers.get_list("set-cookie")
    name, _, rest = set_cookie.partition("=")
    assert name == binding_cookie_name(first_id) != binding_cookie_name(second_id)
    attributes = {part.strip().lower() for part in rest.split(";")[1:]}
    assert {"httponly", "secure", "samesite=lax", "path=/", "max-age=600"} <= attributes
    assert approved.status_code == 200, approved.text
    assert denied.status_code == 200, denied.text
    for response, request_id in ((approved, first_id), (denied, second_id)):
        [cleared] = response.headers.get_list("set-cookie")
        assert cleared.startswith(f"{binding_cookie_name(request_id)}=")
        assert "max-age=0" in cleared.lower()
    assert not [name for name in browser.cookies if name.startswith("library_oauth_request_")]


async def test_forwarded_consent_link_is_refused(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser, other_user: AuthUser
) -> None:
    """Consent phishing: someone starts connecting their own app and sends the
    consent link to a family member. The member is signed in, but their
    browser never followed ``/mcp/authorize``, so reading, approving and
    denying all answer 404 telling them to start from their app — and the
    request is still there for the browser that started it."""
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as attacker,
        asgi_client(oauth_app) as victim,
    ):
        await log_in(victim, auth_user)
        client_id = await register(machine)
        request_id = await start_authorization(attacker, client_id, pkce_pair()[1])

        read = await victim.get(f"/api/oauth/requests/{request_id}")
        approved = await victim.post(f"/api/oauth/requests/{request_id}/approve")
        denied = await victim.post(f"/api/oauth/requests/{request_id}/deny")
        victim_grants = (await victim.get("/api/oauth/grants")).json()

        await log_in(attacker, other_user)
        own = await attacker.post(f"/api/oauth/requests/{request_id}/approve")

    for refused in (read, approved, denied):
        assert refused.status_code == 404, refused.text
        assert "start connecting again" in refused.json()["detail"]
    assert victim_grants == []
    assert own.status_code == 200, own.text


async def test_expired_request_reads_expired_and_approve_is_410(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        client_id = await register(machine)
        _, challenge = pkce_pair()
        request_id = await start_authorization(browser, client_id, challenge)
        await _expire_requests(api_database_url, client_id)
        info = await browser.get(f"/api/oauth/requests/{request_id}")
        approved = await browser.post(f"/api/oauth/requests/{request_id}/approve")

    assert info.status_code == 200, info.text
    assert info.json()["expired"] is True
    assert approved.status_code == 410, approved.text
    assert "expired" in approved.json()["detail"]


async def _expire_requests(database_url: str, client_id: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(
                text(
                    "UPDATE oauth_authorization_requests SET expires_at = :past "
                    "WHERE client_id = :client_id"
                ),
                {"past": datetime.now(UTC) - timedelta(minutes=1), "client_id": client_id},
            )
            await session.commit()
    finally:
        await engine.dispose()


# ------------------------------------------------------------- connected apps


async def test_grant_delete_kills_tokens(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """Disconnecting an app in Settings kills its access and refresh tokens."""
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as browser,
    ):
        await log_in(browser, auth_user)
        connected = await connect(machine, browser)
        before = tool_ok(await call_tool(machine, connected.access_token, "library_stats"))

        grants = (await browser.get("/api/oauth/grants")).json()
        deleted = await browser.delete(f"/api/oauth/grants/{grants[0]['id']}")
        after = await call_tool(machine, connected.access_token, "library_stats")
        refreshed = await machine.post(
            "/mcp/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": connected.refresh_token,
                "client_id": connected.client_id,
            },
        )
        grants_after = (await browser.get("/api/oauth/grants")).json()

    assert "total_documents" in before
    assert len(grants) == 1
    assert grants[0]["client_id"] == connected.client_id
    assert grants[0]["client_name"] == "Test connector"
    assert grants[0]["last_used_at"] is not None
    assert deleted.status_code == 204, deleted.text
    assert after.status_code == 401
    assert refreshed.status_code == 401, refreshed.text
    assert refreshed.json()["error"] == "invalid_grant"
    assert grants_after == []


async def test_other_users_grant_not_deletable(
    oauth_app: FastAPI, api_database_url: str, auth_user: AuthUser, other_user: AuthUser
) -> None:
    async with (
        running(oauth_app, api_database_url),
        asgi_client(oauth_app) as machine,
        asgi_client(oauth_app) as owner,
        asgi_client(oauth_app) as stranger,
    ):
        await log_in(owner, auth_user)
        await log_in(stranger, other_user)
        connected = await connect(machine, owner)
        grant_id = (await owner.get("/api/oauth/grants")).json()[0]["id"]

        stranger_list = await stranger.get("/api/oauth/grants")
        stranger_delete = await stranger.delete(f"/api/oauth/grants/{grant_id}")
        unknown_delete = await owner.delete(f"/api/oauth/grants/{grant_id + 1_000_000}")
        still_works = await call_tool(machine, connected.access_token, "library_stats")
        owner_list = await owner.get("/api/oauth/grants")

    assert stranger_list.json() == []
    assert stranger_delete.status_code == 404
    assert unknown_delete.status_code == 404
    assert stranger_delete.json() == unknown_delete.json()  # says nothing about whose
    tool_ok(still_works)
    assert [grant["id"] for grant in owner_list.json()] == [grant_id]


# --------------------------------------------------------------- OAuth off


async def test_oauth_off_routes_404_and_no_resource_metadata(
    api_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> None:
    """Without ``public_base_url`` the server behaves as before OAuth: a bare
    ``Bearer`` challenge, no discovery documents, no OAuth endpoints, and the
    consent API answers 404 even to a signed-in user (and to a bearer token,
    which would otherwise get approve's 403)."""
    raw, _ = await _insert_token(api_database_url, auth_user.id, "oauth-off")
    async with (
        running(api_app, api_database_url),
        asgi_client(api_app) as machine,
        asgi_client(api_app) as browser,
        asgi_client(api_app, token=raw) as script,
    ):
        script_approve = await script.post("/api/oauth/requests/abc/approve")
        challenge = await machine.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
        discovery = {
            path: (await machine.get(path)).status_code
            for path in (
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource",
                *AS_METADATA_PATHS,
            )
        }
        registered = await machine.post("/mcp/register", json={"redirect_uris": [REDIRECT_URI]})
        authorize = await machine.get("/mcp/authorize", params={"client_id": "x"})
        await log_in(browser, auth_user)
        consent_api = {
            "GET requests": (await browser.get("/api/oauth/requests/abc")).status_code,
            "POST approve": (await browser.post("/api/oauth/requests/abc/approve")).status_code,
            "POST deny": (await browser.post("/api/oauth/requests/abc/deny")).status_code,
            "GET grants": (await browser.get("/api/oauth/grants")).status_code,
            "DELETE grant": (await browser.delete("/api/oauth/grants/1")).status_code,
        }

    assert challenge.status_code == 401
    assert challenge.headers["WWW-Authenticate"] == "Bearer"
    assert set(discovery.values()) == {404}, discovery
    assert registered.status_code in {404, 405}
    assert authorize.status_code in {404, 405}
    assert consent_api == dict.fromkeys(consent_api, 404)
    assert script_approve.status_code == 404


def test_http_public_base_url_keeps_oauth_off(
    api_database_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``public_base_url`` predates OAuth; a plain-HTTP LAN value must not stop
    the app from booting (the SDK refuses a non-HTTPS issuer) — OAuth just
    stays off, loudly. HTTPS and loopback HTTP turn it on."""
    from library.config import Settings
    from library.mcp_server import oauth_base_url

    def base(value: str) -> str | None:
        return oauth_base_url(Settings(public_base_url=value))

    assert base("https://library.example.org/") == "https://library.example.org"
    assert base("http://localhost:8000") == "http://localhost:8000"
    assert base("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    with caplog.at_level("WARNING", logger="library.mcp_server"):
        assert base("http://192.0.2.10:8010") is None
    assert "MCP OAuth stays off" in caplog.text

    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    monkeypatch.setenv("LIBRARY_PUBLIC_BASE_URL", "http://192.0.2.10:8010")
    get_settings.cache_clear()
    try:
        app = build_test_app(api_database_url)
    finally:
        get_settings.cache_clear()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert not any(str(path).startswith("/.well-known/") for path in paths)
