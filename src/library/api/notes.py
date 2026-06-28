"""Notes REST API (U2): in-app note authoring with in-place editing + history.

A note is a born-digital ``text/markdown`` Document (``source=note``) composed
inside Library. It is ingested through the existing born-digital text path (one
DocumentPage, no OCR/vision API call) with auto-extracted metadata, but unlike
an upload it is:

- edited *in place* (the same Document row), with a version-history snapshot
  recorded in ``note_versions`` on every edit/restore, and
- exempt from the SHA-256 content dedup (its ``sha256`` is a salted digest), so
  re-editing back to an identical body — or authoring two identical notes —
  never collides.

Auth is enforced at include level in ``app.py`` (see ``documents.py``). The
serializer and 404 helper are reused from the documents router so a note's
detail payload is shaped exactly like any other document's.
"""

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.api.documents import _detail
from library.auth.deps import current_user
from library.db import get_session
from library.jobs import extract_document, markdown_document, process_document
from library.models import Document, DocumentSource, IngestionEvent, NoteVersion, User
from library.schemas import DocumentDetail, NoteCreate, NoteUpdate, NoteVersionOut
from library.storage import path_for

router: APIRouter = APIRouter(tags=["notes"])

# The relationships ``_detail`` reads; refreshed after a commit so the response
# never triggers an (async-illegal) implicit lazy load.
_DETAIL_RELATIONSHIPS: list[str] = ["kind", "sender", "tags", "projects", "events"]


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
    """
    path = path_for(sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body_bytes)


async def _get_note_or_404(session: AsyncSession, document_id: int) -> Document:
    """The note, or 404 if it does not exist, is deleted, or is not a note."""
    document = await session.get(Document, document_id)
    is_note = document is not None and document.source == DocumentSource.NOTE
    if document is None or document.deleted_at is not None or not is_note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="note not found")
    return document


async def _next_version_no(session: AsyncSession, document_id: int) -> int:
    """The next monotonic ``version_no`` for a note's history (1-based)."""
    current_max = (
        await session.execute(
            select(func.max(NoteVersion.version_no)).where(NoteVersion.document_id == document_id)
        )
    ).scalar()
    return (current_max or 0) + 1


async def _snapshot_current(session: AsyncSession, document: Document) -> None:
    """Append the note's current (title, body) to its version history.

    The current body is the document's ``ocr_text`` — for a born-digital note
    that is the authoritative Markdown layer the OCR passthrough captured.
    """
    session.add(
        NoteVersion(
            document_id=document.id,
            version_no=await _next_version_no(session, document.id),
            title=document.title,
            body=document.ocr_text or "",
        )
    )


def _apply_body(document: Document, body_markdown: str) -> None:
    """Overwrite a note's stored body + ``ocr_text`` in place (sha stays fixed)."""
    body_bytes = body_markdown.encode("utf-8")
    _write_body(document.sha256, body_bytes)
    document.ocr_text = body_markdown


def _lock_title(document: Document, title: str) -> None:
    """Set the title and keep it in ``user_edited_fields`` (auto-extraction off)."""
    document.title = title
    edited = list(document.extra.get("user_edited_fields", []))
    if "title" not in edited:
        edited.append("title")
    document.extra = {**document.extra, "user_edited_fields": edited}


async def _reprocess_note(document_id: int) -> None:
    """Re-run the metadata + markdown (and, via markdown, embed) stages for an edit.

    Deferred after the commit: Procrastinate uses its own connection, so a job
    picked up before the edit is visible would read stale content.
    """
    await extract_document.defer_async(document_id=document_id)
    await markdown_document.defer_async(document_id=document_id)


@router.post(
    "/notes",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Author a new note",
)
async def create_note(
    payload: NoteCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> DocumentDetail:
    """Create a born-digital Markdown note and queue the processing pipeline.

    The title is locked against re-extraction; the body is written under a
    salted sha (dedup bypass). Auto-extraction still fills summary/topics/tags/
    kind from the body.
    """
    body_bytes = payload.body_markdown.encode("utf-8")
    sha256 = _salted_sha256(body_bytes)
    _write_body(sha256, body_bytes)

    document = Document(
        sha256=sha256,
        mime_type="text/markdown",
        source=DocumentSource.NOTE,
        title=payload.title,
        original_filename=f"{payload.title}.md",
        uploader_id=user.id,
        extra={"user_edited_fields": ["title"]},
    )
    session.add(document)
    await session.flush()
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="received",
            detail={"source": DocumentSource.NOTE.value, "size": len(body_bytes)},
        )
    )
    # Commit before deferring: the worker defers over its own connection.
    await session.commit()
    await process_document.defer_async(document_id=document.id)
    await session.refresh(document, _DETAIL_RELATIONSHIPS)
    return _detail(document)


@router.patch(
    "/notes/{document_id}",
    response_model=DocumentDetail,
    summary="Edit a note in place",
    responses={404: {"description": "Unknown, deleted, or non-note document"}},
)
async def update_note(
    document_id: int,
    payload: NoteUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Snapshot the current (title, body) into history, then apply the edit.

    Only fields present in the body change. A body edit rewrites the stored file
    and ``ocr_text`` and re-queues extraction + markdown (markdown re-embeds).
    """
    document = await _get_note_or_404(session, document_id)
    provided = payload.model_dump(exclude_unset=True)

    await _snapshot_current(session, document)

    body_changed = False
    if provided.get("title") is not None:
        _lock_title(document, provided["title"])
    if provided.get("body_markdown") is not None:
        _apply_body(document, provided["body_markdown"])
        body_changed = True

    session.add(
        IngestionEvent(
            document_id=document.id,
            event="note_edited",
            detail={"fields": sorted(provided)},
        )
    )
    await session.commit()
    if body_changed:
        await _reprocess_note(document.id)
    await session.refresh(document, _DETAIL_RELATIONSHIPS)
    return _detail(document)


@router.get(
    "/notes/{document_id}/versions",
    response_model=list[NoteVersionOut],
    summary="A note's version history (newest first)",
    responses={404: {"description": "Unknown, deleted, or non-note document"}},
)
async def list_note_versions(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[NoteVersionOut]:
    """Every prior (title, body) snapshot for the note, newest first."""
    await _get_note_or_404(session, document_id)
    rows = (
        (
            await session.execute(
                select(NoteVersion)
                .where(NoteVersion.document_id == document_id)
                .order_by(NoteVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        NoteVersionOut(
            version_no=row.version_no,
            title=row.title,
            body=row.body,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/notes/{document_id}/versions/{version_no}/restore",
    response_model=DocumentDetail,
    summary="Restore a note to a previous version",
    responses={404: {"description": "Unknown note, or unknown version number"}},
)
async def restore_note_version(
    document_id: int,
    version_no: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Snapshot the current state, then re-apply the chosen version's content.

    The restore is itself an edit: the current (title, body) is snapshotted as a
    new version first, so a restore can always be undone.
    """
    document = await _get_note_or_404(session, document_id)
    target = (
        await session.execute(
            select(NoteVersion).where(
                NoteVersion.document_id == document_id,
                NoteVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="note version not found")

    await _snapshot_current(session, document)
    _lock_title(document, target.title or "")
    _apply_body(document, target.body)
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="note_restored",
            detail={
                "restored_version_no": version_no,
                "restored_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    await session.commit()
    await _reprocess_note(document.id)
    await session.refresh(document, _DETAIL_RELATIONSHIPS)
    return _detail(document)
