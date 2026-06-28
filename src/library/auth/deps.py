"""FastAPI dependencies: the auth gate and the CSRF double-submit check.

Both are attached at include-router level in app.py, so every `/api`
route (except the separately-mounted login route) is protected without
per-endpoint ceremony. ``current_user`` is also usable inside endpoints
that need the user object — FastAPI's per-request dependency cache means
it runs once.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.service import validate_api_token, validate_session
from library.db import get_session
from library.models import User

SESSION_COOKIE: str = "library_session"
CSRF_COOKIE: str = "library_csrftoken"
CSRF_HEADER: str = "X-CSRF-Token"

_CSRF_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return None


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """The authenticated user, via bearer API token or session cookie; else 401.

    A presented ``Authorization: Bearer`` header is authoritative: when it
    is invalid the request fails even if a valid session cookie rides along.
    """
    bearer = _bearer_token(request)
    if bearer is not None:
        user = await validate_api_token(db, bearer)
        if user is not None:
            return user
        raise _unauthenticated()
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        user = await validate_session(db, raw)
        if user is not None:
            return user
    raise _unauthenticated()


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    """The authenticated user, but only if they are an admin; else 403.

    Layers on top of ``current_user`` so anonymous requests still get a 401
    (from ``current_user``) and merely non-admin requests get a 403.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin privileges required",
        )
    return user


async def csrf_protect(request: Request) -> None:
    """Double-submit CSRF check for cookie-authenticated state changes.

    Exempt: safe methods, and requests carrying an ``Authorization: Bearer``
    header (cross-site pages cannot set custom headers; the bearer secret
    itself proves intent).
    """
    if request.method in _CSRF_SAFE_METHODS or _bearer_token(request) is not None:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"missing or invalid {CSRF_HEADER} header",
        )
