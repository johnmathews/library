"""Standalone LLM "business matter" classifier pass.

A matter is an evergreen subject category (car insurance, health insurance,
subscriptions) a document may belong to any number of — see
:class:`library.models.Matter` and :mod:`library.matters`. This module owns one
Anthropic structured-output call that auto-files a document into 0..n *existing*
matters, chosen from the user-curated vocabulary (each matter's ``hint`` guides
the choice). It never invents matters; it only attaches ones already offered.

Two disciplines, mirroring the extraction/email-label passes:

1. **Merge-only, never destructive.** A returned slug is attached only if it is
   in the offered vocabulary AND not already on the document. Existing matters
   are never removed. A document with ``"matters"`` in
   ``extra["user_edited_fields"]`` is skipped entirely — a manual curation of
   the matter set is never overwritten.
2. **Fail-open.** A disabled/blown budget, an empty vocabulary, an API error, or
   a malformed response all end in a normal return with no write — the pass can
   only *add* matters, never fail a document or raise into the caller.

Spend is budget-gated exactly like extraction: today's
``matter_classification_completed`` spend is summed and compared to
``matter_classification_daily_budget_usd`` before the call. This module does not
commit — the caller (the classification job, W4) owns the transaction.
"""

import logging

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import todays_spend_usd
from library.extraction.extractor import estimate_cost_usd
from library.matters import MatterCount, list_matters
from library.models import Document, IngestionEvent, Matter, Sender

logger = logging.getLogger(__name__)

#: Bump when the prompt or schema changes so stored provenance stays interpretable.
PROMPT_VERSION = "matter-classifier-v2"

#: The completion event whose daily total gates this pass.
CLASSIFICATION_EVENT = "matter_classification_completed"

_MAX_OUTPUT_TOKENS = 512

SYSTEM_PROMPT = """You file a personal document into its "business matters" — \
evergreen life/business categories the user curates (e.g. car insurance, health \
insurance, subscriptions). You are given the document's title, summary, and \
sender, plus a numbered list of the matters that exist, each shown as \
`slug: name — hint`.

Return the slugs of the matters that CLEARLY apply to this document.

Rules:
- Match on the document's PRIMARY subject — what the document actually IS — not \
on topics it merely mentions in passing. A parking fine, a repair invoice, or a \
purchase receipt that happens to mention a car is NOT "car insurance"; it is a \
fine, a service record, or a purchase. Incidental mentions never justify a match.
- The hint is authoritative. Respect its inclusions AND its exclusions: if a \
hint says a matter excludes something, never file that something there even when \
the topic is related.
- Prefer PRECISION over recall. When you are unsure whether a matter truly \
applies, LEAVE IT OUT. Return an empty list when none of the offered matters \
clearly apply — a wrong match is worse than none, and an unfiled document is \
easily found later.
- Return ONLY slugs that appear verbatim in the offered list. Never invent a \
slug or return one that is not listed.
- A document may genuinely belong to SEVERAL matters; return every one that \
clearly and primarily applies — but do not pad the list to be safe.
- Judge by the title, summary, and sender against each matter's name and hint. \
The sender is a strong signal: an insurer implies insurance, a garage implies \
servicing, a retailer implies a purchase."""


class MatterClassificationResult(BaseModel):
    """Structured output: the subset of offered slugs that apply (may be empty)."""

    model_config = ConfigDict(extra="forbid")

    matched_slugs: list[str]


def _vocabulary_block(matters: list[MatterCount]) -> str:
    lines = []
    for matter in matters:
        hint = f" — {matter.hint}" if matter.hint else ""
        lines.append(f"{matter.slug}: {matter.name}{hint}")
    return "\n".join(lines)


async def apply_matter_classification(
    session: AsyncSession,
    document: Document,
    settings: Settings,
    client: AsyncAnthropic | None = None,
    *,
    replace: bool = False,
) -> None:
    """Auto-file ``document`` into 0..n existing matters via one LLM call.

    Fail-open: any skip or error returns without writing. Does NOT commit — the
    caller owns the transaction. Provenance is stamped onto
    ``document.extra["matter_classification"]``.

    Two modes:

    - **merge** (default): append predicted matters not already attached; never
      remove. This is the ingest default so a re-run can only add, never undo.
    - **replace** (``replace=True``): re-file from scratch — set the document's
      matters to exactly the prediction. Used by ``sweep-matters --reclassify``
      after the vocabulary/hints improve, to correct earlier auto-filing. Safe
      because a user-edited document is skipped *before* either mode runs, so
      replace only ever touches auto-assigned memberships, never hand-curated
      ones.

    ``client`` may be injected (tests); when None a client is constructed from
    ``settings.anthropic_api_key``.
    """
    if "matters" in set(document.extra.get("user_edited_fields", [])):
        logger.info("matter-classify: document %s has user-edited matters; skipping", document.id)
        return

    vocabulary = await list_matters(session, include_archived=False)
    if not vocabulary:
        logger.info("matter-classify: no matters defined; skipping document %s", document.id)
        return

    try:
        spend = await todays_spend_usd(session, CLASSIFICATION_EVENT)
        if spend >= settings.matter_classification_daily_budget_usd:
            logger.info(
                "matter-classify: daily budget $%.2f reached ($%.4f spent); skipping document %s",
                settings.matter_classification_daily_budget_usd,
                spend,
                document.id,
            )
            return

        sender_name: str | None = None
        if document.sender_id is not None:
            sender = await session.get(Sender, document.sender_id)
            if sender is not None:
                sender_name = sender.name

        user_content = (
            f"Title: {document.title or '(none)'}\n"
            f"Summary: {document.summary or '(none)'}\n"
            f"Sender: {sender_name or '(unknown)'}\n\n"
            f"Offered matters:\n{_vocabulary_block(vocabulary)}"
        )

        owns_client = client is None
        if client is None:
            if settings.anthropic_api_key is None:
                logger.info("matter-classify: no API key; skipping document %s", document.id)
                return
            client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

        try:
            response = await client.messages.parse(
                model=settings.matter_classifier_model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_format=MatterClassificationResult,
            )
        finally:
            if owns_client:
                await client.close()

        parsed = response.parsed_output
        if parsed is None:
            logger.warning(
                "matter-classify: no parseable output; leaving document %s unchanged", document.id
            )
            return
    except Exception:
        logger.exception(
            "matter-classify: classification failed; leaving document %s unchanged", document.id
        )
        return

    by_slug: dict[str, Matter] = {}
    for matter_count in vocabulary:
        by_slug[matter_count.slug] = await session.get(Matter, matter_count.id)  # type: ignore[assignment]

    # Predicted matters: dedup, keep only ones in the offered vocabulary.
    predicted: list[Matter] = []
    seen: set[str] = set()
    for slug in parsed.matched_slugs:
        matter = by_slug.get(slug)
        if matter is None or slug in seen:
            continue  # unknown/hallucinated or duplicate slug — ignore
        predicted.append(matter)
        seen.add(slug)

    before = {matter.slug for matter in document.matters}
    if replace:
        # Re-file from scratch. The whole current set is auto-assigned (a
        # user-edited document returned early above), so replace it wholesale.
        document.matters = predicted
        added = [slug for slug in seen if slug not in before]
        removed = sorted(before - seen)
    else:
        added = []
        for matter in predicted:
            if matter.slug in before:
                continue  # already attached — merge leaves it be
            document.matters.append(matter)
            added.append(matter.slug)
        removed = []

    cost_usd = estimate_cost_usd(
        settings.matter_classifier_model,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    provenance = {
        "model": settings.matter_classifier_model,
        "prompt_version": PROMPT_VERSION,
        "mode": "replace" if replace else "merge",
        "cost_usd": cost_usd,
        "matched_slugs": list(parsed.matched_slugs),
        "attached_slugs": added,
        "removed_slugs": removed,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    document.extra = {**document.extra, "matter_classification": provenance}
    # Record the spend event so ``todays_spend_usd(CLASSIFICATION_EVENT)`` can
    # accrue the daily budget — the gate above reads these events, so without
    # this the budget would never accumulate.
    session.add(
        IngestionEvent(
            document_id=document.id,
            event=CLASSIFICATION_EVENT,
            detail=provenance,
        )
    )
