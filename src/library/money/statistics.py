"""Planner statistics for the tables the money views read.

``payments`` is a recursive view and ``spend_facts`` ranks and aggregates over
it. On tables that have never been analysed the planner estimates a few dozen
rows for the recursive step and picks nested loops; measured on 4,000
documents, ``sum_amount`` then ran past a 120 s timeout against 1.34 s after
``ANALYZE`` (``docs/money-facts.md`` §6.2). Autovacuum closes the window on its
own, but only after a delay, so the two bulk writers of money facts, the amount
backfill and the paperless import, analyse once they are done.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Every table the money views read, plus ``senders`` (the group-by and filter
#: side of a spending question). ``tests/test_money_statistics.py`` pins that
#: this covers each base table of ``payments`` and ``spend_facts``.
MONEY_TABLES: tuple[str, ...] = (
    "documents",
    "document_labels",
    "facet_values",
    "facets",
    "line_labels",
    "payment_overrides",
    "senders",
    "spend_lines",
)


async def analyze_money_tables(session: AsyncSession) -> None:
    """Refresh planner statistics for :data:`MONEY_TABLES`.

    Issues one ``ANALYZE`` of its own, after committing whatever the caller
    still has open, then commits again so the session is left clean. Uses
    ``ANALYZE``, never ``VACUUM``, which cannot run inside a transaction.
    """
    await session.commit()
    await session.execute(text("ANALYZE " + ", ".join(MONEY_TABLES)))
    await session.commit()
    logger.info("analysed money tables: %s", ", ".join(MONEY_TABLES))
