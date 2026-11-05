"""Authoring notes, shared by every surface that creates or rewrites one.

A note is a born-digital ``text/markdown`` Document (``source=note``) composed
inside Library rather than uploaded. ``POST /api/notes`` and the MCP
``create_note`` tool both call ``create_note``, so the stored file, the
reader's page, the full-text mirror, the audit event and the queued pipeline
happen the same way whoever writes the note. The notes router's edit and
restore routes reuse ``apply_body`` and ``lock_title``.

This module does not speak HTTP or MCP: it has no refusals of its own (the
callers validate the title and body), and it commits.
"""

import hashlib
import os
import tempfile
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from library.jobs import process_document
from library.markdown.apply import document_markdown_text
from library.models import Document, DocumentPage, DocumentSource, IngestionEvent
from library.storage import path_for


def _salted_sha256(body_bytes: bytes) -> str:
    """A per-note salted digest: structurally bypasses content dedup.

    Notes are never looked up by content sha, so salting with a random nonce
    lets identical (or edited-back-to-identical) bodies coexist as distinct
    notes. Computed once at creation and fixed for the note's life.
    """
    return hashlib.sha256(body_bytes + uuid4().hex.encode()).hexdigest()


def _write_body(sha256: str, body_bytes: bytes) -> None:
    """Write a note's body to its content-addressed original path.

    Bypasses ``storage.store`` deliberately: ``store`` re-hashes the content and
    would file it under a different name, breaking the fixed salted-sha identity.
    The born-digital OCR passthrough reads exactly this file.

    The write is atomic (temp file in the target dir + ``os.replace``), mirroring
    ``storage.store``: an edit/restore overwrites the note's only file in place,
    so a crash mid-write must never truncate or corrupt the existing content.
    """
    path = path_for(sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{sha256}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body_bytes)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


async def apply_body(session: AsyncSession, document: Document, body_markdown: str) -> None:
    """Materialize a note's body in place: stored file, ``ocr_text``, the
    single born-digital ``DocumentPage`` the reader renders, and the
    ``pages_markdown`` mirror full-text search indexes.

    Born-digital text needs no OCR/vision call, so the displayable layer is
    written **synchronously** here rather than waiting for the async pipeline —
    the reader and search are correct the instant the caller commits, even if
    the worker is backed up or down. The deferred pipeline still re-runs
    extraction (metadata) and embedding (search) idempotently. The sha stays
    fixed; the file is overwritten atomically.
    """
    body_bytes = body_markdown.encode("utf-8")
    _write_body(document.sha256, body_bytes)
    document.ocr_text = body_markdown
    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    body = body_markdown.strip()
    if body:
        session.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                markdown=body,
                char_count=len(body),
            )
        )
        document.page_count = 1
        document.pages_markdown = document_markdown_text([body])
    else:
        document.page_count = None
        document.pages_markdown = None


def lock_title(document: Document, title: str) -> None:
    """Set the title and keep it in ``user_edited_fields`` (auto-extraction off)."""
    document.title = title
    edited = list(document.extra.get("user_edited_fields", []))
    if "title" not in edited:
        edited.append("title")
    document.extra = {**document.extra, "user_edited_fields": edited}


class NoteNotQueued(RuntimeError):
    """The note was committed, but queueing its processing pipeline failed.

    Carries the saved ``document``: a caller that can still answer (MCP)
    reports the note with a warning, because a bare error invites a retry
    and every retry files another note. The REST route lets it propagate.
    """

    def __init__(self, document: Document) -> None:
        super().__init__(f"note {document.id} was saved but its processing was not queued")
        self.document = document


async def create_note(
    session: AsyncSession, *, title: str, body_markdown: str, uploader_id: int | None
) -> Document:
    """Create a born-digital Markdown note by ``uploader_id``, commit, and
    queue the processing pipeline.

    The title is locked against re-extraction; the body is written under a
    salted sha (dedup bypass), so every call makes a new note. Auto-extraction
    still fills summary/topics/tags/kind from the body, and the pipeline embeds
    it. Returns the committed document; raises ``NoteNotQueued`` (carrying
    it) when the note was committed but the pipeline could not be queued.
    """
    body_bytes = body_markdown.encode("utf-8")
    document = Document(
        sha256=_salted_sha256(body_bytes),
        mime_type="text/markdown",
        source=DocumentSource.NOTE,
        title=title,
        original_filename=f"{title}.md",
        uploader_id=uploader_id,
        extra={"user_edited_fields": ["title"]},
    )
    session.add(document)
    await session.flush()
    # Materialize the body synchronously (file + ocr_text + DocumentPage +
    # pages_markdown) so the reader and search are correct immediately; the
    # deferred pipeline still adds metadata, markdown re-render, and embeddings.
    await apply_body(session, document, body_markdown)
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="received",
            detail={"source": DocumentSource.NOTE.value, "size": len(body_bytes)},
        )
    )
    # Commit before deferring: the worker defers over its own connection.
    await session.commit()
    try:
        await process_document.defer_async(document_id=document.id)
    except Exception as exc:
        raise NoteNotQueued(document) from exc
    return document
