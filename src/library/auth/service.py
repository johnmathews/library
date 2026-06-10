"""Session and API-token lifecycle: create, validate, revoke.

Tokens are opaque secrets held only by the client; the database stores
their SHA-256 hex digest (``token_hash``), so a database leak does not
leak credentials. Validation refreshes sliding expiry / last-used
timestamps, write-throttled to roughly once per ``TOUCH_INTERVAL`` so
every request does not become an UPDATE.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from library.auth.passwords import verify_password
from library.config import get_settings
from library.models import ApiToken, User
from library.models import Session as SessionModel

API_TOKEN_PREFIX: str = "library_"
_TOKEN_BYTES: int = 32  # 256-bit secrets
TOUCH_INTERVAL: timedelta = timedelta(minutes=5)


def sha256_hex(value: str) -> str:
    """SHA-256 hex digest of a token string (the stored form)."""
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """The user, iff the username exists, is active, and the password matches."""
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user


# -------------------------------------------------------------------- sessions


async def create_session(db: AsyncSession, user: User) -> str:
    """Create a session row for the user; returns the raw cookie token."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    now = _now()
    db.add(
        SessionModel(
            token_hash=sha256_hex(raw),
            user_id=user.id,
            expires_at=now + timedelta(days=get_settings().session_ttl_days),
            last_seen_at=now,
        )
    )
    await db.commit()
    return raw


async def validate_session(db: AsyncSession, raw: str) -> User | None:
    """The session's active user, refreshing sliding expiry; None if invalid."""
    record = (
        await db.execute(
            select(SessionModel)
            .options(joinedload(SessionModel.user))
            .where(SessionModel.token_hash == sha256_hex(raw))
        )
    ).scalar_one_or_none()
    now = _now()
    if record is None or record.expires_at <= now or not record.user.is_active:
        return None
    if record.last_seen_at is None or now - record.last_seen_at >= TOUCH_INTERVAL:
        record.last_seen_at = now
        record.expires_at = now + timedelta(days=get_settings().session_ttl_days)
        await db.commit()
    return record.user


async def revoke_session(db: AsyncSession, raw: str) -> None:
    """Delete the session row; the cookie is dead server-side immediately."""
    await db.execute(delete(SessionModel).where(SessionModel.token_hash == sha256_hex(raw)))
    await db.commit()


# ------------------------------------------------------------------ API tokens


async def create_api_token(db: AsyncSession, user: User, name: str) -> tuple[str, ApiToken]:
    """Create a named API token; returns (one-time secret, token row)."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    token = ApiToken(user_id=user.id, name=name, token_hash=sha256_hex(raw))
    db.add(token)
    await db.commit()
    return raw, token


async def validate_api_token(db: AsyncSession, raw: str) -> User | None:
    """The token's active user, touching last_used_at; None if invalid/revoked."""
    token = (
        await db.execute(
            select(ApiToken)
            .options(joinedload(ApiToken.user))
            .where(ApiToken.token_hash == sha256_hex(raw))
        )
    ).scalar_one_or_none()
    if token is None or token.revoked_at is not None or not token.user.is_active:
        return None
    now = _now()
    if token.last_used_at is None or now - token.last_used_at >= TOUCH_INTERVAL:
        token.last_used_at = now
        await db.commit()
    return token.user


async def revoke_api_token(db: AsyncSession, user: User, token_id: int) -> bool:
    """Revoke the user's token by id; False if it isn't theirs (or unknown)."""
    token = (
        await db.execute(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
        )
    ).scalar_one_or_none()
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = _now()
        await db.commit()
    return True


async def revoke_all_credentials(db: AsyncSession, user_id: int) -> None:
    """Kill every session and API token of a user (used by `user disable`)."""
    await db.execute(delete(SessionModel).where(SessionModel.user_id == user_id))
    await db.execute(
        update(ApiToken)
        .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.commit()
