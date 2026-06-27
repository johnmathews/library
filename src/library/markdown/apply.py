"""Run vision markdown generation for a document and persist per-page rows.

Same invariant as extraction: **markdown never fails a document.** Disabled,
missing key, blown budget, unusable input, API errors — all end in a
skip/failed audit event and a normal return, so the pipeline continues.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import Numeric, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.markdown.generator import (
    PROMPT_VERSION,
    MarkdownSkipped,
    generate_markdown,
)
from library.markdown.renderer import render_page_images
from library.models import Document, DocumentPage, IngestionEvent
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)


async def todays_markdown_spend_usd(session: AsyncSession) -> float:
    """Sum today's (UTC) estimated markdown spend from markdown_completed events."""
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.coalesce(func.sum(IngestionEvent.detail["cost_usd"].astext.cast(Numeric)), 0)
    ).where(
        IngestionEvent.event == "markdown_completed",
        IngestionEvent.detail.has_key("cost_usd"),
        IngestionEvent.created_at >= start_of_day,
    )
    return float((await session.execute(statement)).scalar_one())


async def _record_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, Any]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def _apply_born_digital_markdown(session: AsyncSession, document: Document) -> None:
    """Synthesize the markdown layer for born-digital text directly from OCR text.

    For ``text/markdown``/``text/plain`` the raw file content (already captured
    as ``ocr_text`` by the OCR passthrough) is the authoritative text layer, so
    one ``DocumentPage`` is written verbatim — no Anthropic call, no budget
    consumption, bypassing ``markdown_max_pages``.
    """
    body = (document.ocr_text or "").strip()
    if not body:
        await _record_event(session, document, "markdown_skipped", {"reason": "no_text"})
        return
    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    session.add(
        DocumentPage(
            document_id=document.id,
            page_number=1,
            markdown=body,
            char_count=len(body),
        )
    )
    document.page_count = 1
    await _record_event(
        session,
        document,
        "markdown_completed",
        {"engine": "passthrough", "model": None, "pages": 1, "cost_usd": 0.0},
    )
    logger.info(
        "markdown passthrough for born-digital document %s (%s)", document.id, document.mime_type
    )


async def apply_markdown(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Generate per-page markdown for one document and persist it (best-effort)."""
    if document.mime_type in ("text/markdown", "text/plain"):
        await _apply_born_digital_markdown(session, document)
        return
    if not settings.markdown_enabled:
        await _record_event(session, document, "markdown_skipped", {"reason": "disabled"})
        return
    if settings.anthropic_api_key is None:
        await _record_event(session, document, "markdown_skipped", {"reason": "missing_api_key"})
        return

    spent = await todays_markdown_spend_usd(session)
    if spent >= settings.markdown_daily_budget_usd:
        await _record_event(
            session,
            document,
            "markdown_skipped",
            {
                "reason": "budget",
                "spent_usd": spent,
                "budget_usd": settings.markdown_daily_budget_usd,
            },
        )
        return

    try:
        images = render_page_images(
            document.mime_type,
            path_for(document.sha256),
            derived_dir(document.sha256),
            max_pages=settings.markdown_max_pages,
            long_side_px=settings.markdown_image_long_side_px,
        )
    except Exception as exc:
        logger.exception("markdown renderer raised for document %s", document.id)
        await _record_event(
            session,
            document,
            "markdown_skipped",
            {"reason": "input_unusable", "error": str(exc)},
        )
        return

    if not images:
        await _record_event(
            session,
            document,
            "markdown_skipped",
            {"reason": "input_unusable", "mime": document.mime_type},
        )
        return

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            result = await generate_markdown(
                document, document.ocr_text or "", images, client=client, settings=settings
            )
    except MarkdownSkipped as exc:
        await _record_event(
            session, document, "markdown_skipped", {"reason": exc.reason, "detail": str(exc)}
        )
        return
    except Exception as exc:
        logger.exception("markdown generation failed for document %s", document.id)
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session,
            document,
            "markdown_failed",
            {"error": str(exc), "prompt_version": PROMPT_VERSION},
        )
        return

    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    for page in result.pages:
        session.add(
            DocumentPage(
                document_id=document.id,
                page_number=page.page_number,
                markdown=page.markdown,
                char_count=len(page.markdown),
            )
        )
    await _record_event(
        session,
        document,
        "markdown_completed",
        {
            "model": result.model,
            "prompt_version": result.prompt_version,
            "pages": len(result.pages),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
        },
    )
    logger.info(
        "markdown completed for document %s: pages=%s cost=$%.4f",
        document.id,
        len(result.pages),
        result.cost_usd,
    )
