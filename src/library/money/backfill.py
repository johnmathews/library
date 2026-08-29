"""Decide amount semantics for documents extracted before the field existed.

A separate, cheap call rather than a full re-extraction: only two fields are
missing, and re-running extraction would also overwrite titles, summaries and
senders that a human may since have corrected.
"""

from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.extraction.schema import MAX_REFERENCE_CHARS, normalize_amount_kind
from library.llm import subscription
from library.llm.envelope import strip_json_envelope
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


class AmountClassification(BaseModel):
    """Structured-output schema for the amount classifier.

    Deliberately permissive: ``amount_kind`` is a bare string rather than an
    enum so a model naming a value outside the vocabulary still returns
    *something* and :func:`normalize_amount_kind` decides, exactly as it does
    for the free-text backend. Constraining it here would turn an unrecognised
    kind into a schema failure and lose the reference alongside it.
    """

    amount_kind: str | None = None
    reference: str | None = None


class AmountParseError(Exception):
    """The amount classifier's structured-output call returned nothing parseable."""


def _parse(payload: str) -> tuple[str | None, str | None]:
    """Map a raw model payload onto ``(amount_kind, reference)``.

    Never raises: an unparseable payload yields ``(None, None)``, which leaves
    the document undecided and still in the backfill queue rather than failing
    the whole run. Tolerates a markdown fence or surrounding prose — see
    :func:`~library.llm.envelope.strip_json_envelope` — which is belt-and-braces
    alongside the structured-output call in :func:`classify_amount` and the only
    protection the subscription backend (free text, no ``messages.parse``) gets.
    """
    try:
        parsed = json.loads(strip_json_envelope(payload))
    except json.JSONDecodeError:
        logger.warning("amount classifier returned unparseable JSON")
        return None, None
    reference = parsed.get("reference")
    stripped = str(reference).strip() or None if reference else None
    # Clamped here too (schema.py clamps the ingest path already): this
    # function parses raw model JSON directly rather than going through
    # ExtractedMetadata, so it must not rely on that other path staying in
    # sync. Document.reference is String(128); an unclamped value raises a
    # DataError at commit rather than a validation error here.
    clamped = stripped[:MAX_REFERENCE_CHARS] if stripped else stripped
    return normalize_amount_kind(parsed.get("amount_kind")), clamped


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

    The API backend uses ``client.messages.parse()`` with
    :class:`AmountClassification`, not a free-text call the caller parses. The
    system prompt asks for bare JSON, but asking is not enough: the first live
    run of this backfill logged "amount classifier returned unparseable JSON"
    for every one of five documents, because the model wrapped its otherwise
    correct JSON in a ```` ```json ```` fence. The facet labeller had already
    hit the identical failure (GH #108) — this is the same fix. The
    subscription backend returns free text and cannot use ``parse()``, so it
    still goes through :func:`_parse` directly, which is why that function
    strips an envelope before decoding.
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
        response = await anthropic.messages.parse(
            model=settings.extraction_model,
            max_tokens=MAX_AMOUNT_TOKENS,
            system=AMOUNT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=AmountClassification,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise AmountParseError(f"{settings.extraction_model} returned no parseable output")
        kind_value, reference = _parse(parsed.model_dump_json())
        return kind_value, reference, response.usage.input_tokens, response.usage.output_tokens

    if client is not None:
        return await _call(client)
    if settings.anthropic_api_key is None:
        return None
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as owned:
        return await _call(owned)


async def _classify_one(session: AsyncSession, settings: Settings, document_id: int) -> str | None:
    """Classify one document. Returns ``"classified"``, ``"empty"``, or None.

    None covers every way a document turns out not to be classifiable at
    all: its row vanished between selection and lookup, the classification
    call could not even be attempted (no client and no API key configured),
    or it vanished again between the call returning and the write. This
    function does not catch exceptions itself — a network error from
    ``classify_amount`` or a database error from writing the row is left to
    propagate to the caller's savepoint guard.
    """
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
        return None
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
        return None
    kind_value, reference, _in_tokens, _out_tokens = result
    if kind_value is None:
        # Left NULL deliberately: not summable, and still in the queue.
        return "empty"
    document = await session.get(Document, document_id)
    if document is None:
        return None
    document.amount_kind = AmountKind(kind_value)
    if reference and not document.reference:
        document.reference = reference
    return "classified"


async def run_amount_backfill(
    session: AsyncSession, settings: Settings, *, limit: int | None
) -> tuple[int, int, int]:
    """Classify each selected document. Returns ``(classified, empty, skipped)``.

    Commits per document so a part-way failure keeps the work already done.

    Each document is also classified inside a SAVEPOINT, which is what makes
    that promise hold against both a network failure (``classify_amount``
    calls the Anthropic API live, and any ``APIError``/``RateLimitError``/
    timeout would otherwise propagate straight out of the loop and abort the
    whole run — every amount-bearing document that still has no
    ``amount_kind``, which is what ``documents_needing_amount_kind`` selects)
    and a database failure (an over-long
    ``reference`` raising ``DataError`` at flush — belt-and-braces here,
    since ``reference`` is also clamped where it is created, in
    ``_parse`` and in ``ExtractedMetadata``). Rolling back to the savepoint
    discards only that document's writes; it is counted as skipped and the
    run continues.

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
    vanished between selection and lookup, the classification call could not
    even be attempted (no client and no API key configured), or classifying
    it raised — most likely a network error talking to the model.
    """
    ids = await documents_needing_amount_kind(session, limit=limit)
    classified = empty = skipped = 0
    for document_id in ids:
        try:
            async with session.begin_nested():
                outcome = await _classify_one(session, settings, document_id)
        except Exception:  # one document must never abort the archive run
            logger.exception("amount classification failed for document %s", document_id)
            skipped += 1
            continue
        if outcome is None:
            skipped += 1
            continue
        await session.commit()
        if outcome == "empty":
            empty += 1
            continue
        classified += 1
    return classified, empty, skipped
