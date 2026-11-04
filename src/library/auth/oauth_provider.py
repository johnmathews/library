"""Library as an OAuth 2.1 authorization server for MCP clients.

``LibraryOAuthProvider`` implements FastMCP's ``OAuthProvider`` (the MCP SDK's
``OAuthAuthorizationServerProvider``) over Postgres, so claude.ai, ChatGPT and
Claude Code can connect to ``/mcp`` as a specific Library user without a
static token. The SDK owns the HTTP endpoints (``/register``, ``/authorize``,
``/token``, ``/revoke``) and PKCE verification; this class owns storage and
policy:

- **Registration** (RFC 7591) is open — that is how these clients work — but
  every redirect URI must be on an allow-list (``Settings.
  oauth_redirect_allowlist``), so a registered client can only ever send a code
  to a known vendor callback or to the user's own machine.
- **Authorization** never issues a code itself: it parks the request and sends
  the browser to the consent page (``consent_url``), where a signed-in user
  approves or denies (``library.auth.oauth_service``). The parked request is
  bound to the browser that followed ``/authorize``: this class replaces the
  SDK's ``/authorize`` route with one that sets a per-request binding cookie
  on the SDK's 302, and only a request carrying it can answer (see
  ``get_routes``).
- **Tokens** are opaque and stored hashed: access ``libmcp_at_…`` (1 h),
  refresh ``libmcp_rt_…`` (30 d, rotated on every use; replaying a rotated one
  revokes its whole family). An access token is bound to ``resource_url`` and
  carries the user's id in its claims, which is what ``mcp_server`` reads for
  attribution.

Database access goes through the shared session factory
(``library.db.get_sessionmaker``), resolved per call so a disposed engine is
rebuilt in the current event loop.
"""

import logging
from collections.abc import Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from fastmcp.server.auth.auth import (
    AccessToken,
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import AUTHORIZATION_PATH
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as OAuthTokenResponse
from pydantic import AnyHttpUrl, AnyUrl, PrivateAttr
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from library.auth.oauth_service import (
    ACCESS_TOKEN_PREFIX,
    ACCESS_TOKEN_TTL,
    AUTHORIZATION_REQUEST_TTL,
    REFRESH_TOKEN_PREFIX,
    REFRESH_TOKEN_TTL,
    binding_cookie_name,
    delete_expired,
    new_secret,
    now,
    revoke_family,
)
from library.auth.service import TOUCH_INTERVAL, sha256_hex
from library.db import get_sessionmaker
from library.models import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)

logger = logging.getLogger(__name__)

#: Hosts RFC 8252 §7.3 treats as the user's own machine, where a native app
#: (Claude Code) listens on a port it picks per login.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "[::1]"})


# ------------------------------------------------------------ redirect policy


def _is_loopback_port_variant(registered: AnyUrl, requested: AnyUrl) -> bool:
    """Same loopback redirect apart from the port (RFC 8252 §7.3)."""
    return (
        registered.scheme == requested.scheme == "http"
        and registered.host in LOOPBACK_HOSTS
        and registered.host == requested.host
        and registered.path == requested.path
        and registered.query == requested.query
        and requested.fragment is None
    )


def redirect_uri_allowed(uri: AnyUrl, allowlist: Sequence[str]) -> bool:
    """Whether ``uri`` may be registered, given the configured allow-list.

    Entry forms: an exact URL; a prefix ending ``/*``; a loopback URL with no
    port, which matches that loopback URL on any port. A URI with a fragment
    is never allowed (RFC 6749 §3.1.2).
    """
    if uri.fragment is not None:
        return False
    text = str(uri)
    for entry in allowlist:
        if entry.endswith("/*"):
            if text.startswith(str(AnyUrl(entry[:-1]))):
                return True
            continue
        allowed = AnyUrl(entry)
        if str(allowed) == text:
            return True
        if urlsplit(entry).port is None and _is_loopback_port_variant(allowed, uri):
            return True
    return False


class LibraryOAuthClient(OAuthClientInformationFull):
    """A registered client whose loopback redirects match on any port, and
    whose redirects must still be on the server's *current* allow-list.

    Claude Code registers ``http://localhost:<port>/callback`` once and then
    listens on a fresh port per login, so an exact match would fail the next
    sign-in. Only the port may differ; everything else is the SDK's exact check.

    The allow-list is checked again here, not only at registration, so
    narrowing ``LIBRARY_OAUTH_REDIRECT_ALLOWLIST`` also cuts off clients that
    registered under the broader list. The SDK's ``/authorize`` calls this
    before anything else and answers a refusal with a direct 400, never a
    redirect to the refused URI. ``get_client`` sets the list; ``None`` (a
    client built elsewhere) skips the check.
    """

    _redirect_allowlist: tuple[str, ...] | None = PrivateAttr(default=None)

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and any(
            _is_loopback_port_variant(registered, redirect_uri)
            for registered in self.redirect_uris or []
        ):
            resolved = redirect_uri
        else:
            resolved = super().validate_redirect_uri(redirect_uri)
        allowlist = self._redirect_allowlist
        if allowlist is not None and not redirect_uri_allowed(resolved, allowlist):
            raise InvalidRedirectUriError(
                f"Redirect URI '{resolved}' is no longer allowed by this server"
            )
        return resolved


# ------------------------------------------------------------ SDK model types


class LibraryAuthorizationCode(AuthorizationCode):
    """The SDK's code model plus the grant it was minted under."""

    grant_id: int


class LibraryRefreshToken(RefreshToken):
    """The SDK's refresh-token model plus its grant and rotation family."""

    grant_id: int
    family_id: str


# ------------------------------------------------------------------ provider


def _timestamp(moment: datetime) -> int:
    return int(moment.timestamp())


class IssuedAuthorization(NamedTuple):
    """A request ``authorize`` parked: where to send the browser, the request
    id in that URL, and the binding secret the browser gets as a cookie."""

    consent_url: str
    request_id: str
    binding: str


#: Set by the wrapped ``/authorize`` route for the duration of one call; the
#: provider appends what it parked so the route can set the binding cookie on
#: the 302 the SDK builds from ``authorize``'s return value.
_issued_authorizations: ContextVar[list[IssuedAuthorization] | None] = ContextVar(
    "library_oauth_issued_authorizations", default=None
)


class LibraryOAuthProvider(OAuthProvider):
    """DB-backed OAuth authorization server for the MCP endpoint.

    Args:
        base_url: Where the SDK's OAuth endpoints are mounted, and the issuer
            (``{public_base_url}/mcp``).
        resource_url: The protected resource tokens are bound to — exactly
            ``{public_base_url}/mcp``, no trailing slash.
        consent_url: The SPA consent page (``{public_base_url}/oauth/consent``).
        redirect_allowlist: Redirect URIs a client may register (see
            ``redirect_uri_allowed``).
    """

    def __init__(
        self,
        *,
        base_url: str,
        resource_url: str,
        consent_url: str,
        redirect_allowlist: Sequence[str],
        client_registration_options: ClientRegistrationOptions | None = None,
        revocation_options: RevocationOptions | None = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            client_registration_options=client_registration_options,
            revocation_options=revocation_options,
            required_scopes=required_scopes,
        )
        self.resource_url: str = resource_url.rstrip("/")
        self.consent_url: str = consent_url
        self.redirect_allowlist: tuple[str, ...] = tuple(redirect_allowlist)

    @property
    def issuer(self) -> str:
        """The issuer identifier, for RFC 9207 ``iss`` on the approve redirect."""
        return str(self.issuer_url)

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        """Always ``resource_url``: FastMCP would append the mount path to ``base_url``
        (giving ``…/mcp/mcp``), and RFC 9728 metadata must name the URL users paste."""
        return AnyHttpUrl(self.resource_url)

    def _canonical_resource(self, resource: str | None) -> str | None:
        """``resource`` with a trailing slash dropped (``/mcp/`` is the same endpoint)."""
        return None if resource is None else resource.rstrip("/")

    @staticmethod
    def _session() -> AsyncSession:
        return get_sessionmaker()()

    # ---------------------------------------------------------- clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._session() as session:
            client = await session.get(OAuthClient, client_id)
        if client is None:
            return None
        info = LibraryOAuthClient.model_validate(
            {
                **client.client_info,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            }
        )
        info._redirect_allowlist = self.redirect_allowlist
        return info

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")
        for uri in client_info.redirect_uris:
            if not redirect_uri_allowed(uri, self.redirect_allowlist):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"redirect_uri {uri} is not allowed by this server",
                )
        options = self.client_registration_options
        if client_info.scope is not None and options is not None and options.valid_scopes:
            invalid = set(client_info.scope.split()) - set(options.valid_scopes)
            if invalid:
                raise RegistrationError(
                    "invalid_client_metadata",
                    f"requested scopes are not valid: {', '.join(sorted(invalid))}",
                )

        info: dict[str, Any] = client_info.model_dump(
            mode="json", exclude_none=True, exclude={"client_id", "client_secret"}
        )
        values = {
            "client_id": client_info.client_id,
            "client_secret": client_info.client_secret,
            "client_name": client_info.client_name,
            "client_info": info,
        }
        statement = insert(OAuthClient).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[OAuthClient.client_id],
            set_={key: statement.excluded[key] for key in values if key != "client_id"},
        )
        async with self._session() as session:
            await session.execute(statement)
            await session.commit()

    # ------------------------------------------------------- authorization

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """The SDK's hook: park the request (``authorize_bound``) and return
        the consent URL; the binding goes to the wrapping route's cookie.

        Outside that route there is nowhere to put the cookie, and a request
        nobody can bind could never be answered, so it refuses instead.
        """
        issued = _issued_authorizations.get()
        if issued is None:
            raise AuthorizeError(
                "server_error", "authorization requests must arrive through /authorize"
            )
        pending = await self.authorize_bound(client, params)
        issued.append(pending)
        return pending.consent_url

    async def authorize_bound(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> IssuedAuthorization:
        """Park an authorization request bound to a fresh browser secret."""
        resource = self._canonical_resource(params.resource)
        if resource is not None and resource != self.resource_url:
            # RFC 8707 says invalid_target; the SDK only allows these codes.
            raise AuthorizeError(
                "invalid_request", f"resource must be {self.resource_url} (got {params.resource})"
            )
        if client.client_id is None:
            raise AuthorizeError("unauthorized_client", "client_id is required")

        request_id = new_secret()
        binding = new_secret()
        stored = params.model_dump(mode="json")
        stored["resource"] = resource
        async with self._session() as session:
            await delete_expired(session)
            session.add(
                OAuthAuthorizationRequest(
                    id_hash=sha256_hex(request_id),
                    binding_hash=sha256_hex(binding),
                    client_id=client.client_id,
                    params=stored,
                    expires_at=now() + AUTHORIZATION_REQUEST_TTL,
                )
            )
            await session.commit()
        return IssuedAuthorization(
            consent_url=f"{self.consent_url}?{urlencode({'request': request_id})}",
            request_id=request_id,
            binding=binding,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> LibraryAuthorizationCode | None:
        async with self._session() as session:
            row = (
                await session.execute(
                    select(OAuthAuthorizationCode, OAuthGrant, User)
                    .join(OAuthGrant, OAuthGrant.id == OAuthAuthorizationCode.grant_id)
                    .join(User, User.id == OAuthGrant.user_id)
                    .where(
                        OAuthAuthorizationCode.code_hash == sha256_hex(authorization_code),
                        OAuthGrant.client_id == client.client_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        code, grant, user = row
        if code.expires_at <= now() or grant.revoked_at is not None or not user.is_active:
            return None
        return LibraryAuthorizationCode(
            code=authorization_code,
            scopes=list(code.scopes),
            expires_at=code.expires_at.timestamp(),
            client_id=grant.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=AnyUrl(code.redirect_uri),
            redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
            resource=code.resource,
            subject=str(user.id),
            grant_id=grant.id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthTokenResponse:
        async with self._session() as session:
            # Single use: whichever exchange deletes the row wins; a second
            # (or concurrent) exchange of the same code finds nothing.
            consumed = (
                await session.execute(
                    delete(OAuthAuthorizationCode)
                    .where(OAuthAuthorizationCode.code_hash == sha256_hex(authorization_code.code))
                    .returning(
                        OAuthAuthorizationCode.grant_id,
                        OAuthAuthorizationCode.expires_at,
                        OAuthAuthorizationCode.resource,
                        OAuthAuthorizationCode.scopes,
                    )
                )
            ).one_or_none()
            if consumed is None:
                await session.rollback()
                raise TokenError("invalid_grant", "authorization code was already used")
            grant_id, expires_at, resource, scopes = consumed
            grant = await self._live_grant(session, grant_id, client)
            if (
                grant is None
                or expires_at <= now()
                or (resource is not None and resource != self.resource_url)
            ):
                await session.commit()  # the code stays consumed
                raise TokenError("invalid_grant", "authorization code is no longer valid")
            response = self._issue(
                session,
                grant,
                family_id=uuid4().hex,
                access_scopes=list(scopes),
                refresh_scopes=list(scopes),
            )
            grant.last_used_at = now()
            await delete_expired(session)
            await session.commit()
        return response

    # -------------------------------------------------------------- refresh

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> LibraryRefreshToken | None:
        if not refresh_token.startswith(REFRESH_TOKEN_PREFIX):
            return None
        async with self._session() as session:
            row = (
                await session.execute(
                    select(OAuthToken, OAuthGrant, User)
                    .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
                    .join(User, User.id == OAuthGrant.user_id)
                    .where(
                        OAuthToken.token_hash == sha256_hex(refresh_token),
                        OAuthToken.kind == "refresh",
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            token, grant, user = row
            if grant.client_id != client.client_id:
                return None
            if token.revoked_at is not None:
                # Replay of a rotated (or revoked) refresh token: whoever holds
                # it is not the legitimate client, or the legitimate client lost
                # it. Either way the chain is compromised.
                logger.warning(
                    "oauth refresh token replayed; revoking family",
                    extra={"grant_id": grant.id, "family_id": token.family_id},
                )
                await revoke_family(session, token.family_id)
                await session.commit()
                return None
        if token.expires_at <= now() or grant.revoked_at is not None or not user.is_active:
            return None
        return LibraryRefreshToken(
            token=refresh_token,
            client_id=grant.client_id,
            scopes=list(token.scopes),
            expires_at=_timestamp(token.expires_at),
            subject=str(user.id),
            grant_id=grant.id,
            family_id=token.family_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthTokenResponse:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "requested scopes exceed the original grant")
        async with self._session() as session:
            rotated = (
                await session.execute(
                    update(OAuthToken)
                    .where(
                        OAuthToken.token_hash == sha256_hex(refresh_token.token),
                        OAuthToken.kind == "refresh",
                        OAuthToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now())
                    .returning(OAuthToken.grant_id, OAuthToken.family_id)
                )
            ).one_or_none()
            if rotated is None:
                # Lost a race with another use of the same refresh token.
                family_id = getattr(refresh_token, "family_id", None)
                if family_id is not None:
                    await revoke_family(session, family_id)
                await session.commit()
                raise TokenError("invalid_grant", "refresh token was already used")
            grant_id, family_id = rotated
            # Rotation retires the family's earlier access tokens too.
            await revoke_family(session, family_id)
            grant = await self._live_grant(session, grant_id, client)
            if grant is None:
                await session.commit()
                raise TokenError("invalid_grant", "the grant behind this token was revoked")
            # RFC 6749 §6: a narrower request narrows the access token only;
            # the new refresh token keeps what the old one carried.
            response = self._issue(
                session,
                grant,
                family_id=family_id,
                access_scopes=scopes,
                refresh_scopes=list(refresh_token.scopes),
            )
            grant.last_used_at = now()
            await session.commit()
        return response

    # --------------------------------------------------------------- access

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            return None
        async with self._session() as session:
            row = (
                await session.execute(
                    select(OAuthToken, OAuthGrant, User)
                    .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
                    .join(User, User.id == OAuthGrant.user_id)
                    .where(
                        OAuthToken.token_hash == sha256_hex(token),
                        OAuthToken.kind == "access",
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            record, grant, user = row
            moment = now()
            if (
                record.revoked_at is not None
                or record.expires_at <= moment
                or grant.revoked_at is not None
                or not user.is_active
                or record.resource != self.resource_url
            ):
                return None
            if grant.last_used_at is None or moment - grant.last_used_at >= TOUCH_INTERVAL:
                grant.last_used_at = moment
                await session.commit()
        return AccessToken(
            token=token,
            client_id=grant.client_id,
            scopes=list(record.scopes),
            expires_at=_timestamp(record.expires_at),
            resource=self.resource_url,
            subject=str(user.id),
            claims={"user_id": user.id, "username": user.username, "auth": "oauth"},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke the token's whole family: its access and refresh tokens together."""
        async with self._session() as session:
            family_id = (
                await session.execute(
                    select(OAuthToken.family_id).where(
                        OAuthToken.token_hash == sha256_hex(token.token)
                    )
                )
            ).scalar_one_or_none()
            if family_id is None:
                return
            await revoke_family(session, family_id)
            await session.commit()

    # --------------------------------------------------------------- routes

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """FastMCP's OAuth routes, with ``/authorize`` wrapped to bind the request.

        The SDK's handler builds its 302 from the URL ``authorize`` returns and
        offers no hook for headers, so the route is replaced — as FastMCP
        itself replaces ``/token`` — by one that runs the same handler and
        then sets each parked request's binding cookie on its response.
        """
        return [
            self._binding_authorize_route(route) if route.path == AUTHORIZATION_PATH else route
            for route in super().get_routes(mcp_path)
        ]

    def _binding_authorize_route(self, route: Route) -> Route:
        sdk_handle = route.endpoint
        secure = urlsplit(self.issuer).scheme == "https"
        max_age = int(AUTHORIZATION_REQUEST_TTL.total_seconds())

        async def handle(request: Request) -> Response:
            issued: list[IssuedAuthorization] = []
            reset = _issued_authorizations.set(issued)
            try:
                response: Response = await sdk_handle(request)
            finally:
                _issued_authorizations.reset(reset)
            for pending in issued:
                # Lax: sent on the top-level navigation into the SPA and on its
                # same-origin API calls; never on cross-site subrequests.
                response.set_cookie(
                    binding_cookie_name(pending.request_id),
                    pending.binding,
                    max_age=max_age,
                    path="/",
                    secure=secure,
                    httponly=True,
                    samesite="lax",
                )
            return response

        return Route(route.path, endpoint=handle, methods=route.methods, name=route.name)

    # -------------------------------------------------------------- helpers

    async def _live_grant(
        self, session: AsyncSession, grant_id: int, client: OAuthClientInformationFull
    ) -> OAuthGrant | None:
        """The grant, iff it belongs to ``client``, is unrevoked and its user active."""
        row = (
            await session.execute(
                select(OAuthGrant, User)
                .join(User, User.id == OAuthGrant.user_id)
                .where(OAuthGrant.id == grant_id)
            )
        ).one_or_none()
        if row is None:
            return None
        grant, user = row
        if grant.client_id != client.client_id or grant.revoked_at is not None:
            return None
        return grant if user.is_active else None

    def _issue(
        self,
        session: AsyncSession,
        grant: OAuthGrant,
        *,
        family_id: str,
        access_scopes: list[str],
        refresh_scopes: list[str],
    ) -> OAuthTokenResponse:
        """Add a fresh access + refresh pair to the session; return the SDK
        response, whose ``scope`` is the access token's (RFC 6749 §5.1)."""
        access = new_secret(ACCESS_TOKEN_PREFIX)
        refresh = new_secret(REFRESH_TOKEN_PREFIX)
        moment = now()
        for kind, raw, ttl, scopes in (
            ("access", access, ACCESS_TOKEN_TTL, access_scopes),
            ("refresh", refresh, REFRESH_TOKEN_TTL, refresh_scopes),
        ):
            session.add(
                OAuthToken(
                    grant_id=grant.id,
                    kind=kind,
                    token_hash=sha256_hex(raw),
                    family_id=family_id,
                    resource=self.resource_url,
                    scopes=scopes,
                    expires_at=moment + ttl,
                )
            )
        return OAuthTokenResponse(
            access_token=access,
            expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
            scope=" ".join(access_scopes) if access_scopes else None,
            refresh_token=refresh,
        )
