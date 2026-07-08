"""Backfill better titles onto existing Ask conversations.

Older threads were named with the truncated first question (the placeholder
title ``_thread_title`` still applies to a brand-new thread). This one-off
script re-titles any thread still carrying that placeholder by summarising its
first question/answer with the title model — exactly how new threads are now
named (see ``library.ask.engine.generate_thread_title``).

Idempotent and safe to re-run: a thread whose title no longer equals the
placeholder (already regenerated, or manually renamed) is skipped, and threads
with no turns are skipped.

Usage::

    uv run python -m scripts.backfill_ask_titles --dry-run
    uv run python -m scripts.backfill_ask_titles [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.api.ask import _thread_title
from library.ask.engine import generate_thread_title
from library.config import get_settings
from library.db import get_sessionmaker
from library.models import AskThread, AskTurn

logger = logging.getLogger("backfill_ask_titles")


def is_placeholder_title(title: str, first_question: str) -> bool:
    """True when ``title`` is still the auto-generated placeholder for its thread.

    The placeholder is the truncated first question (``_thread_title``); a thread
    that has been re-titled — by this script or by a manual rename — no longer
    matches it and must be left untouched.
    """
    return title == _thread_title(first_question)


async def _first_turn(session: AsyncSession, thread_id: int) -> tuple[str, str] | None:
    """The thread's earliest turn as ``(question, answer)``, or None if it has none."""
    row = (
        await session.execute(
            select(AskTurn.query, AskTurn.answer)
            .where(AskTurn.thread_id == thread_id)
            .order_by(AskTurn.created_at, AskTurn.id)
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


async def backfill(*, dry_run: bool, limit: int | None) -> int:
    """Re-title placeholder-named threads; return how many were (or would be) changed."""
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise SystemExit("No Anthropic API key configured (set LIBRARY_ANTHROPIC_API_KEY).")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        thread_ids = (
            (await session.execute(select(AskThread.id).order_by(AskThread.id))).scalars().all()
        )

    changed = 0
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
        for thread_id in thread_ids:
            if limit is not None and changed >= limit:
                break
            async with sessionmaker() as session:
                thread = await session.get(AskThread, thread_id)
                if thread is None:
                    continue
                first = await _first_turn(session, thread_id)
                if first is None:
                    continue  # a thread with no turns has nothing to summarise
                question, answer = first
                if not is_placeholder_title(thread.title, question):
                    continue

                try:
                    result = await generate_thread_title(
                        client,
                        model=settings.ask_title_model,
                        question=question,
                        answer=answer,
                    )
                except Exception:
                    logger.warning(
                        "title generation failed for thread %s; skipping", thread_id, exc_info=True
                    )
                    continue
                if not result.title or result.title == thread.title:
                    continue

                logger.info("thread %s: %r -> %r", thread_id, thread.title, result.title)
                changed += 1
                if dry_run:
                    continue
                thread.title = result.title
                await session.commit()

    verb = "would be retitled (dry run)" if dry_run else "retitled"
    logger.info("%s thread(s) %s", changed, verb)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Ask conversation titles.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N retitles.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(backfill(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()
