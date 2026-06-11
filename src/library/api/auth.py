"""Auth endpoints: login/logout, current user, API-token management.

Two routers: ``login_router`` is mounted without the auth gate (it IS the
way in); ``router`` carries everything else and is included in the
protected ``/api`` router in app.py. See docs/api.md §1.9.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import CSRF_COOKIE, SESSION_COOKIE, current_user
from library.auth.service import (
    authenticate_user,
    create_api_token,
    create_session,
    revoke_api_token,
    revoke_session,
)
from library.config import get_settings
from library.db import get_session
from library.models import ApiToken, User
from library.schemas import (
    LoginRequest,
    TokenCreatedResponse,
    TokenCreateRequest,
    TokenInfo,
    UserOut,
)

login_router: APIRouter = APIRouter(tags=["auth"])
router: APIRouter = APIRouter(tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, display_name=user.display_name)


def _set_cookie(response: Response, name: str, value: str, *, httponly: bool) -> None:
    settings = get_settings()
    response.set_cookie(
        name,
        value,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
        httponly=httponly,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@login_router.post(
    "/auth/login",
    response_model=UserOut,
    summary="Log in",
    responses={401: {"description": "Invalid credentials (generic; no enumeration)"}},
)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> UserOut:
    """Verify credentials; set the session and CSRF cookies.

    Unknown username, wrong password, and disabled account all return the
    same generic 401.
    """
    user = await authenticate_user(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    raw = await create_session(db, user)
    _set_cookie(response, SESSION_COOKIE, raw, httponly=True)
    _set_cookie(response, CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False)
    return _user_out(user)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(current_user)],
) -> None:
    """Delete the session row (immediate server-side death) and clear cookies."""
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        await revoke_session(db, raw)
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")


@router.get("/auth/me", response_model=UserOut, summary="The authenticated user")
async def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    """Works with either credential: session cookie or bearer token."""
    return _user_out(user)


@router.get(
    "/auth/tokens",
    response_model=list[TokenInfo],
    summary="List your API tokens",
)
async def list_tokens(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> list[TokenInfo]:
    """Your tokens, newest first — names and timestamps only, never secrets."""
    tokens = (
        (
            await db.execute(
                select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        TokenInfo(
            id=token.id,
            name=token.name,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            revoked_at=token.revoked_at,
        )
        for token in tokens
    ]


@router.post(
    "/auth/tokens",
    response_model=TokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API token",
)
async def create_token(
    payload: TokenCreateRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> TokenCreatedResponse:
    """Returns the bearer secret — the only time it is ever shown."""
    raw, token = await create_api_token(db, user, payload.name)
    return TokenCreatedResponse(
        id=token.id, name=token.name, token=raw, created_at=token.created_at
    )


@router.delete(
    "/auth/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API token",
    responses={404: {"description": "No such token of yours"}},
)
async def delete_token(
    token_id: int,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> None:
    """Sets revoked_at; takes effect immediately. Other users' tokens 404."""
    if not await revoke_api_token(db, user, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")
