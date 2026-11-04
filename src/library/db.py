"""Async database engine, session factory, and FastAPI session dependency."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """The process-wide async engine, built lazily from settings.

    SQLAlchemy's default async pool: 5 connections plus 10 overflow, and a
    caller that finds all 15 busy waits up to 30 s for one rather than failing.
    That queueing is what keeps a burst of requests inside Postgres's
    connection budget, which the worker and the events broker share.
    """
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The process-wide session factory bound to the engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Close the process-wide engine's connections and forget it.

    asyncpg connections belong to the event loop that opened them. The app's
    lifespan calls this on the way out, so the next lifespan (a test client
    runs each app in a loop of its own) builds a fresh engine in its own loop
    instead of reusing pooled connections from a loop that has gone away.
    Safe to call when no engine was ever built.
    """
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one AsyncSession per request."""
    async with get_sessionmaker()() as session:
        yield session
