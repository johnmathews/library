"""Authored-series signature matching and auto-continue (propose-for-review).

Sits on top of an authored series' mechanical *signature* (its dominant
``(sender_id, kind_id, currency)`` triple — see ``library.series.derive_signature``):

- **Auto-continue (PROPOSE-FOR-REVIEW).** When a newly-indexed document matches
  an authored series' signature, ``propose_authored_matches`` records it as a
  ``pending`` :class:`~library.models.AuthoredSeriesSuggestion` for the owner to
  review. It is NEVER silently added as a member — the owner accepts or dismisses
  it via the API.

The related **odd-one-out** rationale lives in ``library.series.odd_ones_out``
and is deterministic (built from the documents' real sender/kind/currency), not
LLM-generated: an LLM asked to phrase it once hallucinated a sender name that
appeared in none of the documents, so this path deliberately uses no LLM.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.models import (
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    SuggestionState,
)
from library.series import load_authored_signature

logger = logging.getLogger(__name__)


async def _candidate_series_ids(session: AsyncSession, document: Document) -> list[int]:
    """Authored series with ≥1 member sharing this document's (sender, kind).

    A cheap pre-filter: only these series can possibly have a signature the
    document matches, so we compute the (more expensive) full signature for just
    them rather than every authored series.
    """
    statement = (
        select(AuthoredSeriesMember.authored_series_id)
        .join(Document, Document.id == AuthoredSeriesMember.document_id)
        .where(
            Document.sender_id == document.sender_id,
            Document.kind_id == document.kind_id,
        )
        .distinct()
    )
    return list((await session.execute(statement)).scalars().all())


async def _has_membership(session: AsyncSession, authored_series_id: int, document_id: int) -> bool:
    exists = (
        await session.execute(
            select(AuthoredSeriesMember.id).where(
                AuthoredSeriesMember.authored_series_id == authored_series_id,
                AuthoredSeriesMember.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    return exists is not None


async def _has_suggestion(session: AsyncSession, authored_series_id: int, document_id: int) -> bool:
    """True when any suggestion row (pending OR dismissed) already exists."""
    exists = (
        await session.execute(
            select(AuthoredSeriesSuggestion.id).where(
                AuthoredSeriesSuggestion.authored_series_id == authored_series_id,
                AuthoredSeriesSuggestion.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    return exists is not None


async def propose_authored_matches(
    session: AsyncSession, settings: Settings, document_id: int
) -> None:
    """Record ``document_id`` as a pending suggestion for every authored series it matches.

    PROPOSE-FOR-REVIEW: this NEVER adds an ``AuthoredSeriesMember`` and NEVER
    calls the LLM (a mechanical match needs no rationale). Best-effort and
    idempotent — a document already a member or already carrying a suggestion row
    (pending or dismissed, the latter a tombstone) is skipped, and the insert is
    conflict-guarded on the unique ``(series, document)`` key.

    Skips entirely when auto-continue is disabled, the document is deleted, has no
    amount, or lacks a resolved sender/kind (nothing to match on).
    """
    if not settings.series_autocontinue_enabled:
        return
    document = await session.get(Document, document_id)
    if (
        document is None
        or document.deleted_at is not None
        or document.amount_total is None
        or document.sender_id is None
        or document.kind_id is None
    ):
        return

    for authored_series_id in await _candidate_series_ids(session, document):
        signature = await load_authored_signature(session, authored_series_id)
        if (
            signature is None
            or signature.dominance < settings.series_autocontinue_min_dominance
            or signature.sender_id != document.sender_id
            or signature.kind_id != document.kind_id
            or signature.currency != document.currency
        ):
            continue
        if await _has_membership(session, authored_series_id, document_id):
            continue
        if await _has_suggestion(session, authored_series_id, document_id):
            continue
        statement = (
            pg_insert(AuthoredSeriesSuggestion)
            .values(
                authored_series_id=authored_series_id,
                document_id=document_id,
                state=SuggestionState.PENDING.value,
                signature_sender_id=signature.sender_id,
                signature_kind_id=signature.kind_id,
                signature_currency=signature.currency,
            )
            .on_conflict_do_nothing(constraint="authored_series_suggestions_series_document")
        )
        await session.execute(statement)
        await session.commit()
        logger.info(
            "proposed document %s for authored series %s (signature match)",
            document_id,
            authored_series_id,
        )
