"""The only module that both calls the model and writes to the database.

Three outcomes per facet, and the split between them is the point: a confident
in-vocabulary value is applied; anything below the confidence floor is withheld
and reported as unknown; a value the model wanted but the vocabulary lacks is
queued as a suggestion. Nothing here can create a facet value.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.facets.labeller import DocumentFields, LabelProposal, label_document
from library.facets.vocabulary import (
    UnknownValueError,
    load_vocabulary,
    set_document_label,
)
from library.models import Document, Facet, FacetValueSuggestion, Kind, Sender

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabellingOutcome:
    document_id: int
    applied: dict[str, str]
    unknown: tuple[str, ...]
    suggested: tuple[tuple[str, str], ...]


async def apply_proposals(
    session: AsyncSession,
    document_id: int,
    proposals: Sequence[LabelProposal],
    *,
    min_confidence: float,
) -> LabellingOutcome:
    applied: dict[str, str] = {}
    unknown: list[str] = []
    suggested: list[tuple[str, str]] = []

    for proposal in proposals:
        if proposal.suggested_label:
            facet_id = (
                await session.execute(select(Facet.id).where(Facet.key == proposal.facet_key))
            ).scalar_one_or_none()
            if facet_id is not None:
                await session.execute(
                    pg_insert(FacetValueSuggestion)
                    .values(
                        facet_id=facet_id,
                        document_id=document_id,
                        suggested_label=proposal.suggested_label,
                        reason=proposal.reason,
                        state="pending",
                    )
                    .on_conflict_do_nothing(constraint="facet_value_suggestions_unique")
                )
                suggested.append((proposal.facet_key, proposal.suggested_label))

        if proposal.value_key is None or proposal.confidence < min_confidence:
            unknown.append(proposal.facet_key)
            continue
        try:
            await set_document_label(session, document_id, proposal.facet_key, proposal.value_key)
        except UnknownValueError:
            # The vocabulary changed between parsing and writing. Treat as
            # unknown rather than failing the run.
            unknown.append(proposal.facet_key)
            continue
        applied[proposal.facet_key] = proposal.value_key

    return LabellingOutcome(
        document_id=document_id,
        applied=applied,
        unknown=tuple(unknown),
        suggested=tuple(suggested),
    )


async def document_fields(session: AsyncSession, document_id: int) -> DocumentFields | None:
    """The facts the labeller may see. None when the document does not exist."""
    row = (
        await session.execute(
            select(
                Document.title,
                Document.summary,
                Sender.name,
                Kind.slug,
                Document.amount_total,
                Document.currency,
                Document.ocr_text,
            )
            .outerjoin(Sender, Sender.id == Document.sender_id)
            .outerjoin(Kind, Kind.id == Document.kind_id)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        return None
    title, summary, sender, kind, amount, currency, ocr_text = row
    return DocumentFields(
        title=title,
        summary=summary,
        sender=sender,
        kind=kind,
        amount=str(amount) if amount is not None else None,
        currency=currency,
        excerpt=ocr_text,
    )


async def label_and_apply(
    session: AsyncSession,
    settings: Settings,
    document_id: int,
    *,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> LabellingOutcome | None:
    """Label one document end to end. None when the document or the model is absent."""
    fields = await document_fields(session, document_id)
    if fields is None:
        return None
    vocabulary = await load_vocabulary(session)
    result = await label_document(settings, vocabulary, fields, client=client, backend=backend)
    if result is None:
        return None
    proposals, _input_tokens, _output_tokens = result
    return await apply_proposals(
        session,
        document_id,
        proposals,
        min_confidence=settings.facet_label_min_confidence,
    )
