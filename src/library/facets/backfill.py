"""Which documents a labelling run should touch, and the run itself."""

from __future__ import annotations

import logging

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.facets.apply import label_and_apply
from library.models import Document, DocumentLabel

logger = logging.getLogger(__name__)


async def documents_needing_labels(
    session: AsyncSession, *, relabel: bool, limit: int | None
) -> list[int]:
    """Non-deleted document ids to label, oldest first.

    Without ``relabel``, a document carrying ANY label is skipped, which is what
    makes the command safe to re-run after adding a facet: it picks up only what
    has never been labelled.
    """
    has_label = exists().where(DocumentLabel.document_id == Document.id)
    statement = select(Document.id).where(Document.deleted_at.is_(None)).order_by(Document.id)
    if not relabel:
        statement = statement.where(~has_label)
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.execute(statement)).scalars())


async def run_backfill(
    session: AsyncSession, settings: Settings, *, relabel: bool, limit: int | None
) -> tuple[int, int]:
    """Label each selected document. Returns ``(labelled, skipped)``.

    Each document commits on its own so a failure part-way leaves the work
    already done in place; the command is re-runnable by design.

    Each document is also labelled inside a SAVEPOINT, which is what makes that
    promise hold for a *database* failure. A statement-level Postgres error
    aborts the whole transaction, so without it one bad document would take the
    rest of the run with it — every later commit would raise
    InFailedSqlTransaction. Rolling back to the savepoint discards only that
    document's writes; it is counted as skipped and the run continues.
    """
    ids = await documents_needing_labels(session, relabel=relabel, limit=limit)
    labelled = skipped = 0
    for document_id in ids:
        try:
            async with session.begin_nested():
                outcome = await label_and_apply(session, settings, document_id)
        except Exception:  # one document must never abort the archive run
            logger.exception("facet labelling failed for document %s", document_id)
            skipped += 1
            continue
        if outcome is None:
            skipped += 1
            continue
        await session.commit()
        labelled += 1
    return labelled, skipped
