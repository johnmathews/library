"""Procrastinate job queue wiring and the document-processing pipeline.

The pipeline in W3 is a skeleton: it advances a document through
``received -> ocr -> extract -> indexed`` recording an ingestion event per
transition. Real OCR (W4) and extraction (W6) plug into the ``run_ocr`` /
``run_extraction`` hooks, which currently no-op.
"""

import logging

from procrastinate import App, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from library.config import get_settings
from library.db import get_sessionmaker
from library.models import Document, DocumentStatus, IngestionEvent

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


def run_ocr(document: Document) -> None:
    """OCR hook — W4 replaces this no-op with the real routed OCR engines."""


def run_extraction(document: Document) -> None:
    """Extraction hook — W6 replaces this no-op with LLM metadata extraction."""


def _run_stage_hook(document: Document, status: DocumentStatus) -> None:
    """Run the work associated with having entered the given status."""
    if status is DocumentStatus.OCR:
        run_ocr(document)
    elif status is DocumentStatus.EXTRACT:
        run_extraction(document)


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
                _run_stage_hook(document, document.status)
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
