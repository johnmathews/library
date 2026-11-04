"""OAuth consent and connected-app lifecycle, for the API behind the consent page.

``library.auth.oauth_provider`` is what the MCP SDK calls (register, authorize,
token, revoke). This module is the other half: what a *signed-in user* does —
look at a pending authorization request, approve or deny it, list the apps they
connected and revoke one. Both halves share the constants and helpers here so
the code minted on approval is exactly what the token endpoint consumes.

Every secret handed to a client (request id, code, token) or to the browser
(the request's binding cookie) is stored only as its SHA-256 hex digest
(``library.auth.service.sha256_hex``), as API tokens are. Functions that
change state commit, like the rest of ``library.auth``.

A pending request is **bound to the browser that started it**: ``/mcp/authorize``
sets a per-request cookie (``binding_cookie_name``) and stores its hash on the
request, and every function here takes that cookie's value as ``binding``. A
missing or different binding reads exactly like an unknown request, so a
consent link forwarded to someone else — who is signed in, but whose browser
never followed ``/authorize`` — cannot be approved by them (consent phishing).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from library.auth.service import sha256_hex
from library.models import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
    User,
)

ACCESS_TOKEN_PREFIX: str = "libmcp_at_"
REFRESH_TOKEN_PREFIX: str = "libmcp_rt_"
AUTHORIZATION_REQUEST_TTL: timedelta = timedelta(minutes=10)
AUTHORIZATION_CODE_TTL: timedelta = timedelta(minutes=5)
ACCESS_TOKEN_TTL: timedelta = timedelta(hours=1)
REFRESH_TOKEN_TTL: timedelta = timedelta(days=30)
_SECRET_BYTES: int = 32  # 256-bit secrets, above the SDK's 160-bit SHOULD

#: Prefix of the cookie that binds a pending authorization request to the
#: browser that followed ``/mcp/authorize`` (see ``binding_cookie_name``).
BINDING_COOKIE_PREFIX: str = "library_oauth_request_"
_BINDING_COOKIE_ID_CHARS: int = 16


def now() -> datetime:
    """The current time, timezone-aware (UTC)."""
    return datetime.now(UTC)


def new_secret(prefix: str = "") -> str:
    """A fresh URL-safe random secret, optionally prefixed."""
    return prefix + secrets.token_urlsafe(_SECRET_BYTES)


def binding_cookie_name(request_id: str) -> str:
    """The binding cookie's name for one authorization request.

    One cookie per request, so a browser can have several flows in progress
    (two apps connecting at once) without them sharing or overwriting a value.
    The name carries a prefix of the request id's SHA-256, not the id itself.
    """
    return BINDING_COOKIE_PREFIX + sha256_hex(request_id)[:_BINDING_COOKIE_ID_CHARS]


def with_query(uri: str, **params: str | None) -> str:
    """``uri`` with ``params`` appended to its query; ``None`` values are skipped.

    Existing query parameters (including blank ones) are preserved, so a client
    redirect URI that carries its own query still works.
    """
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit(parts._replace(query=urlencode(query)))


async def delete_expired(session: AsyncSession) -> None:
    """Drop expired requests, codes and tokens (indexed, cheap; caller commits).

    A rotated refresh token is kept until its own expiry so a replay can still
    be recognised and its family revoked; once expired it is just dead.
    """
    moment = now()
    await session.execute(
        delete(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.expires_at < moment)
    )
    await session.execute(
        delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.expires_at < moment)
    )
    await session.execute(delete(OAuthToken).where(OAuthToken.expires_at < moment))


async def revoke_family(session: AsyncSession, family_id: str) -> None:
    """Revoke every live token in a refresh-rotation family (caller commits)."""
    await session.execute(
        update(OAuthToken)
        .where(OAuthToken.family_id == family_id, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )


# ------------------------------------------------------------ consent requests


class AuthorizationRequestError(Exception):
    """The consent page's request id cannot be acted on."""


class AuthorizationRequestNotFound(AuthorizationRequestError):
    """Unknown, already used, cleaned up after expiry, or started in another
    browser (no matching binding cookie) — deliberately indistinguishable."""


class AuthorizationRequestExpired(AuthorizationRequestError):
    """Known but older than ``AUTHORIZATION_REQUEST_TTL``; the client must restart."""


@dataclass(frozen=True)
class AuthorizationRequestInfo:
    """What the consent page shows about a pending authorization request."""

    client_id: str
    client_name: str | None
    redirect_uri: str
    redirect_host: str
    scopes: list[str]
    expires_at: datetime
    expired: bool


def _request_matches(request_id: str, binding: str) -> list[ColumnElement[bool]]:
    """WHERE clauses for the request ``request_id`` as seen by the browser
    holding the binding cookie ``binding``."""
    return [
        OAuthAuthorizationRequest.id_hash == sha256_hex(request_id),
        OAuthAuthorizationRequest.binding_hash == sha256_hex(binding),
    ]


async def _load_request(
    session: AsyncSession, request_id: str, binding: str | None
) -> tuple[OAuthAuthorizationRequest, OAuthClient] | None:
    if not binding:
        return None
    row = (
        await session.execute(
            select(OAuthAuthorizationRequest, OAuthClient)
            .join(OAuthClient, OAuthClient.client_id == OAuthAuthorizationRequest.client_id)
            .where(*_request_matches(request_id, binding))
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def _consume_request(
    session: AsyncSession, request_id: str, binding: str | None
) -> tuple[str, dict[str, Any], datetime] | None:
    """Delete the request in one statement; ``(client_id, params, expires_at)``,
    or None when there was nothing (left) to consume.

    Like a code exchange: of two concurrent answers to one request, whichever
    DELETE runs second matches no row, so only one ever mints a code.
    """
    if not binding:
        return None
    row = (
        await session.execute(
            delete(OAuthAuthorizationRequest)
            .where(*_request_matches(request_id, binding))
            .returning(
                OAuthAuthorizationRequest.client_id,
                OAuthAuthorizationRequest.params,
                OAuthAuthorizationRequest.expires_at,
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], dict(row[1]), row[2])


async def get_authorization_request(
    session: AsyncSession, request_id: str, *, binding: str | None
) -> AuthorizationRequestInfo | None:
    """The pending request behind ``/oauth/consent?request=<id>``, or None
    (also when ``binding`` is not this request's cookie)."""
    loaded = await _load_request(session, request_id, binding)
    if loaded is None:
        return None
    request, client = loaded
    redirect_uri = str(request.params["redirect_uri"])
    return AuthorizationRequestInfo(
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uri=redirect_uri,
        redirect_host=urlsplit(redirect_uri).hostname or "",
        scopes=list(request.params.get("scopes") or []),
        expires_at=request.expires_at,
        expired=request.expires_at <= now(),
    )


async def approve_authorization_request(
    session: AsyncSession, *, user: User, request_id: str, binding: str | None, issuer: str
) -> str:
    """Approve as ``user``: mint a single-use code and return the client redirect.

    Creates the user-and-client grant (or reuses a live one; a revoked one is
    replaced, so tokens from before the revocation stay dead). The request is
    consumed atomically (one DELETE … RETURNING), so concurrent approvals of
    one request mint one code. The returned URL is the client's redirect_uri with ``code``,
    ``state`` (when the client sent one) and ``iss`` (RFC 9207) appended.

    Raises ``AuthorizationRequestNotFound`` (also for a ``binding`` that is
    not this request's cookie) / ``AuthorizationRequestExpired``.
    """
    consumed = await _consume_request(session, request_id, binding)
    if consumed is None:
        await session.rollback()
        raise AuthorizationRequestNotFound(request_id)
    client_id, params, expires_at = consumed
    if expires_at <= now():
        await session.commit()  # the request stays consumed
        raise AuthorizationRequestExpired(request_id)

    grant = (
        await session.execute(
            select(OAuthGrant).where(
                OAuthGrant.user_id == user.id, OAuthGrant.client_id == client_id
            )
        )
    ).scalar_one_or_none()
    if grant is not None and grant.revoked_at is not None:
        await session.delete(grant)
        await session.flush()
        grant = None
    if grant is None:
        grant = OAuthGrant(user_id=user.id, client_id=client_id)
        session.add(grant)
        await session.flush()

    code = new_secret()
    session.add(
        OAuthAuthorizationCode(
            code_hash=sha256_hex(code),
            grant_id=grant.id,
            code_challenge=params["code_challenge"],
            redirect_uri=params["redirect_uri"],
            redirect_uri_provided_explicitly=params["redirect_uri_provided_explicitly"],
            resource=params.get("resource"),
            scopes=list(params.get("scopes") or []),
            expires_at=now() + AUTHORIZATION_CODE_TTL,
        )
    )
    await session.commit()
    return with_query(params["redirect_uri"], code=code, state=params.get("state"), iss=issuer)


async def deny_authorization_request(
    session: AsyncSession, *, request_id: str, binding: str | None, issuer: str
) -> str:
    """Deny: consume the request and return the redirect with ``error=access_denied``.

    An expired request is still answered with ``access_denied`` — the client
    gets a definite answer either way. Raises ``AuthorizationRequestNotFound``
    (also for a ``binding`` that is not this request's cookie).
    """
    consumed = await _consume_request(session, request_id, binding)
    if consumed is None:
        await session.rollback()
        raise AuthorizationRequestNotFound(request_id)
    _client_id, params, _expires_at = consumed
    await session.commit()
    return with_query(
        params["redirect_uri"], error="access_denied", state=params.get("state"), iss=issuer
    )


# --------------------------------------------------------------- connected apps


@dataclass(frozen=True)
class GrantInfo:
    """One connected app, as the user's Settings → Connected apps lists it."""

    id: int
    client_id: str
    client_name: str | None
    created_at: datetime
    last_used_at: datetime | None


async def list_grants(session: AsyncSession, user: User) -> list[GrantInfo]:
    """The user's live (unrevoked) grants, newest first."""
    grants = (
        (
            await session.execute(
                select(OAuthGrant)
                .options(joinedload(OAuthGrant.client))
                .where(OAuthGrant.user_id == user.id, OAuthGrant.revoked_at.is_(None))
                .order_by(OAuthGrant.created_at.desc(), OAuthGrant.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        GrantInfo(
            id=grant.id,
            client_id=grant.client_id,
            client_name=grant.client.client_name,
            created_at=grant.created_at,
            last_used_at=grant.last_used_at,
        )
        for grant in grants
    ]


async def revoke_grant(session: AsyncSession, user: User, grant_id: int) -> bool:
    """Revoke one of the user's grants and every code and token under it.

    False when the grant is unknown, someone else's, or already revoked —
    the API maps that to 404 without saying which.
    """
    grant = (
        await session.execute(
            select(OAuthGrant).where(
                OAuthGrant.id == grant_id,
                OAuthGrant.user_id == user.id,
                OAuthGrant.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        return False
    moment = now()
    grant.revoked_at = moment
    await session.execute(
        update(OAuthToken)
        .where(OAuthToken.grant_id == grant.id, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=moment)
    )
    await session.execute(
        delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.grant_id == grant.id)
    )
    await session.commit()
    return True
