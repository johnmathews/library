"""Run extraction for a document and apply the result to the database.

This is the pipeline-facing half of W6. The invariant it enforces:
**extraction never fails a document.** Disabled feature, missing API key,
blown budget, unusable input, API errors — all end in a skip/failed audit
event and a normal return, so the pipeline continues to ``indexed`` and the
document stays searchable by its OCR text.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import Numeric, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.extractor import (
    PROMPT_VERSION,
    ExtractionOutcome,
    ExtractionSkipped,
    extract,
)
from library.extraction.validation import derive_review_status, findings_to_payload, validate
from library.models import (
    Document,
    DocumentLanguage,
    IngestionEvent,
    Kind,
    Sender,
    Tag,
)

logger = logging.getLogger(__name__)


async def todays_spend_usd(session: AsyncSession) -> float:
    """Sum today's (UTC) estimated extraction spend from the audit trail."""
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.coalesce(func.sum(IngestionEvent.detail["cost_usd"].astext.cast(Numeric)), 0)
    ).where(
        IngestionEvent.event == "extraction_completed",
        IngestionEvent.detail.has_key("cost_usd"),
        IngestionEvent.created_at >= start_of_day,
    )
    return float((await session.execute(statement)).scalar_one())


async def upsert_sender(session: AsyncSession, name: str) -> Sender:
    """Find a sender by case-insensitive name match, creating it if new."""
    cleaned = name.strip()
    existing = (
        await session.execute(select(Sender).where(func.lower(Sender.name) == cleaned.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=cleaned)
    session.add(sender)
    await session.flush()
    return sender


async def get_or_create_tag(session: AsyncSession, slug: str) -> Tag:
    existing = (await session.execute(select(Tag).where(Tag.slug == slug))).scalar_one_or_none()
    if existing is not None:
        return existing
    tag = Tag(slug=slug, name=slug.replace("-", " ").capitalize())
    session.add(tag)
    await session.flush()
    return tag


async def _record_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, Any]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def _apply_outcome(
    session: AsyncSession, document: Document, outcome: ExtractionOutcome
) -> list[str]:
    """Write extracted values onto the document; return the fields set.

    Skips any field listed in ``extra["user_edited_fields"]`` (user edits
    win over re-extraction) and never nulls out existing data with a None
    extraction value.
    """
    metadata = outcome.metadata
    user_edited = set(document.extra.get("user_edited_fields", []))
    fields_set: list[str] = []

    def settable(field: str, value: object) -> bool:
        return value is not None and field not in user_edited

    if settable("kind_id", metadata.kind_slug):
        kind = (
            await session.execute(select(Kind).where(Kind.slug == metadata.kind_slug))
        ).scalar_one_or_none()
        if kind is not None:
            document.kind_id = kind.id
            fields_set.append("kind_id")

    if settable("sender_id", metadata.sender_name):
        assert metadata.sender_name is not None
        document.sender_id = (await upsert_sender(session, metadata.sender_name)).id
        fields_set.append("sender_id")

    scalar_values: dict[str, object | None] = {
        "title": metadata.title,
        "summary": metadata.summary,
        "document_date": metadata.document_date,
        "due_date": metadata.due_date,
        "expiry_date": metadata.expiry_date,
        "amount_total": Decimal(metadata.amount_total) if metadata.amount_total else None,
        "currency": metadata.currency,
    }
    for field, value in scalar_values.items():
        if settable(field, value):
            setattr(document, field, value)
            fields_set.append(field)

    if metadata.language != "unknown" and "language" not in user_edited:
        document.language = DocumentLanguage(metadata.language)
        fields_set.append("language")

    if metadata.topics and "topics" not in user_edited:
        document.topics = metadata.topics
        fields_set.append("topics")

    if metadata.tags and "tags" not in user_edited:
        existing_slugs = {tag.slug for tag in document.tags}
        merged = False
        for slug in metadata.tags:
            if slug not in existing_slugs:
                document.tags.append(await get_or_create_tag(session, slug))
                merged = True
        if merged:
            fields_set.append("tags")

    document.extra = {
        **document.extra,
        "extraction": {
            "prompt_version": outcome.prompt_version,
            "model": outcome.model,
            "confidence": metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "escalated": outcome.escalated,
            "input_mode": outcome.input_mode,
            "fields_set": fields_set,
            "reasoning_note": metadata.reasoning_note,
        },
    }
    return fields_set


async def _apply_validation(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Run deterministic validation and set review_status + extra["validation"].

    Best-effort: the caller (``apply_extraction``) wraps this in a try/except so
    any failure here never propagates and never fails the document.  There is no
    need to skip user-locked fields — validation reads whatever the document's
    current values are, regardless of their provenance.
    """
    kind_slug: str | None = None
    if document.kind_id is not None:
        kind = await session.get(Kind, document.kind_id)
        kind_slug = kind.slug if kind is not None else None

    findings = validate(
        document,
        kind_slug=kind_slug,
        ocr_floor=settings.extraction_validation_ocr_floor,
        today=datetime.now(UTC).date(),
    )
    document.review_status = derive_review_status(findings)
    document.extra = {
        **document.extra,
        "validation": {
            "prompt_version": PROMPT_VERSION,
            "findings": findings_to_payload(findings),
            "validated_at": datetime.now(UTC).isoformat(),
        },
    }


async def apply_extraction(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Extract metadata for one document and persist the result.

    Always commits an audit event (``extraction_completed`` /
    ``extraction_skipped`` / ``extraction_failed``) and never raises for
    extraction-level problems — the document must reach ``indexed`` no
    matter what happens here.
    """
    if not settings.extraction_enabled:
        await _record_event(session, document, "extraction_skipped", {"reason": "disabled"})
        return
    if settings.anthropic_api_key is None:
        await _record_event(session, document, "extraction_skipped", {"reason": "missing_api_key"})
        return

    spent = await todays_spend_usd(session)
    if spent >= settings.extraction_daily_budget_usd:
        await _record_event(
            session,
            document,
            "extraction_skipped",
            {
                "reason": "budget",
                "spent_usd": spent,
                "budget_usd": settings.extraction_daily_budget_usd,
            },
        )
        return

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            outcome = await extract(
                document, document.ocr_text or "", client=client, settings=settings
            )
    except ExtractionSkipped as exc:
        await _record_event(
            session, document, "extraction_skipped", {"reason": exc.reason, "detail": str(exc)}
        )
        return
    except Exception as exc:
        logger.exception("extraction failed for document %s", document.id)
        # Drop any partial state, then un-expire the document (rollback expires
        # loaded attributes; a later lazy access would need sync IO and fail).
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session,
            document,
            "extraction_failed",
            {"error": str(exc), "prompt_version": PROMPT_VERSION},
        )
        return

    fields_set = await _apply_outcome(session, document, outcome)
    try:
        await _apply_validation(session, document, settings)
    except Exception:  # validation is best-effort; never fail the document
        logger.exception("validation failed for document %s", document.id)
    await _record_event(
        session,
        document,
        "extraction_completed",
        {
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "confidence": outcome.metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "escalated": outcome.escalated,
            "input_mode": outcome.input_mode,
            "fields_set": fields_set,
        },
    )
    logger.info(
        "extraction completed for document %s: model=%s confidence=%s cost=$%.4f fields=%s",
        document.id,
        outcome.model,
        outcome.metadata.confidence,
        outcome.cost_usd,
        fields_set,
    )
