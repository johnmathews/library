"""Procrastinate job queue wiring and the document-processing pipeline.

The pipeline advances a document through ``received -> ocr -> extract ->
markdown -> embed -> indexed``, recording an ingestion event per transition.
The OCR stage (W4) runs the routed engines from ``library.ocr``; the extract
stage (W6) runs Claude metadata extraction from ``library.extraction``; the
markdown stage runs Claude-vision per-page markdown generation; the embed
stage chunks the text and computes embeddings.
"""

import asyncio
import json
import logging

from procrastinate import App, PsycopgConnector
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library import thumbnails
from library.config import get_settings
from library.db import get_sessionmaker
from library.embedding import EmbeddingError, embed_texts
from library.embedding.chunker import chunk_markdown, chunk_text
from library.extraction.apply import apply_extraction
from library.markdown.apply import apply_markdown
from library.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    IngestionEvent,
    ReviewStatus,
)
from library.notifications import (
    dispatch_document_completion,
    dispatch_document_notification,
)
from library.ocr import router as ocr_router
from library.schemas import NotificationEvent
from library.series_insight import refresh_series_insight
from library.series_match import propose_authored_matches
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)

# Next status in the happy path; INDEXED and FAILED are terminal.
_NEXT_STATUS: dict[DocumentStatus, DocumentStatus] = {
    DocumentStatus.RECEIVED: DocumentStatus.OCR,
    DocumentStatus.OCR: DocumentStatus.EXTRACT,
    DocumentStatus.EXTRACT: DocumentStatus.MARKDOWN,
    DocumentStatus.MARKDOWN: DocumentStatus.EMBED,
    DocumentStatus.EMBED: DocumentStatus.INDEXED,
}

_TERMINAL_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.INDEXED, DocumentStatus.FAILED}
)


def procrastinate_conninfo(database_url: str) -> str:
    """Translate a SQLAlchemy asyncpg URL into a libpq URL for psycopg."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# Postgres NOTIFY channel the SSE endpoint (``library.api.events``) listens on.
# The worker emits on it as documents move through the pipeline; the payload is
# a compact JSON object kept well under Postgres's 8 kB NOTIFY limit.
EVENTS_CHANNEL = "library_doc_events"


async def notify_document_event(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: int,
    event: str,
    status: str,
    *,
    title: str | None = None,
) -> None:
    """Best-effort Postgres NOTIFY so the SSE endpoint can push live updates.

    Runs on its own short-lived session, fully decoupled from the pipeline's
    unit of work: a NOTIFY failure is isolated to this session (the ``async
    with`` rolls it back on error) and can never strand a document or fail the
    job — any error is logged and swallowed, mirroring the thumbnail-defer
    guard. Crosses the worker→api process boundary via Postgres itself.
    """
    payload = json.dumps(
        {"document_id": document_id, "event": event, "status": status, "title": title}
    )
    try:
        async with session_factory() as session:
            await session.execute(
                sql_text("SELECT pg_notify(:channel, :payload)"),
                {"channel": EVENTS_CHANNEL, "payload": payload},
            )
            await session.commit()
    except Exception:
        logger.warning(
            "could not emit %s NOTIFY for document %s; continuing",
            event,
            document_id,
            exc_info=True,
        )


job_app: App = App(
    connector=PsycopgConnector(conninfo=procrastinate_conninfo(get_settings().database_url))
)


async def run_ocr(session: AsyncSession, document: Document) -> None:
    """OCR stage: route to the right engine, persist results, record an event.

    The routed OCR work is CPU-bound and subprocess-heavy, so it runs in a
    thread (``asyncio.to_thread``) to keep the async worker responsive. On
    success the document gains ``ocr_text``/``ocr_confidence``/``page_count``/
    ``searchable_pdf`` and an ``ocr_completed`` event; on failure an
    ``ocr_failed`` event is committed and the error re-raised (the pipeline's
    generic failure handling then marks the document failed).
    """
    original_path = path_for(document.sha256)
    derived = derived_dir(document.sha256)
    try:
        result = await asyncio.to_thread(ocr_router.run_ocr, document, original_path, derived)
    except Exception as exc:
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="ocr_failed",
                detail={"error": str(exc)},
            )
        )
        await session.commit()
        raise
    document.ocr_text = result.text or None
    document.ocr_confidence = result.confidence
    document.page_count = result.pages
    document.searchable_pdf = result.searchable_pdf is not None
    detail: dict[str, object] = {
        "engine": result.engine,
        "confidence": result.confidence,
        "pages": result.pages,
        "characters": len(result.text),
    }
    if result.gate is not None:
        # The confidence gate retried via the photo path: record both raw
        # confidences (incomparable scales; `engine` names the kept one).
        detail["gate"] = {
            "tesseract_confidence": result.gate.tesseract_confidence,
            "rapidocr_confidence": result.gate.rapidocr_confidence,
        }
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="ocr_completed",
            detail=detail,
        )
    )
    await session.commit()
    logger.info(
        "OCR completed for document %s: engine=%s confidence=%s pages=%s chars=%s",
        document.id,
        result.engine,
        result.confidence,
        result.pages,
        len(result.text),
    )


async def run_extraction(session: AsyncSession, document: Document) -> None:
    """Extraction stage: Claude metadata extraction (best-effort, never raises).

    Skips/failures are recorded as ingestion events and the pipeline
    continues — extraction must not stop a document from reaching
    ``indexed`` (it stays searchable by OCR text either way).
    """
    await apply_extraction(session, document, get_settings())


async def run_markdown(session: AsyncSession, document: Document) -> None:
    """Markdown stage: Claude vision per-page markdown (best-effort, never raises)."""
    await apply_markdown(session, document, get_settings())


async def _record_embed_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, object]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def run_embed(session: AsyncSession, document: Document) -> None:
    """Embedding stage: chunk OCR text and store vectors (best-effort).

    Like extraction, embedding must never stop a document reaching
    ``indexed``: when disabled, textless, or the embedder is unreachable, the
    reason is recorded as an event and swallowed. Re-running replaces the
    document's existing chunks (idempotent re-embed).
    """
    settings = get_settings()
    if not settings.embedding_enabled:
        await _record_embed_event(session, document, "embedding_skipped", {"reason": "disabled"})
        return

    pages = (
        (
            await session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number)
            )
        )
        .scalars()
        .all()
    )

    chunker = chunk_markdown if document.mime_type == "text/markdown" else chunk_text
    chunk_records: list[tuple[str, int | None]] = []
    if pages:
        for page in pages:
            for piece in chunker(
                page.markdown,
                max_chars=settings.embedding_chunk_chars,
                overlap=settings.embedding_chunk_overlap,
            ):
                chunk_records.append((piece, page.page_number))
    else:
        for piece in chunker(
            document.ocr_text or "",
            max_chars=settings.embedding_chunk_chars,
            overlap=settings.embedding_chunk_overlap,
        ):
            chunk_records.append((piece, None))

    if not chunk_records:
        await _record_embed_event(session, document, "embedding_skipped", {"reason": "no_text"})
        return

    texts = [text for text, _ in chunk_records]
    try:
        vectors = await embed_texts(texts, settings=settings)
    except EmbeddingError as exc:
        await _record_embed_event(
            session, document, "embedding_failed", {"error": str(exc), "chunks": len(texts)}
        )
        return

    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    for index, ((text, page_number), vector) in enumerate(
        zip(chunk_records, vectors, strict=True), start=1
    ):
        session.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                page_number=page_number,
                text=text,
                embedding=vector,
            )
        )
    await _record_embed_event(
        session,
        document,
        "embedded",
        {"chunks": len(texts), "model": settings.embedding_model_name, "page_aware": bool(pages)},
    )
    logger.info(
        "embedded document %s into %s chunks (page_aware=%s)", document.id, len(texts), bool(pages)
    )


async def _run_stage_hook(
    session: AsyncSession, document: Document, status: DocumentStatus
) -> None:
    """Run the work associated with having entered the given status."""
    if status is DocumentStatus.OCR:
        await run_ocr(session, document)
        # Thumbnail rendering needs nothing from extraction (and the HEIC
        # conversion already exists from ingest), so once OCR has finished
        # it runs as a separate job, in parallel with the extract stage.
        # Best-effort: OCR results are already committed, and a transient
        # queue error here must not strand the document in ``failed``.
        try:
            await generate_thumbnail.defer_async(document_id=document.id)
        except Exception:
            logger.warning(
                "could not queue thumbnail for document %s; continuing",
                document.id,
                exc_info=True,
            )
    elif status is DocumentStatus.EXTRACT:
        await run_extraction(session, document)
    elif status is DocumentStatus.MARKDOWN:
        await run_markdown(session, document)
    elif status is DocumentStatus.EMBED:
        await run_embed(session, document)


async def advance_pipeline(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> None:
    """Advance a document through the status lifecycle until indexed.

    Resumes from the document's current status, so re-running on an already
    indexed (or failed) document is a no-op. Any exception marks the document
    failed, records a ``failed`` event, and re-raises so the job is also
    marked failed in Procrastinate.
    """
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        if document.status in _TERMINAL_STATUSES:
            logger.info("document %s already %s; nothing to do", document_id, document.status)
            return

        try:
            while document.status is not DocumentStatus.INDEXED:
                previous = document.status
                document.status = _NEXT_STATUS[previous]
                session.add(
                    IngestionEvent(
                        document_id=document.id,
                        event="status_changed",
                        detail={"from": previous.value, "to": document.status.value},
                    )
                )
                await session.commit()
                await notify_document_event(
                    session_factory,
                    document.id,
                    "status_changed",
                    document.status.value,
                    title=document.title,
                )
                await _run_stage_hook(session, document, document.status)
            # Reached INDEXED: send the owner one completion push (success, or
            # needs-review when extraction flagged the document). Best-effort.
            await dispatch_document_completion(
                session_factory,
                document.id,
                needs_review=document.review_status == ReviewStatus.NEEDS_REVIEW,
                document_url_base=get_settings().public_base_url,
            )
            # This document may have joined (or grown) a recurring series; refresh
            # its cached LLM description out of band. Best-effort: a queue hiccup
            # must never strand an already-indexed document.
            if document.sender_id is not None and document.kind_id is not None:
                try:
                    await generate_series_insight.defer_async(
                        sender_id=document.sender_id, kind_id=document.kind_id
                    )
                except Exception:
                    logger.warning(
                        "could not queue series insight for document %s; continuing",
                        document.id,
                        exc_info=True,
                    )
                # It may also match an authored series' signature: propose it for
                # review (never a silent membership). Best-effort, same as above.
                try:
                    await evaluate_series_autocontinue.defer_async(document_id=document.id)
                except Exception:
                    logger.warning(
                        "could not queue series autocontinue for document %s; continuing",
                        document.id,
                        exc_info=True,
                    )
        except Exception as exc:
            failed_in = document.status
            await session.rollback()
            document.status = DocumentStatus.FAILED
            session.add(
                IngestionEvent(
                    document_id=document.id,
                    event="failed",
                    detail={"error": str(exc), "status": failed_in.value},
                )
            )
            await session.commit()
            await notify_document_event(
                session_factory, document.id, "failed", "failed", title=document.title
            )
            await dispatch_document_notification(
                session_factory,
                document.id,
                NotificationEvent.PROCESSING_ERROR,
                document_url_base=get_settings().public_base_url,
            )
            logger.exception("document %s failed during %s", document_id, failed_in.value)
            raise


@job_app.task(name="library.jobs.process_document")
async def process_document(document_id: int) -> None:
    """Background task: run the processing pipeline for one document."""
    await advance_pipeline(get_sessionmaker(), document_id)


async def run_generate_thumbnail(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> None:
    """Render the first-page WebP thumbnail for a document and record an event.

    The artifact lands at ``derived/<sha>/thumb.webp``; its existence is the
    only thumbnail marker (no database column). Types without a visual
    (plain text) record a ``thumbnail_skipped`` event instead.
    """
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        target = await asyncio.to_thread(
            thumbnails.render_thumbnail,
            document.mime_type,
            path_for(document.sha256),
            derived_dir(document.sha256),
        )
        if target is None:
            event = "thumbnail_skipped"
            detail = {"reason": "unsupported_mime", "mime_type": document.mime_type}
        else:
            event = "thumbnail_generated"
            detail = {"artifact": target.name}
        session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
        await session.commit()
        logger.info("thumbnail %s for document %s", event, document_id)


@job_app.task(name="library.jobs.generate_thumbnail")
async def generate_thumbnail(document_id: int) -> None:
    """Background task: render the first-page thumbnail for one document.

    Deferred by the pipeline after OCR completes; safe to re-run (the
    artifact is simply rewritten).
    """
    await run_generate_thumbnail(get_sessionmaker(), document_id)


@job_app.task(name="library.jobs.extract_document")
async def extract_document(document_id: int) -> None:
    """Background task: (re-)run metadata extraction for one document.

    Deferred manually (e.g. after a prompt upgrade) — independent of the
    pipeline status, so it also works on already-indexed documents.
    Re-extraction overwrites extraction-owned fields but honours
    ``extra["user_edited_fields"]`` and never removes tags.
    """
    async with get_sessionmaker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        await apply_extraction(session, document, get_settings())


@job_app.task(name="library.jobs.embed_document")
async def embed_document(document_id: int) -> None:
    """Background task: (re-)embed one document, independent of pipeline status.

    Deferred by the backfill CLI to populate chunks for documents indexed
    before the embedding stage existed. Best-effort and idempotent (replaces
    any existing chunks); works on already-indexed documents.
    """
    async with get_sessionmaker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        await run_embed(session, document)


@job_app.task(name="library.jobs.markdown_document")
async def markdown_document(document_id: int) -> None:
    """Background task: (re-)generate markdown for one document, then re-embed.

    Deferred by the backfill CLI (and after a prompt upgrade), independent of
    pipeline status. Best-effort and idempotent (replaces a document's pages
    and, via run_embed, its chunks).
    """
    async with get_sessionmaker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        await apply_markdown(session, document, get_settings())
        await run_embed(session, document)


@job_app.task(name="library.jobs.generate_series_insight")
async def generate_series_insight(sender_id: int, kind_id: int) -> None:
    """Background task: (re-)generate the cached LLM description for one series.

    Deferred when a document reaches ``indexed`` with both a sender and a kind.
    Best-effort and idempotent (upserts the single ``series_insights`` row);
    skips quietly when the series is too small or extraction is disabled.
    """
    async with get_sessionmaker()() as session:
        await refresh_series_insight(session, get_settings(), sender_id, kind_id)


@job_app.task(name="library.jobs.evaluate_series_autocontinue")
async def evaluate_series_autocontinue(document_id: int) -> None:
    """Background task: propose an indexed document for any authored series it matches.

    Deferred when a document reaches ``indexed`` with both a sender and a kind.
    PROPOSE-FOR-REVIEW: records pending suggestions only, never adds a member.
    Best-effort and idempotent (skips docs already suggested/dismissed/member).
    """
    async with get_sessionmaker()() as session:
        await propose_authored_matches(session, get_settings(), document_id)


def email_poll_cron(minutes: int) -> str:
    """Cron expression for the email poller: every ``minutes`` minutes.

    Cron steps live in the minute field (0-59), so the value is clamped
    to 1-59; longer intervals are not worth a second schedule shape for
    a mailbox poll.
    """
    return f"*/{min(max(minutes, 1), 59)} * * * *"


@job_app.periodic(cron=email_poll_cron(get_settings().email_poll_minutes))
@job_app.task(name="library.jobs.poll_email_inbox")
async def poll_email_inbox(timestamp: int) -> None:
    """Periodic task: poll the IMAP inbox for attachment documents (W14).

    Instant no-op while ``LIBRARY_EMAIL_HOST`` is unset (the schedule
    still ticks; the task just returns). The synchronous IMAP work runs
    in a thread via ``poll_mailbox_async`` so the worker loop stays
    responsive.
    """
    settings = get_settings()
    if settings.email_host is None:
        return
    # Imported lazily: email_ingest imports library.ingest, which imports
    # this module for process_document — a top-level import would cycle.
    from library.email_ingest import poll_mailbox_async

    summary = await poll_mailbox_async(settings, get_sessionmaker())
    logger.info("email poll (scheduled for %s): %s", timestamp, summary)
