"""Finding documents whose LLM metadata was skipped by the daily budget gate.

When a burst of ingestion exhausts the per-day extraction/markdown budget
(``extraction_daily_budget_usd`` / ``markdown_daily_budget_usd``), those stages
record an ``extraction_skipped`` / ``markdown_skipped`` event with
``detail->>'reason' = 'budget'`` and the document still reaches ``indexed`` — it
is OCR-searchable but lacks LLM metadata until re-run. These queries surface such
documents so they can be counted (admin visibility) or re-enqueued (the daily
``backfill_budget_skipped`` task, when ``budget_backfill_enabled`` is set). The
budget resets daily, so a re-run on the next day fills them in.

A document counts as *currently* budget-skipped for a stage when the **most
recent** event for that stage is a budget skip — a later ``*_completed`` clears
it, so a document that has since been filled in is not reported.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Per stage, keep only each document's latest event, then keep documents whose
# latest is a budget skip. `id DESC` orders events chronologically (BigInteger
# PK, monotonic). A document qualifies if either stage is budget-skipped.
_BUDGET_SKIPPED_IDS = """
WITH latest_extraction AS (
    SELECT DISTINCT ON (document_id) document_id, event, detail
    FROM ingestion_events
    WHERE event IN ('extraction_completed', 'extraction_skipped', 'extraction_failed')
    ORDER BY document_id, id DESC
),
latest_markdown AS (
    SELECT DISTINCT ON (document_id) document_id, event, detail
    FROM ingestion_events
    WHERE event IN ('markdown_completed', 'markdown_skipped', 'markdown_failed')
    ORDER BY document_id, id DESC
)
SELECT d.id
FROM documents d
WHERE d.deleted_at IS NULL AND (
    d.id IN (
        SELECT document_id FROM latest_extraction
        WHERE event = 'extraction_skipped' AND detail->>'reason' = 'budget'
    )
    OR d.id IN (
        SELECT document_id FROM latest_markdown
        WHERE event = 'markdown_skipped' AND detail->>'reason' = 'budget'
    )
)
"""

_BUDGET_SKIPPED_IDS_SQL = text(_BUDGET_SKIPPED_IDS + " ORDER BY d.id")
_BUDGET_SKIPPED_COUNT_SQL = text(f"SELECT count(*) FROM ({_BUDGET_SKIPPED_IDS}) s")


async def budget_skipped_document_ids(session: AsyncSession) -> list[int]:
    """IDs of non-deleted documents currently budget-skipped for extraction or markdown."""
    return list((await session.execute(_BUDGET_SKIPPED_IDS_SQL)).scalars().all())


async def budget_skipped_count(session: AsyncSession) -> int:
    """Count of non-deleted documents currently budget-skipped (cheap; for admin stats)."""
    return int((await session.execute(_BUDGET_SKIPPED_COUNT_SQL)).scalar_one())
