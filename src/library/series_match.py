"""Authored-series signature matching, odd-one-out rationale, and auto-continue.

Two related, deliberately-conservative features sit on top of an authored
series' mechanical *signature* (its dominant ``(sender_id, kind_id, currency)``
triple — see ``library.series.derive_signature``):

- **Auto-continue (PROPOSE-FOR-REVIEW).** When a newly-indexed document matches
  an authored series' signature, ``propose_authored_matches`` records it as a
  ``pending`` :class:`~library.models.AuthoredSeriesSuggestion` for the owner to
  review. It is NEVER silently added as a member — the owner accepts or dismisses
  it via the API.

- **Odd-one-out rationale.** The matching itself is purely mechanical; the LLM is
  used ONLY to write a one-sentence explanation of *why* a member that breaks the
  signature (a different sender / kind / currency) is unlike the rest. That call
  (``generate_reason``) mirrors ``series_insight.generate_description``: best-
  effort, capped tokens, text extracted from ``type=="text"`` blocks.
"""

import logging

from anthropic import AsyncAnthropic
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
from library.series import SeriesSignature, _Member, load_authored_signature

logger = logging.getLogger(__name__)

# A single explanatory sentence needs very little room; this also caps spend.
MAX_REASON_TOKENS: int = 60

REASON_SYSTEM_PROMPT: str = """\
You explain, in ONE short sentence, why a single document does not fit a
recurring series of household documents in "Library", a self-hosted family
document archive.

You are told the series' dominant identity (its usual sender, document kind, and
currency), the one document that breaks it, and which axis differs: a different
sender, a different document kind, or a different currency.

Write exactly ONE plain-English sentence naming why this document is unlike the
rest of the series. Do NOT add a preamble, a heading, bullet points, or advice —
return only the single sentence."""


def build_reason_prompt(
    signature: SeriesSignature, candidate: _Member, mechanical_axis: str
) -> str:
    """Render the odd-one-out contrast into the user prompt for ``generate_reason``."""
    lines = [
        "The series is predominantly: "
        f"sender_id={signature.sender_id}, kind_id={signature.kind_id}, "
        f"currency={signature.currency}.",
        "The document that does not fit:",
        f"- Title: {candidate.title or 'untitled'}",
        f"- Sender: {candidate.sender}",
        f"- Document kind: {candidate.kind}",
        f"- Currency: {candidate.currency}",
        f"It differs from the rest of the series on this axis: {mechanical_axis}.",
    ]
    return "\n".join(lines)


async def generate_reason(
    client: AsyncAnthropic,
    model: str,
    *,
    signature: SeriesSignature,
    candidate: _Member,
    mechanical_axis: str,
) -> tuple[str, int, int]:
    """Call the LLM once; return ``(reason, input_tokens, output_tokens)``."""
    response = await client.messages.create(
        model=model,
        max_tokens=MAX_REASON_TOKENS,
        system=REASON_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_reason_prompt(signature, candidate, mechanical_axis)}
        ],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    return text, response.usage.input_tokens, response.usage.output_tokens


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
