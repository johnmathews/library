"""The DB-backed OAuth provider and consent service, driven directly.

These tests call ``LibraryOAuthProvider`` and ``library.auth.oauth_service`` the
way the MCP SDK's handlers and the consent API will, against the shared test
database. HTTP wiring (discovery, the SDK handlers, PKCE verification) is
W3's ``tests/test_mcp_oauth.py``.

The provider resolves ``library.db.get_sessionmaker()`` per call, exactly as in
production; the ``provider`` fixture points it at the test database and
disposes the engine afterwards (asyncpg connections are loop-bound and each
test runs in a loop of its own).

Fixtures are adversarial on purpose: a second user on the same client, a second
token family that must survive the first one's revocation, expired rows, a
revoked grant, and redirect URIs that differ from an allowed one by one part.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as OAuthTokenResponse
from pydantic import AnyUrl, ValidationError
from sqlalchemy import func, select, text, update

from library.auth.oauth_provider import LibraryOAuthClient, LibraryOAuthProvider
from library.auth.oauth_service import (
    AuthorizationRequestExpired,
    AuthorizationRequestNotFound,
    approve_authorization_request,
    deny_authorization_request,
    get_authorization_request,
    list_grants,
    now,
    revoke_grant,
)
from library.auth.service import sha256_hex
from library.config import Settings, get_settings
from library.db import dispose_engine, get_sessionmaker
from library.models import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)

BASE = "https://library.example.test"
RESOURCE = f"{BASE}/mcp"
CONSENT = f"{BASE}/oauth/consent"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CHATGPT_CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


@pytest.fixture
async def provider(
    api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[LibraryOAuthProvider]:
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    monkeypatch.delenv("LIBRARY_OAUTH_REDIRECT_ALLOWLIST", raising=False)
    get_settings.cache_clear()
    await dispose_engine()
    yield _provider()
    await dispose_engine()
    get_settings.cache_clear()


def _provider(resource_url: str = RESOURCE) -> LibraryOAuthProvider:
    return LibraryOAuthProvider(
        base_url=f"{BASE}/mcp",
        resource_url=resource_url,
        consent_url=CONSENT,
        redirect_allowlist=get_settings().oauth_redirect_allowlist,
    )


# --------------------------------------------------------------------- helpers


async def _user(*, active: bool = True) -> User:
    async with get_sessionmaker()() as session:
        user = User(username=f"oauth-{uuid4().hex[:12]}", password_hash="x", is_active=active)
        session.add(user)
        await session.commit()
        return user


def _client_info(redirect_uris: list[str], name: str | None = None) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=str(uuid4()),
        client_secret=uuid4().hex,
        client_name=name or f"client-{uuid4().hex[:8]}",
        redirect_uris=[AnyUrl(uri) for uri in redirect_uris],
        token_endpoint_auth_method="client_secret_post",
    )


async def _register(
    provider: LibraryOAuthProvider, redirect_uris: list[str] | None = None
) -> OAuthClientInformationFull:
    info = _client_info(redirect_uris or [CLAUDE_CALLBACK])
    await provider.register_client(info)
    assert info.client_id is not None
    client = await provider.get_client(info.client_id)
    assert client is not None
    return client


def _params(
    redirect_uri: str = CLAUDE_CALLBACK,
    *,
    state: str | None = "state-123",
    resource: str | None = RESOURCE,
) -> AuthorizationParams:
    return AuthorizationParams(
        state=state,
        scopes=None,
        code_challenge="challenge-" + uuid4().hex,
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
        resource=resource,
    )


async def _authorize(
    provider: LibraryOAuthProvider,
    client: OAuthClientInformationFull,
    params: AuthorizationParams | None = None,
) -> str:
    """Run /authorize; returns the raw request id from the consent URL."""
    url = await provider.authorize(client, params or _params())
    assert url.startswith(f"{CONSENT}?request=")
    return parse_qs(urlsplit(url).query)["request"][0]


async def _approve(provider: LibraryOAuthProvider, user: User, request_id: str) -> str:
    async with get_sessionmaker()() as session:
        return await approve_authorization_request(
            session, user=user, request_id=request_id, issuer=provider.issuer
        )


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


@dataclass
class Flow:
    client: OAuthClientInformationFull
    user: User
    request_id: str
    code: str
    tokens: OAuthTokenResponse


async def _code(
    provider: LibraryOAuthProvider,
    client: OAuthClientInformationFull,
    user: User,
    params: AuthorizationParams | None = None,
) -> tuple[str, str]:
    request_id = await _authorize(provider, client, params)
    redirect = await _approve(provider, user, request_id)
    return request_id, _query(redirect)["code"][0]


async def _flow(
    provider: LibraryOAuthProvider,
    client: OAuthClientInformationFull | None = None,
    user: User | None = None,
) -> Flow:
    """register → authorize → approve → token, as the SDK handlers would."""
    client = client or await _register(provider)
    user = user or await _user()
    request_id, code = await _code(provider, client, user)
    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    tokens = await provider.exchange_authorization_code(client, loaded)
    return Flow(client, user, request_id, code, tokens)


async def _refresh(
    provider: LibraryOAuthProvider, client: OAuthClientInformationFull, refresh_token: str
) -> OAuthTokenResponse:
    loaded = await provider.load_refresh_token(client, refresh_token)
    assert loaded is not None
    return await provider.exchange_refresh_token(client, loaded, loaded.scopes)


async def _execute(statement: Any) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(statement)
        await session.commit()


async def _count(model: Any) -> int:
    async with get_sessionmaker()() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# -------------------------------------------------------------- registration


@pytest.mark.parametrize(
    "bad_uri",
    [
        "https://evil.example/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/extra",
        "https://claude.ai/api/mcp/auth_callback#fragment",
        "https://claude.ai:8443/api/mcp/auth_callback",
        "https://chatgpt.com.evil.example/connector/oauth/abc",
        "https://chatgpt.com/connector/oauthx",
        "https://chatgpt.com/connector/oauth/../../elsewhere",
        "https://chatgpt.com/connector/oauth/%2e%2e/elsewhere",
        "https://localhost/callback",
        "http://localhost:8080/other",
        "http://evil.example/callback",
    ],
)
async def test_register_rejects_unlisted_redirect(
    provider: LibraryOAuthProvider, bad_uri: str
) -> None:
    # One allowed URI alongside the bad one: the bad one alone must sink it.
    info = _client_info([CLAUDE_CALLBACK, bad_uri])
    with pytest.raises(RegistrationError) as raised:
        await provider.register_client(info)
    assert raised.value.error == "invalid_redirect_uri"
    assert info.client_id is not None
    assert await provider.get_client(info.client_id) is None


async def test_register_accepts_vendor_and_loopback_redirects(
    provider: LibraryOAuthProvider,
) -> None:
    uris = [
        CLAUDE_CALLBACK,
        "https://claude.com/api/mcp/auth_callback",
        CHATGPT_CALLBACK,
        "https://chatgpt.com/connector/oauth/abc123",
        "http://localhost:53682/callback",
        "http://127.0.0.1:41000/callback",
        "http://localhost/callback",
    ]
    info = _client_info(uris, name="Claude")
    await provider.register_client(info)
    assert info.client_id is not None
    client = await provider.get_client(info.client_id)
    assert isinstance(client, LibraryOAuthClient)
    assert [str(uri) for uri in client.redirect_uris or []] == [str(AnyUrl(uri)) for uri in uris]
    assert client.client_secret == info.client_secret
    assert client.client_name == "Claude"
    assert client.token_endpoint_auth_method == "client_secret_post"


async def test_loopback_redirect_any_port(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider, ["http://localhost:53682/callback"])
    other_port = AnyUrl("http://localhost:60001/callback")
    assert client.validate_redirect_uri(other_port) == other_port
    for wrong in (
        "http://localhost:60001/other",
        "http://127.0.0.1:60001/callback",
        "https://localhost:60001/callback",
        "http://localhost:60001/callback?x=1",
    ):
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl(wrong))

    # Port leniency is for loopback only: a vendor callback must match exactly.
    vendor = await _register(provider, [CLAUDE_CALLBACK])
    with pytest.raises(InvalidRedirectUriError):
        vendor.validate_redirect_uri(AnyUrl("https://claude.ai:8443/api/mcp/auth_callback"))

    # The code is bound to the redirect actually used, port included.
    user = await _user()
    _, code = await _code(provider, client, user, _params(str(other_port)))
    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    assert str(loaded.redirect_uri) == str(other_port)


def test_allowlist_setting_parses_env_and_rejects_widening_wildcards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LIBRARY_OAUTH_REDIRECT_ALLOWLIST",
        " https://a.example/cb , https://b.example/prefix/* ,, http://localhost/callback",
    )
    assert Settings(_env_file=None).oauth_redirect_allowlist == [
        "https://a.example/cb",
        "https://b.example/prefix/*",
        "http://localhost/callback",
    ]
    for bad in ("https://chatgpt.com*", "https://a.example/x*/y", "a.example/cb", "ftp://a/x"):
        monkeypatch.setenv("LIBRARY_OAUTH_REDIRECT_ALLOWLIST", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


# ------------------------------------------------------------- authorization


async def test_authorize_rejects_foreign_resource(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    before = await _count(OAuthAuthorizationRequest)
    for foreign in ("https://other.example/mcp", f"{BASE}/api", f"{RESOURCE}/extra"):
        with pytest.raises(AuthorizeError) as raised:
            await provider.authorize(client, _params(resource=foreign))
        assert raised.value.error == "invalid_request"
    assert await _count(OAuthAuthorizationRequest) == before

    # Our own resource (with or without the trailing slash) and none at all pass.
    for accepted in (RESOURCE, f"{RESOURCE}/", None):
        await _authorize(provider, client, _params(resource=accepted))
    assert await _count(OAuthAuthorizationRequest) == before + 3


async def test_code_is_single_use(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    user = await _user()
    _, code = await _code(provider, client, user)
    # Two token requests racing with the same code: both load it first.
    first = await provider.load_authorization_code(client, code)
    second = await provider.load_authorization_code(client, code)
    assert first is not None and second is not None

    tokens = await provider.exchange_authorization_code(client, first)
    assert tokens.access_token
    with pytest.raises(TokenError) as raised:
        await provider.exchange_authorization_code(client, second)
    assert raised.value.error == "invalid_grant"
    assert await provider.load_authorization_code(client, code) is None


async def test_code_bound_to_its_client(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    other = await _register(provider)
    _, code = await _code(provider, client, await _user())
    assert await provider.load_authorization_code(other, code) is None


async def test_expired_code_rejected(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    _, code = await _code(provider, client, await _user())
    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    await _execute(
        update(OAuthAuthorizationCode)
        .where(OAuthAuthorizationCode.code_hash == sha256_hex(code))
        .values(expires_at=now() - timedelta(seconds=1))
    )
    assert await provider.load_authorization_code(client, code) is None
    # Loaded before it expired, exchanged after: still refused.
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, loaded)


async def test_code_for_revoked_grant_rejected(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    user = await _user()
    _, code = await _code(provider, client, user)
    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    await _execute(update(OAuthGrant).where(OAuthGrant.user_id == user.id).values(revoked_at=now()))
    assert await provider.load_authorization_code(client, code) is None
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, loaded)


# ------------------------------------------------------------------- tokens


async def test_exchange_returns_prefixed_tokens_with_expiry(
    provider: LibraryOAuthProvider,
) -> None:
    flow = await _flow(provider)
    assert flow.tokens.access_token.startswith("libmcp_at_")
    assert flow.tokens.refresh_token is not None
    assert flow.tokens.refresh_token.startswith("libmcp_rt_")
    assert flow.tokens.expires_in == 3600
    assert flow.tokens.token_type == "Bearer"


async def test_refresh_rotates_old_refresh_dead(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    assert flow.tokens.refresh_token is not None
    rotated = await _refresh(provider, flow.client, flow.tokens.refresh_token)

    assert rotated.refresh_token not in (None, flow.tokens.refresh_token)
    assert await provider.load_access_token(rotated.access_token) is not None
    assert rotated.refresh_token is not None
    assert await provider.load_refresh_token(flow.client, rotated.refresh_token) is not None
    # The old pair is retired by the rotation.
    assert await provider.load_access_token(flow.tokens.access_token) is None


async def test_refresh_reuse_revokes_family(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    sibling = await _flow(provider, client=flow.client, user=flow.user)  # another family
    assert flow.tokens.refresh_token is not None
    rotated = await _refresh(provider, flow.client, flow.tokens.refresh_token)
    assert rotated.refresh_token is not None

    # The rotated-away refresh token comes back: an attacker or a stale copy.
    assert await provider.load_refresh_token(flow.client, flow.tokens.refresh_token) is None
    assert await provider.load_access_token(rotated.access_token) is None
    assert await provider.load_refresh_token(flow.client, rotated.refresh_token) is None
    # Only that family: the same user's other connection is untouched.
    assert await provider.load_access_token(sibling.tokens.access_token) is not None


async def test_refresh_race_second_exchange_revokes_family(
    provider: LibraryOAuthProvider,
) -> None:
    flow = await _flow(provider)
    assert flow.tokens.refresh_token is not None
    first = await provider.load_refresh_token(flow.client, flow.tokens.refresh_token)
    second = await provider.load_refresh_token(flow.client, flow.tokens.refresh_token)
    assert first is not None and second is not None
    winner = await provider.exchange_refresh_token(flow.client, first, [])
    with pytest.raises(TokenError) as raised:
        await provider.exchange_refresh_token(flow.client, second, [])
    assert raised.value.error == "invalid_grant"
    assert await provider.load_access_token(winner.access_token) is None


async def test_refresh_token_bound_to_its_client(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    other = await _register(provider)
    assert flow.tokens.refresh_token is not None
    assert await provider.load_refresh_token(other, flow.tokens.refresh_token) is None
    # A wrong-client presentation is not treated as a replay.
    assert await provider.load_access_token(flow.tokens.access_token) is not None


async def test_expired_access_token_rejected(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    assert await provider.load_access_token(flow.tokens.access_token) is not None
    await _execute(
        update(OAuthToken)
        .where(OAuthToken.token_hash == sha256_hex(flow.tokens.access_token))
        .values(expires_at=now() - timedelta(seconds=1))
    )
    assert await provider.load_access_token(flow.tokens.access_token) is None


async def test_disabled_user_token_rejected(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    other = await _flow(provider, client=flow.client)  # a second user, same client
    await _execute(update(User).where(User.id == flow.user.id).values(is_active=False))

    assert await provider.load_access_token(flow.tokens.access_token) is None
    assert flow.tokens.refresh_token is not None
    assert await provider.load_refresh_token(flow.client, flow.tokens.refresh_token) is None
    assert await provider.load_access_token(other.tokens.access_token) is not None


def test_resource_url_is_not_derived_from_the_mount_path() -> None:
    """FastMCP would append the mount path to base_url (``…/mcp/mcp``)."""
    provider = LibraryOAuthProvider(
        base_url=f"{BASE}/mcp",
        resource_url=f"{RESOURCE}/",
        consent_url=CONSENT,
        redirect_allowlist=[],
    )
    provider.set_mcp_path("/mcp")
    assert str(provider._get_resource_url("/mcp")) == RESOURCE
    assert provider.resource_url == RESOURCE
    assert provider.issuer == RESOURCE


async def test_access_token_bound_to_resource(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    # public_base_url moved: tokens minted for the old resource stop working.
    moved = _provider(resource_url="https://elsewhere.example.test/mcp")
    assert await moved.load_access_token(flow.tokens.access_token) is None
    assert await provider.load_access_token(flow.tokens.access_token) is not None


async def test_tokens_stored_hashed(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    assert flow.tokens.refresh_token is not None
    # A pending request and an unexchanged code, so every table has a row.
    pending_request = await _authorize(provider, flow.client)
    _, pending_code = await _code(provider, flow.client, flow.user)
    secrets_ = [
        flow.request_id,
        flow.code,
        flow.tokens.access_token,
        flow.tokens.refresh_token,
        pending_request,
        pending_code,
    ]
    async with get_sessionmaker()() as session:
        dumps = [
            str(row)
            for table in (
                "oauth_authorization_requests",
                "oauth_authorization_codes",
                "oauth_tokens",
                "oauth_grants",
                "oauth_clients",
            )
            for row in (await session.execute(text(f"SELECT to_jsonb(t)::text FROM {table} t")))
            .scalars()
            .all()
        ]
        token_hashes = set((await session.execute(select(OAuthToken.token_hash))).scalars())
        request_hashes = set(
            (await session.execute(select(OAuthAuthorizationRequest.id_hash))).scalars()
        )
        code_hashes = set(
            (await session.execute(select(OAuthAuthorizationCode.code_hash))).scalars()
        )
    for secret in secrets_:
        assert not any(secret in dump for dump in dumps), "plaintext secret stored"
    assert {sha256_hex(flow.tokens.access_token), sha256_hex(flow.tokens.refresh_token)} <= (
        token_hashes
    )
    assert sha256_hex(pending_request) in request_hashes
    assert sha256_hex(pending_code) in code_hashes


async def test_access_token_claims_carry_user_id(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    token = await provider.verify_token(flow.tokens.access_token)
    assert token is not None
    assert token.claims == {
        "user_id": flow.user.id,
        "username": flow.user.username,
        "auth": "oauth",
    }
    assert isinstance(token.claims["user_id"], int)
    assert token.client_id == flow.client.client_id
    assert token.resource == RESOURCE
    assert token.subject == str(flow.user.id)
    assert token.expires_at is not None and token.expires_at > now().timestamp()


async def test_non_oauth_tokens_ignored(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    assert flow.tokens.refresh_token is not None
    # A refresh token is not an access token, and other prefixes are not ours.
    assert await provider.load_access_token(flow.tokens.refresh_token) is None
    assert await provider.load_access_token("library_" + "x" * 40) is None
    assert await provider.load_refresh_token(flow.client, flow.tokens.access_token) is None


async def test_access_marks_grant_used(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    await _execute(
        update(OAuthGrant).where(OAuthGrant.user_id == flow.user.id).values(last_used_at=None)
    )
    assert await provider.load_access_token(flow.tokens.access_token) is not None
    async with get_sessionmaker()() as session:
        last_used = (
            await session.execute(
                select(OAuthGrant.last_used_at).where(OAuthGrant.user_id == flow.user.id)
            )
        ).scalar_one()
    assert last_used is not None


async def test_revoke_token_revokes_its_pair(provider: LibraryOAuthProvider) -> None:
    flow = await _flow(provider)
    sibling = await _flow(provider, client=flow.client, user=flow.user)
    access = await provider.load_access_token(flow.tokens.access_token)
    assert access is not None
    await provider.revoke_token(access)
    assert await provider.load_access_token(flow.tokens.access_token) is None
    assert flow.tokens.refresh_token is not None
    assert await provider.load_refresh_token(flow.client, flow.tokens.refresh_token) is None
    assert await provider.load_access_token(sibling.tokens.access_token) is not None


async def test_expired_rows_cleaned_up_opportunistically(provider: LibraryOAuthProvider) -> None:
    stale = await _flow(provider)
    stale_request = await _authorize(provider, stale.client)
    past = now() - timedelta(seconds=1)
    await _execute(update(OAuthToken).values(expires_at=past))
    await _execute(update(OAuthAuthorizationRequest).values(expires_at=past))

    await _flow(provider, client=stale.client)  # authorize + exchange both sweep

    async with get_sessionmaker()() as session:
        live_hashes = set((await session.execute(select(OAuthToken.token_hash))).scalars())
        request_hashes = set(
            (await session.execute(select(OAuthAuthorizationRequest.id_hash))).scalars()
        )
    assert sha256_hex(stale.tokens.access_token) not in live_hashes
    assert sha256_hex(stale_request) not in request_hashes
    assert len(live_hashes) == 2  # only the fresh pair


# ------------------------------------------------------------ consent service


async def test_get_authorization_request_for_consent_page(
    provider: LibraryOAuthProvider,
) -> None:
    client = await _register(provider)
    request_id = await _authorize(provider, client)
    async with get_sessionmaker()() as session:
        info = await get_authorization_request(session, request_id)
        assert await get_authorization_request(session, "not-a-request") is None
    assert info is not None
    assert info.client_id == client.client_id
    assert info.client_name == client.client_name
    assert info.redirect_host == "claude.ai"
    assert info.redirect_uri == CLAUDE_CALLBACK
    assert info.expired is False


async def test_approve_redirect_has_code_state_iss_and_keeps_query(
    provider: LibraryOAuthProvider,
) -> None:
    redirect_uri = "https://chatgpt.com/connector/oauth/cb?tenant=a&blank="
    client = await _register(provider, [redirect_uri])
    user = await _user()
    request_id = await _authorize(provider, client, _params(redirect_uri, state="xyz"))

    redirect = await _approve(provider, user, request_id)

    assert redirect.startswith("https://chatgpt.com/connector/oauth/cb?")
    query = _query(redirect)
    assert query["tenant"] == ["a"]
    assert query["blank"] == [""]
    assert query["state"] == ["xyz"]
    assert query["iss"] == [provider.issuer] == [RESOURCE]
    assert len(query["code"]) == 1
    # The request is consumed: it cannot be approved twice.
    with pytest.raises(AuthorizationRequestNotFound):
        await _approve(provider, user, request_id)
    # And the code is bound to this user.
    loaded = await provider.load_authorization_code(client, query["code"][0])
    assert loaded is not None and loaded.subject == str(user.id)


async def test_approve_without_state_omits_it(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    request_id = await _authorize(provider, client, _params(state=None))
    query = _query(await _approve(provider, await _user(), request_id))
    assert "state" not in query
    assert "code" in query and "iss" in query


async def test_expired_request_cannot_be_approved(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    request_id = await _authorize(provider, client)
    await _execute(
        update(OAuthAuthorizationRequest)
        .where(OAuthAuthorizationRequest.id_hash == sha256_hex(request_id))
        .values(expires_at=now() - timedelta(seconds=1))
    )
    async with get_sessionmaker()() as session:
        info = await get_authorization_request(session, request_id)
    assert info is not None and info.expired is True
    with pytest.raises(AuthorizationRequestExpired):
        await _approve(provider, await _user(), request_id)
    assert await _count(OAuthAuthorizationCode) == 0


async def test_deny_redirects_access_denied(provider: LibraryOAuthProvider) -> None:
    client = await _register(provider)
    request_id = await _authorize(provider, client, _params(state="s1"))
    async with get_sessionmaker()() as session:
        redirect = await deny_authorization_request(
            session, request_id=request_id, issuer=provider.issuer
        )
        with pytest.raises(AuthorizationRequestNotFound):
            await deny_authorization_request(session, request_id=request_id, issuer="x")
    assert redirect.startswith(CLAUDE_CALLBACK + "?")
    query = _query(redirect)
    assert query == {"error": ["access_denied"], "state": ["s1"], "iss": [RESOURCE]}
    assert await _count(OAuthGrant) == 0
    assert await _count(OAuthAuthorizationCode) == 0


async def test_grant_revocation_scoped_to_owner(provider: LibraryOAuthProvider) -> None:
    mine = await _flow(provider)
    theirs = await _flow(provider, client=mine.client)  # second user, same client
    async with get_sessionmaker()() as session:
        [my_grant] = await list_grants(session, mine.user)
        [their_grant] = await list_grants(session, theirs.user)
        assert my_grant.client_name == mine.client.client_name

        # Someone else's grant id: refused, and nothing happens to it.
        assert await revoke_grant(session, mine.user, their_grant.id) is False
    assert await provider.load_access_token(theirs.tokens.access_token) is not None

    async with get_sessionmaker()() as session:
        assert await revoke_grant(session, mine.user, my_grant.id) is True
        assert await revoke_grant(session, mine.user, my_grant.id) is False  # already
        assert await list_grants(session, mine.user) == []
        assert len(await list_grants(session, theirs.user)) == 1
    assert await provider.load_access_token(mine.tokens.access_token) is None
    assert mine.tokens.refresh_token is not None
    assert await provider.load_refresh_token(mine.client, mine.tokens.refresh_token) is None
    assert await provider.load_access_token(theirs.tokens.access_token) is not None


async def test_reapproval_after_revocation_starts_a_fresh_grant(
    provider: LibraryOAuthProvider,
) -> None:
    first = await _flow(provider)
    async with get_sessionmaker()() as session:
        [grant] = await list_grants(session, first.user)
        assert await revoke_grant(session, first.user, grant.id)

    again = await _flow(provider, client=first.client, user=first.user)

    assert await provider.load_access_token(again.tokens.access_token) is not None
    assert await provider.load_access_token(first.tokens.access_token) is None
    async with get_sessionmaker()() as session:
        [fresh] = await list_grants(session, first.user)
    assert fresh.id != grant.id


async def test_reapproval_reuses_a_live_grant(provider: LibraryOAuthProvider) -> None:
    first = await _flow(provider)
    second = await _flow(provider, client=first.client, user=first.user)
    assert await _count(OAuthGrant) == 1
    # Both connections stay live: re-consenting does not log out the first.
    assert await provider.load_access_token(first.tokens.access_token) is not None
    assert await provider.load_access_token(second.tokens.access_token) is not None


async def test_client_reregistration_updates_in_place(provider: LibraryOAuthProvider) -> None:
    info = _client_info([CLAUDE_CALLBACK], name="old")
    await provider.register_client(info)
    await provider.register_client(info.model_copy(update={"client_name": "new"}))
    assert info.client_id is not None
    client = await provider.get_client(info.client_id)
    assert client is not None and client.client_name == "new"
    assert await _count(OAuthClient) == 1
