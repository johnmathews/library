"""Decide amount semantics for documents extracted before the field existed.

A separate, cheap call rather than a full re-extraction: only two fields are
missing, and re-running extraction would also overwrite titles, summaries and
senders that a human may since have corrected.
"""

from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.extraction.schema import normalize_amount_kind
from library.llm import subscription
from library.models import AmountKind, Document, Kind, Sender

logger = logging.getLogger(__name__)

MAX_AMOUNT_TOKENS: int = 200
MAX_EXCERPT_CHARS: int = 2000

AMOUNT_SYSTEM_PROMPT: str = """\
You decide what a single number on a household document MEANS. You are not
asked how much it is.

Answer with one of exactly these values:
  payment_due     an invoice or bill the reader owes
  payment_made    a receipt or confirmation that money was paid
  assessment      a tax or levy demand
  coverage_limit  an insurance sum insured or maximum payout — NOT money paid
  balance         an account or statement position
  estimate        a quote or indicative price, not yet owed
  none            the amount is incidental, or zero because nothing is due

Also return the document's own invoice / order / booking / assessment number
exactly as printed, or null if it shows none.

If you are unsure of the kind, return null. An unsure answer leaves the amount
out of every total, which is safe; a confident wrong answer corrupts them.

Return ONLY this JSON, no prose or code fences:
{"amount_kind": "..."|null, "reference": "..."|null}"""


async def documents_needing_amount_kind(session: AsyncSession, *, limit: int | None) -> list[int]:
    """Amount-bearing, non-deleted documents with no ``amount_kind`` yet.

    A document with no amount has no semantics to decide, and one that already
    has a kind may have been corrected by hand — neither is re-decided.
    """
    statement = (
        select(Document.id)
        .where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.amount_kind.is_(None),
        )
        .order_by(Document.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.execute(statement)).scalars())


def _parse(payload: str) -> tuple[str | None, str | None]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("amount classifier returned unparseable JSON")
        return None, None
    reference = parsed.get("reference")
    return (
        normalize_amount_kind(parsed.get("amount_kind")),
        str(reference).strip() or None if reference else None,
    )


async def classify_amount(
    settings: Settings,
    *,
    title: str | None,
    sender: str | None,
    kind: str | None,
    amount: str | None,
    currency: str | None,
    excerpt: str | None,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> tuple[str | None, str | None, int, int] | None:
    """``(amount_kind, reference, in_tokens, out_tokens)``, or None if unrunnable.

    ``None`` means the call could not even be attempted (no API key configured
    and no client supplied). A completed call that could not classify the
    amount still returns a tuple — with ``amount_kind`` set to ``None`` — so
    callers can tell "did not run" apart from "ran but stayed unsure".
    """
    prompt = "\n".join(
        [
            f"Sender: {sender}",
            f"Document kind: {kind}",
            f"Title: {title}",
            f"Amount: {amount} {currency}",
            f"Text excerpt: {(excerpt or '')[:MAX_EXCERPT_CHARS]}",
        ]
    )

    if backend == "subscription":
        result = await subscription.text_call(
            config_dir=settings.claude_config_dir,
            model=settings.extraction_model,
            system_prompt=AMOUNT_SYSTEM_PROMPT,
            prompt=prompt,
        )
        kind_value, reference = _parse(result.text)
        return kind_value, reference, result.usage.input_tokens, result.usage.output_tokens

    async def _call(anthropic: AsyncAnthropic) -> tuple[str | None, str | None, int, int]:
        response = await anthropic.messages.create(
            model=settings.extraction_model,
            max_tokens=MAX_AMOUNT_TOKENS,
            system=AMOUNT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        kind_value, reference = _parse(text)
        return kind_value, reference, response.usage.input_tokens, response.usage.output_tokens

    if client is not None:
        return await _call(client)
    if settings.anthropic_api_key is None:
        return None
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as owned:
        return await _call(owned)


async def run_amount_backfill(
    session: AsyncSession, settings: Settings, *, limit: int | None
) -> tuple[int, int, int]:
    """Classify each selected document. Returns ``(classified, empty, skipped)``.

    Commits per document so a part-way failure keeps the work already done.

    ``classified`` counts a document the model gave a usable kind for, and
    which was written to ``amount_kind``.

    ``empty`` counts a document whose classification call *completed* but
    could not decide a kind — the model returned ``none``/an unrecognised
    value, or the response was unparseable JSON. The column is deliberately
    left NULL (NULL means not-yet-decided and is treated as not-summable
    downstream), and the document stays in the queue for a future run. This
    must never be folded into ``classified``: an earlier backfill on this
    project reported "labelled 5, skipped 0" while applying zero labels,
    because it counted a model response as done whenever it returned
    anything at all.

    ``skipped`` counts a document that could not be classified at all: it
    vanished between selection and lookup, or the classification call could
    not even be attempted (no client and no API key configured).
    """
    ids = await documents_needing_amount_kind(session, limit=limit)
    classified = empty = skipped = 0
    for document_id in ids:
        row = (
            await session.execute(
                select(
                    Document.title,
                    Sender.name,
                    Kind.slug,
                    Document.amount_total,
                    Document.currency,
                    Document.ocr_text,
                )
                .outerjoin(Sender, Sender.id == Document.sender_id)
                .outerjoin(Kind, Kind.id == Document.kind_id)
                .where(Document.id == document_id)
            )
        ).one_or_none()
        if row is None:
            skipped += 1
            continue
        title, sender, kind, amount, currency, excerpt = row
        result = await classify_amount(
            settings,
            title=title,
            sender=sender,
            kind=kind,
            amount=str(amount) if amount is not None else None,
            currency=currency,
            excerpt=excerpt,
        )
        if result is None:
            skipped += 1
            continue
        kind_value, reference, _in_tokens, _out_tokens = result
        if kind_value is None:
            # Left NULL deliberately: not summable, and still in the queue.
            empty += 1
            continue
        document = await session.get(Document, document_id)
        if document is None:
            skipped += 1
            continue
        document.amount_kind = AmountKind(kind_value)
        if reference and not document.reference:
            document.reference = reference
        await session.commit()
        classified += 1
    return classified, empty, skipped
