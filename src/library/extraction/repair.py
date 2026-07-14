"""Fill-only metadata repair over the vision markdown layer.

A narrow second chance for the doc-150 failure shape: extraction on a scanned
receipt left ``document_date`` NULL and/or the sender a generic category word
("Restaurant"), while the markdown stage's vision output (``pages_markdown``)
plainly contains the real merchant name and printed date. This pass runs at
the tail of the markdown stage (see ``library.jobs.run_markdown`` /
``markdown_document``) and asks ``settings.extraction_model`` to read ONLY the
repairable fields off that markdown.

The safety property — fill-only, never re-extraction:

- Scalars (``document_date``, ``amount_total``, ``currency``) are written only
  when currently NULL and not user-edited.
- The sender is filled when unset; an existing sender is replaced only when
  its name is itself a generic category word (``_GENERIC_SENDER_NAMES``,
  full-string casefold) and the repair produced a non-generic name. A
  non-generic or user-edited sender is never touched, a generic repair result
  is never written, and the old generic sender row is never deleted.

Same invariant as extraction and markdown: **repair never fails a document.**
Every outcome ends in an ``extraction_repair_completed`` /
``extraction_repair_skipped`` audit event and a normal return. Repair spend
counts toward the EXTRACTION daily budget (see
:data:`library.extraction.apply.EXTRACTION_SPEND_EVENTS`).
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import (
    revalidate_document,
    todays_spend_usd,
    upsert_sender,
)
from library.extraction.extractor import (
    ExtractionParseError,
    _sample_long_text,
    estimate_cost_usd,
)
from library.extraction.schema import (
    _coerce_date,
    normalize_amount_string,
    normalize_currency_code,
)
from library.extraction.validation import _GENERIC_SENDER_NAMES, derive_review_status
from library.models import Document, IngestionEvent, Sender

logger = logging.getLogger(__name__)

# Bump whenever the repair prompt or schema changes meaningfully. Stored in
# extra["extraction_repair"]; a document already stamped with the CURRENT
# version is skipped (reason "already_repaired") so backfills never re-spend.
REPAIR_PROMPT_VERSION: str = "2026-07-14.1"

# The validation findings a repair can plausibly fix; anything else is not a
# reason to spend on a second call.
REPAIRABLE_RULES: frozenset[str] = frozenset({"missing_date", "missing_sender", "generic_sender"})

# The repair output is four small fields; no room needed for prose.
MAX_OUTPUT_TOKENS: int = 512

SYSTEM_PROMPT: str = """\
You repair missing metadata for "Library", a self-hosted family document
archive (Dutch, English, or mixed household paperwork). The input is a
MARKDOWN RENDERING of a scanned document (typically a till receipt or
invoice), produced by a vision model reading the page images — it is not raw
OCR, but it may still contain transcription mistakes.

Fill ONLY values that are clearly printed in the text:
- sender_name: the merchant or organisation name as PRINTED on the document
  (on a till receipt: the shop name in the header), in short canonical form.
  NEVER a generic category word such as "Restaurant", "Shop", "Winkel", or
  "Supermarkt" — a category is not a name. If no name is printed legibly,
  return null.
- document_date: the document's own issue/print date as ISO YYYY-MM-DD.
  null when no date is printed.
- amount_total: the document's main total as a plain decimal string, e.g.
  "12.50", with currency as an ISO 4217 code, e.g. "EUR". Both null when not
  printed.
- Do NOT guess, infer, or reconstruct values that are not plainly printed —
  return null for anything you are unsure about.
- confidence: "low" when the rendering is garbled or you had to judge,
  "high" only when the values are plainly printed.
"""


class RepairMetadata(BaseModel):
    """The narrow structured output of one repair call.

    Deliberately NO kind, title, or summary — repair fills gaps in an
    extraction that already ran; it never re-describes the document.
    """

    model_config = ConfigDict(extra="forbid")

    sender_name: str | None = Field(
        description=(
            "The merchant or organisation name as printed on the document, in "
            "short canonical form. Never a generic category word such as "
            "'Restaurant' or 'Shop'. null when no name is printed legibly."
        )
    )
    document_date: Annotated[
        date | None,
        BeforeValidator(_coerce_date),
        Field(description="The document's own printed issue date. null when not printed."),
    ]
    amount_total: str | None
    currency: str | None
    confidence: Literal["high", "low"]

    @field_validator("sender_name", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value.strip() if value else value

    @field_validator("amount_total", mode="after")
    @classmethod
    def _normalize_amount(cls, value: str | None) -> str | None:
        return normalize_amount_string(value)

    @field_validator("currency", mode="after")
    @classmethod
    def _normalize_currency(cls, value: str | None) -> str | None:
        return normalize_currency_code(value)


@dataclass(frozen=True)
class RepairOutcome:
    """A successful repair call plus its provenance and cost."""

    metadata: RepairMetadata
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


async def repair_extract(
    document: Document, *, client: AsyncAnthropic, settings: Settings
) -> RepairOutcome:
    """One structured-output call over ``document.pages_markdown``.

    No escalation — repair is a bounded, single-call second chance on the
    primary extraction model. Parse/validation failures raise (the caller
    records a skip); API errors propagate likewise.
    """
    text = _sample_long_text((document.pages_markdown or "").strip())
    response = await client.messages.parse(
        model=settings.extraction_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        output_format=RepairMetadata,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ExtractionParseError(f"{settings.extraction_model} returned no parseable output")
    return RepairOutcome(
        metadata=parsed,
        model=settings.extraction_model,
        prompt_version=REPAIR_PROMPT_VERSION,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=estimate_cost_usd(
            settings.extraction_model, response.usage.input_tokens, response.usage.output_tokens
        ),
    )


def _is_generic(name: str) -> bool:
    """True when a sender name is a bare category word (full-string, casefold)."""
    return name.strip().casefold() in _GENERIC_SENDER_NAMES


async def _record_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, Any]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def _apply_repair(
    session: AsyncSession, document: Document, outcome: RepairOutcome
) -> list[str]:
    """Write the repaired values onto the document; return the fields filled.

    Implements the fill-only safety property described in the module
    docstring, and stamps ``extra["extraction_repair"]`` provenance (also the
    idempotency marker) whether or not anything was filled.
    """
    metadata = outcome.metadata
    user_edited = set(document.extra.get("user_edited_fields", []))
    fields_filled: list[str] = []

    scalar_values: dict[str, object | None] = {
        "document_date": metadata.document_date,
        "amount_total": Decimal(metadata.amount_total) if metadata.amount_total else None,
        "currency": metadata.currency,
    }
    for field, value in scalar_values.items():
        if value is not None and field not in user_edited and getattr(document, field) is None:
            setattr(document, field, value)
            fields_filled.append(field)

    # Sender: fill when unset; replace only a generic-named sender with a
    # non-generic repair result. A generic repair result is never written.
    name = metadata.sender_name
    if name is not None and not _is_generic(name) and "sender_id" not in user_edited:
        if document.sender_id is None:
            document.sender_id = (await upsert_sender(session, name)).id
            fields_filled.append("sender_id")
        else:
            # Resolve via session.get, never the lazy relationship (which
            # raises MissingGreenlet on an expired async instance).
            current = await session.get(Sender, document.sender_id)
            if current is not None and _is_generic(current.name):
                # Point at the real merchant; the old generic row stays.
                document.sender_id = (await upsert_sender(session, name)).id
                fields_filled.append("sender_id")

    document.extra = {
        **document.extra,
        "extraction_repair": {
            "prompt_version": outcome.prompt_version,
            "model": outcome.model,
            "input": "markdown",
            "confidence": metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "fields_filled": fields_filled,
        },
    }
    return fields_filled


async def maybe_repair_extraction(
    session: AsyncSession, document: Document, settings: Settings
) -> None:
    """Run the fill-only repair pass for one document, best-effort.

    Called at the tail of the markdown stage. Always records an audit event
    (``extraction_repair_completed`` / ``extraction_repair_skipped``) and never
    raises for repair-level problems — the markdown stage (and the pipeline)
    must proceed no matter what happens here.

    Gate order (each skip records its reason, and no Anthropic call is made
    on any skip path): ``disabled`` / ``missing_api_key`` (repair is
    extraction spend, so extraction's own switches govern it) → ``no_markdown``
    (nothing to read) → ``no_extraction`` (repair fills gaps in an extraction
    that ran; without one it would become a covert extraction pass) →
    ``already_repaired`` (idempotency: ``extra["extraction_repair"]`` already
    stamped with the current :data:`REPAIR_PROMPT_VERSION` — backfills must not
    re-spend) → ``no_gaps`` (no repairable finding) → ``budget`` (checked last
    so a clean document is never mislabelled a budget skip).
    """
    if not settings.extraction_enabled:
        await _record_event(session, document, "extraction_repair_skipped", {"reason": "disabled"})
        return
    if settings.anthropic_api_key is None:
        await _record_event(
            session, document, "extraction_repair_skipped", {"reason": "missing_api_key"}
        )
        return
    if not (document.pages_markdown or "").strip():
        await _record_event(
            session, document, "extraction_repair_skipped", {"reason": "no_markdown"}
        )
        return
    extra = document.extra if isinstance(document.extra, dict) else {}
    if not isinstance(extra.get("extraction"), dict):
        await _record_event(
            session, document, "extraction_repair_skipped", {"reason": "no_extraction"}
        )
        return
    previous = extra.get("extraction_repair")
    if isinstance(previous, dict) and previous.get("prompt_version") == REPAIR_PROMPT_VERSION:
        await _record_event(
            session,
            document,
            "extraction_repair_skipped",
            {"reason": "already_repaired", "prompt_version": REPAIR_PROMPT_VERSION},
        )
        return

    try:
        findings = await revalidate_document(session, document, settings)
    except Exception as exc:
        logger.exception("pre-repair validation failed for document %s", document.id)
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session,
            document,
            "extraction_repair_skipped",
            {"reason": "error", "error": str(exc), "prompt_version": REPAIR_PROMPT_VERSION},
        )
        return
    gaps = sorted({finding.rule for finding in findings} & REPAIRABLE_RULES)
    if not gaps:
        await _record_event(session, document, "extraction_repair_skipped", {"reason": "no_gaps"})
        return

    spent = await todays_spend_usd(session)
    if spent >= settings.extraction_daily_budget_usd:
        await _record_event(
            session,
            document,
            "extraction_repair_skipped",
            {
                "reason": "budget",
                "spent_usd": spent,
                "budget_usd": settings.extraction_daily_budget_usd,
            },
        )
        return

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            outcome = await repair_extract(document, client=client, settings=settings)
        fields_filled = await _apply_repair(session, document, outcome)
    except Exception as exc:
        logger.exception("extraction repair failed for document %s", document.id)
        # Drop any partial state, then un-expire the document (rollback expires
        # loaded attributes; a later lazy access would need sync IO and fail).
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session,
            document,
            "extraction_repair_skipped",
            {"reason": "error", "error": str(exc), "prompt_version": REPAIR_PROMPT_VERSION},
        )
        return
    try:
        # Re-derive findings and review_status from the repaired values, the
        # same policy as the extraction path (_apply_validation): a repaired
        # document leaves needs_review; a still-broken one stays flagged.
        findings = await revalidate_document(session, document, settings)
        document.review_status = derive_review_status(findings)
    except Exception:  # validation is best-effort; never fail the document
        logger.exception("post-repair validation failed for document %s", document.id)
    await _record_event(
        session,
        document,
        "extraction_repair_completed",
        {
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "input": "markdown",
            "confidence": outcome.metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "fields_filled": fields_filled,
            "gaps": gaps,
        },
    )
    logger.info(
        "extraction repair completed for document %s: gaps=%s filled=%s cost=$%.4f",
        document.id,
        gaps,
        fields_filled,
        outcome.cost_usd,
    )
