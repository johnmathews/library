"""Resolving which LLM backend a surface uses, at request time.

Two layers, in order of precedence:

1. An ``instance_settings`` row, written by an admin through the API. Absent by
   default.
2. The ``Settings`` value from the environment.

The DB is an *override*, so an empty table behaves exactly as the environment
says — which is what every deployment gets before an admin touches anything.

**Why this is resolved per request rather than at startup.** The whole point of
the toggle is that it changes without a restart, so a value read once into a
module global would be stale the moment it is used. That also rules out
validating credentials at startup: the setting can become `subscription` long
after boot. The guard moved to the two places that still work — write time
(:func:`set_backend` refuses to enable a backend that cannot authenticate) and
``/healthz`` — which is a better trade anyway, since it reports the problem to
the person making the change at the moment they make it.
"""

import logging
from typing import cast, get_args

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.llm import oauth
from library.models import InstanceSetting

logger = logging.getLogger(__name__)

#: Surfaces whose backend is switchable, mapped to the ``Settings`` field that
#: supplies the default. Adding a surface here is all that is needed to make it
#: configurable — the API and the UI are driven off this mapping.
BACKEND_SURFACES: dict[str, str] = {
    "ask": "ask_llm_backend",
}

_VALID_BACKENDS: tuple[str, ...] = get_args(LLMBackend)


def _key(surface: str) -> str:
    return f"llm_backend.{surface}"


class UnknownSurfaceError(ValueError):
    """The named surface has no switchable backend."""


class BackendUnavailableError(ValueError):
    """The requested backend cannot be used with the current configuration."""


def config_default(surface: str, settings: Settings) -> LLMBackend:
    """The environment-supplied backend for ``surface``."""
    try:
        field = BACKEND_SURFACES[surface]
    except KeyError as exc:
        raise UnknownSurfaceError(f"unknown LLM surface {surface!r}") from exc
    return cast(LLMBackend, getattr(settings, field))


async def resolve_backend(session: AsyncSession, surface: str, settings: Settings) -> LLMBackend:
    """Return the backend in force for ``surface`` right now.

    Falls back to the environment default when no override row exists, and also
    when the stored value is not a backend we recognise — a row left behind by a
    downgrade should degrade to the configured default rather than take the
    surface down.
    """
    default = config_default(surface, settings)
    stored = await session.get(InstanceSetting, _key(surface))
    if stored is None:
        return default
    if stored.value not in _VALID_BACKENDS:
        logger.warning(
            "instance_settings[%s] holds unrecognised backend %r; using %r",
            _key(surface),
            stored.value,
            default,
        )
        return default
    return cast(LLMBackend, stored.value)


async def get_backends(session: AsyncSession, settings: Settings) -> dict[str, LLMBackend]:
    """Resolve every switchable surface at once, for the settings view."""
    return {
        surface: await resolve_backend(session, surface, settings) for surface in BACKEND_SURFACES
    }


def check_backend_available(backend: LLMBackend, settings: Settings) -> None:
    """Raise :class:`BackendUnavailableError` if ``backend`` cannot authenticate.

    This is the guard the startup validator used to be. Enforcing it on write
    means an admin who flips the toggle without provisioning credentials is told
    so immediately, instead of the next person to ask a question discovering it
    as a failed query.
    """
    if backend == "subscription":
        status, detail = oauth.token_health(settings.claude_config_dir)
        if status == "unhealthy":
            raise BackendUnavailableError(f"the Claude subscription is not usable: {detail}")
    elif backend == "api" and settings.anthropic_api_key is None:
        raise BackendUnavailableError(
            "no Anthropic API key is configured (LIBRARY_ANTHROPIC_API_KEY)"
        )


async def set_backend(
    session: AsyncSession,
    surface: str,
    backend: LLMBackend,
    settings: Settings,
    *,
    user_id: int | None = None,
) -> LLMBackend:
    """Persist an override for ``surface``, validating it first.

    Upserts rather than insert-or-update in two statements: two admins saving at
    once would otherwise race on the primary key and one would get a constraint
    error instead of a saved setting.
    """
    if surface not in BACKEND_SURFACES:
        raise UnknownSurfaceError(f"unknown LLM surface {surface!r}")
    if backend not in _VALID_BACKENDS:
        raise BackendUnavailableError(f"unknown backend {backend!r}")

    check_backend_available(backend, settings)

    statement = (
        pg_insert(InstanceSetting)
        .values(key=_key(surface), value=backend, updated_by_id=user_id)
        .on_conflict_do_update(
            index_elements=[InstanceSetting.key],
            # updated_at explicitly: ``onupdate`` only fires on ORM-level
            # updates, and this is a Core upsert — without it the timestamp
            # keeps its insert-time value and the audit trail silently freezes.
            set_={"value": backend, "updated_by_id": user_id, "updated_at": func.now()},
        )
    )
    await session.execute(statement)
    await session.commit()
    logger.info("LLM backend for %s set to %s by user %s", surface, backend, user_id)
    return backend


async def clear_backend(session: AsyncSession, surface: str) -> None:
    """Drop the override so ``surface`` follows the environment again."""
    if surface not in BACKEND_SURFACES:
        raise UnknownSurfaceError(f"unknown LLM surface {surface!r}")
    stored = await session.get(InstanceSetting, _key(surface))
    if stored is not None:
        await session.delete(stored)
        await session.commit()


async def credential_health(settings: Settings) -> tuple[str, str]:
    """Subscription credential status, for the settings view and /healthz."""
    return oauth.token_health(settings.claude_config_dir)


async def list_overrides(session: AsyncSession) -> list[InstanceSetting]:
    """Every stored backend override row, for the settings view."""
    result = await session.execute(
        select(InstanceSetting).where(InstanceSetting.key.like("llm_backend.%"))
    )
    return list(result.scalars())
