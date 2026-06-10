"""Procrastinate job queue wiring and the document-processing pipeline.

The pipeline advances a document through ``received -> ocr -> extract ->
indexed`` recording an ingestion event per transition. The OCR stage (W4)
runs the routed engines from ``library.ocr``; the extract stage (W6) runs
Claude metadata extraction from ``library.extraction``.
"""

import asyncio
import logging

from procrastinate import App, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library import thumbnails
from library.config import get_settings
from library.db import get_sessionmaker
from library.extraction.apply import apply_extraction
from library.models import Document, DocumentStatus, IngestionEvent
from library.ocr import router as ocr_router
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)

# Next status in the happy path; INDEXED and FAILED are terminal.
_NEXT_STATUS: dict[DocumentStatus, DocumentStatus] = {
    DocumentStatus.RECEIVED: DocumentStatus.OCR,
    DocumentStatus.OCR: DocumentStatus.EXTRACT,
    DocumentStatus.EXTRACT: DocumentStatus.INDEXED,
}

_TERMINAL_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.INDEXED, DocumentStatus.FAILED}
)


def procrastinate_conninfo(database_url: str) -> str:
    """Translate a SQLAlchemy asyncpg URL into a libpq URL for psycopg."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


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
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="ocr_completed",
            detail={
                "engine": result.engine,
                "confidence": result.confidence,
                "pages": result.pages,
                "characters": len(result.text),
            },
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


async def _run_stage_hook(
    session: AsyncSession, document: Document, status: DocumentStatus
) -> None:
    """Run the work associated with having entered the given status."""
    if status is DocumentStatus.OCR:
        await run_ocr(session, document)
        # Thumbnail rendering needs nothing from extraction (and the HEIC
        # conversion already exists from ingest), so once OCR has finished
        # it runs as a separate job, in parallel with the extract stage.
        await generate_thumbnail.defer_async(document_id=document.id)
    elif status is DocumentStatus.EXTRACT:
        await run_extraction(session, document)


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
                await _run_stage_hook(session, document, document.status)
        except Exception as exc:
            await session.rollback()
            failed_in = document.status
            document.status = DocumentStatus.FAILED
            session.add(
                IngestionEvent(
                    document_id=document.id,
                    event="failed",
                    detail={"error": str(exc), "status": failed_in.value},
                )
            )
            await session.commit()
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
