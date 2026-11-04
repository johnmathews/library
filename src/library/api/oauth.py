"""OAuth consent and connected apps: the API behind ``/oauth/consent`` and Settings.

An MCP client (claude.ai, ChatGPT, Claude Code) sends the user's browser to
``/mcp/authorize``; the provider parks the request and redirects to the SPA's
consent page with ``?request=<id>``. These endpoints are what that page and the
"Connected apps" settings call. The state changes live in
``library.auth.oauth_service``; this module maps them to HTTP.

All routes sit under the protected ``/api`` router, so they need a signed-in
user and, for cookie requests that change state, the CSRF header. Approving
and denying additionally refuse bearer tokens: consent is a person clicking a
button in a browser, and a ``library_…`` token in a script must not be able
to mint OAuth codes for itself. When OAuth is off (``public_base_url`` unset
or not HTTPS) every route here answers 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import _bearer_token, current_user
from library.auth.oauth_provider import LibraryOAuthProvider
from library.auth.oauth_service import (
    AuthorizationRequestExpired,
    AuthorizationRequestNotFound,
    approve_authorization_request,
    deny_authorization_request,
    get_authorization_request,
    list_grants,
    revoke_grant,
)
from library.db import get_session
from library.models import User
from library.schemas import OAuthGrantOut, OAuthRedirectOut, OAuthRequestOut

router: APIRouter = APIRouter(tags=["oauth"])

#: The app's OAuth provider lives here (set by ``create_app``); ``None`` = off.
APP_STATE_KEY: str = "mcp_oauth_provider"

RequestId = Annotated[
    str,
    Path(min_length=1, max_length=128, description="The `request` query value of the consent URL."),
]

_NOT_FOUND = "unknown or already answered authorization request"
_EXPIRED = (
    "this authorization request expired; start connecting again from the app that sent you here"
)


def oauth_provider(request: Request) -> LibraryOAuthProvider:
    """The running app's OAuth provider; 404 when OAuth is off."""
    provider: LibraryOAuthProvider | None = getattr(request.app.state, APP_STATE_KEY, None)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OAuth is not enabled")
    return provider


def browser_session_only(request: Request) -> None:
    """403 for bearer-token requests: consent needs the signed-in browser session."""
    if _bearer_token(request) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="authorization requests can only be answered from a signed-in browser session",
        )


Provider = Annotated[LibraryOAuthProvider, Depends(oauth_provider)]
Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.get(
    "/oauth/requests/{request_id}",
    response_model=OAuthRequestOut,
    summary="A pending OAuth authorization request",
    responses={404: {"description": "Unknown or already answered request, or OAuth is off"}},
)
async def get_request(request_id: RequestId, db: Session, _provider: Provider) -> OAuthRequestOut:
    """What the consent page shows: which app asks, and where the answer goes.

    An expired request still answers 200 with ``expired: true``, so the page
    can say so; approving it then answers 410.
    """
    info = await get_authorization_request(db, request_id)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return OAuthRequestOut(
        client_id=info.client_id,
        client_name=info.client_name,
        redirect_uri=info.redirect_uri,
        redirect_host=info.redirect_host,
        scopes=info.scopes,
        expires_at=info.expires_at,
        expired=info.expired,
    )


@router.post(
    "/oauth/requests/{request_id}/approve",
    response_model=OAuthRedirectOut,
    summary="Approve an OAuth authorization request",
    dependencies=[Depends(oauth_provider), Depends(browser_session_only)],
    responses={
        403: {"description": "Bearer-token request, or missing CSRF header"},
        404: {"description": "Unknown or already answered request, or OAuth is off"},
        410: {"description": "The request expired; the client must start again"},
    },
)
async def approve(
    request_id: RequestId, db: Session, user: CurrentUser, provider: Provider
) -> OAuthRedirectOut:
    """Connect the app to the signed-in user's account.

    Returns the client's redirect URL carrying a single-use ``code``, the
    client's ``state`` and ``iss`` (RFC 9207). The page sends the browser
    there. The request is consumed either way.
    """
    try:
        redirect_to = await approve_authorization_request(
            db, user=user, request_id=request_id, issuer=provider.issuer
        )
    except AuthorizationRequestExpired:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=_EXPIRED) from None
    except AuthorizationRequestNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from None
    return OAuthRedirectOut(redirect_to=redirect_to)


@router.post(
    "/oauth/requests/{request_id}/deny",
    response_model=OAuthRedirectOut,
    summary="Deny an OAuth authorization request",
    dependencies=[Depends(oauth_provider), Depends(browser_session_only)],
    responses={
        403: {"description": "Bearer-token request, or missing CSRF header"},
        404: {"description": "Unknown or already answered request, or OAuth is off"},
    },
)
async def deny(request_id: RequestId, db: Session, provider: Provider) -> OAuthRedirectOut:
    """Refuse: the client's redirect URL with ``error=access_denied``, ``state``
    and ``iss``. An expired request is denied the same way, so the client
    always gets an answer."""
    try:
        redirect_to = await deny_authorization_request(
            db, request_id=request_id, issuer=provider.issuer
        )
    except AuthorizationRequestNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from None
    return OAuthRedirectOut(redirect_to=redirect_to)


@router.get(
    "/oauth/grants",
    response_model=list[OAuthGrantOut],
    summary="List your connected apps",
    responses={404: {"description": "OAuth is off"}},
)
async def get_grants(db: Session, user: CurrentUser, _provider: Provider) -> list[OAuthGrantOut]:
    """Apps connected to your account over OAuth, newest first (revoked ones omitted)."""
    return [
        OAuthGrantOut(
            id=grant.id,
            client_id=grant.client_id,
            client_name=grant.client_name,
            created_at=grant.created_at,
            last_used_at=grant.last_used_at,
        )
        for grant in await list_grants(db, user)
    ]


@router.delete(
    "/oauth/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect an app",
    responses={404: {"description": "No such live grant of yours, or OAuth is off"}},
)
async def delete_grant(
    grant_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
    db: Session,
    user: CurrentUser,
    _provider: Provider,
) -> None:
    """Revoke the grant and every token under it; takes effect immediately.
    Another user's grant answers 404, the same as an unknown one."""
    if not await revoke_grant(db, user, grant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="grant not found")
