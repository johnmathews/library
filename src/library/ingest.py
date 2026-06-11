"""Ingestion service: bytes in, Document row + queued processing job out.

This is the single entry point for every ingestion channel (upload now;
consume folder, email, and MCP later — hence the ``source`` parameter).
See docs/ingestion.md for the full flow.
"""

import hashlib
import logging
from dataclasses import dataclass

import filetype
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.images import CONVERTED_JPEG_NAME, HEIC_MIME_TYPES, normalize_image
from library.jobs import process_document
from library.models import Document, DocumentSource, IngestionEvent
from library.storage import derived_dir, store

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
        "image/tiff",
        "text/plain",
    }
)

# Common client-declared aliases, normalised before validation.
_MIME_ALIASES: dict[str, str] = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/tif": "image/tiff",
    "text/plain; charset=utf-8": "text/plain",
}


class IngestError(Exception):
    """Base class for ingestion failures."""


class UnsupportedMimeTypeError(IngestError):
    """The content is not one of the accepted document types."""

    def __init__(self, mime: str | None) -> None:
        self.mime = mime
        super().__init__(f"unsupported mime type: {mime!r}")


class DeletedDuplicateError(IngestError):
    """The same content exists as a soft-deleted document (sha256 is unique)."""

    def __init__(self, document: Document) -> None:
        self.document = document
        super().__init__(f"content matches soft-deleted document {document.id}")


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of an ingest call: the document and whether it already existed."""

    document: Document
    duplicate: bool


def detect_mime(content: bytes, claimed: str | None) -> str | None:
    """Determine the mime type, preferring content sniffing over the client's claim.

    ``filetype`` (pure Python, magic-bytes based) covers every binary type we
    accept; it cannot identify plain text, so UTF-8-decodable content falls
    back to ``text/plain``. The claimed type is the last resort.
    """
    kind = filetype.guess(content)
    if kind is not None:
        return str(kind.mime)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "text/plain"
    if claimed:
        normalized = claimed.strip().lower()
        return _MIME_ALIASES.get(normalized, normalized)
    return None


async def ingest_file(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str | None,
    mime: str | None = None,
    source: DocumentSource,
    uploader_id: int | None = None,
    extra_event_detail: dict[str, object] | None = None,
    defer_processing: bool = True,
) -> IngestResult:
    """Validate, store, register, and enqueue processing for one file.

    Raises UnsupportedMimeTypeError for content outside the allowed set and
    DeletedDuplicateError when the content matches a soft-deleted document.
    Returns ``duplicate=True`` (with the existing document, no new file or
    row) when a non-deleted document already has this content.

    ``extra_event_detail`` lets a channel attach provenance to the recorded
    ``received``/``duplicate_upload`` event (e.g. email sender/subject/
    message-id — see ``library.email_ingest``); keys are merged into the
    standard detail dict.

    ``defer_processing=False`` skips queueing the standard pipeline job; a
    caller that pre-fills pipeline outputs (the paperless importer reuses
    paperless's OCR text) takes over status handling and job deferral
    itself (see ``library.importer.runner``).
    """
    detected = detect_mime(content, mime)
    if detected not in ALLOWED_MIME_TYPES:
        raise UnsupportedMimeTypeError(detected)

    sha256 = hashlib.sha256(content).hexdigest()

    existing = (
        await session.execute(select(Document).where(Document.sha256 == sha256))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.deleted_at is not None:
            raise DeletedDuplicateError(existing)
        session.add(
            IngestionEvent(
                document_id=existing.id,
                event="duplicate_upload",
                detail={
                    "filename": filename,
                    "source": source.value,
                    **(extra_event_detail or {}),
                },
            )
        )
        await session.commit()
        logger.info("duplicate upload of document %s (sha256 %s)", existing.id, sha256)
        return IngestResult(existing, duplicate=True)

    stored = store(content)

    # HEIC originals stay the source of truth; the JPEG conversion becomes a
    # derived artifact used by downstream steps.
    if detected in HEIC_MIME_TYPES:
        normalized = normalize_image(content, detected)
        converted_path = derived_dir(sha256) / CONVERTED_JPEG_NAME
        converted_path.write_bytes(normalized.content)

    document = Document(
        sha256=sha256,
        mime_type=detected,
        source=source,
        original_filename=filename,
        uploader_id=uploader_id,
    )
    session.add(document)
    await session.flush()
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="received",
            detail={
                "filename": filename,
                "size": len(content),
                "mime_type": detected,
                "source": source.value,
                **(extra_event_detail or {}),
            },
        )
    )
    # Commit before deferring: Procrastinate defers over its own connection,
    # so a job deferred first could be picked up before the row is visible.
    await session.commit()
    if defer_processing:
        await process_document.defer_async(document_id=document.id)
    logger.info(
        "ingested document %s (sha256 %s, %s, created=%s)",
        document.id,
        sha256,
        detected,
        stored.created,
    )
    return IngestResult(document, duplicate=False)
