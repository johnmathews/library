"""Documents REST API: upload, list/search, detail, edit, delete, files.

Authentication is enforced at include level in app.py (session cookie or
bearer token); see docs/api.md §1.9.

Search semantics live in ``library.search`` (shared with the MCP server);
see docs/api.md §1.3.3 for the user-facing description.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.documents_service import apply_document_update, revalidate_after_edit
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.jobs import extract_document
from library.models import (
    Document,
    DocumentComment,
    DocumentLanguage,
    DocumentPage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    ReviewStatus,
    User,
)
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.schemas import (
    CommentOut,
    DeletedDocumentItem,
    DeletedDocumentListResponse,
    DocumentDetail,
    DocumentListItem,
    DocumentListResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    ExtractionQueuedResponse,
    IngestionEventOut,
    KindOut,
    MarkdownPage,
    MarkdownResponse,
    ProjectRef,
    RecipientOut,
    SenderOut,
    TagOut,
    ValidationFindingSummary,
)
from library.search import (
    DEFAULT_DOCUMENT_SORT,
    DEFAULT_SORT_DIRECTION,
    DocumentFilters,
    DocumentSort,
    SortDirection,
    build_document_query,
)
from library.series import serialise_summary, summarize_series
from library.storage import derived_path, path_for
from library.storage import remove as remove_stored_files
from library.thumbnails import THUMBNAIL_NAME

router: APIRouter = APIRouter(tags=["documents"])


# ?disposition= on the file endpoints: `attachment` (default) downloads,
# `inline` lets the detail page embed the file in an <iframe>/<img>.
# Anything else fails validation with a 422.
DispositionParam = Annotated[
    Literal["inline", "attachment"],
    Query(description="`inline` to render in the browser, `attachment` (default) to download."),
]

# Defense in depth for inline rendering: only types a browser displays
# without executing anything. The ingest allowlist already keeps active
# content (HTML/SVG/XML) out of the store, but the XSS boundary must not
# depend on a single upstream check — anything outside this set is
# silently downgraded to attachment.
INLINE_SAFE_MIME_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}
)


def _file_response(path: Path, *, media_type: str, filename: str, disposition: str) -> FileResponse:
    """FileResponse with the inline allowlist and hardening headers applied."""
    if disposition == "inline" and media_type not in INLINE_SAFE_MIME_TYPES:
        disposition = "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
        headers={
            "X-Content-Type-Options": "nosniff",
            # Even if something renderable slipped through, it executes
            # nothing and has no origin powers inside the preview iframe.
            "Content-Security-Policy": "sandbox",
        },
    )


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    responses={
        200: {"description": "Duplicate of an existing document (no resource created)"},
        409: {"description": "Content matches a soft-deleted document"},
        413: {"description": "File exceeds the upload size limit"},
        415: {"description": "Unsupported media type"},
    },
)
async def upload_document(
    file: UploadFile,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> DocumentUploadResponse:
    """Ingest one uploaded file; 201 for a new document, 200 for a duplicate."""
    max_bytes = get_settings().max_upload_bytes
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"file exceeds the {max_bytes} byte upload limit",
        )

    try:
        result = await ingest_file(
            session,
            content=content,
            filename=file.filename,
            mime=file.content_type,
            source=DocumentSource.UPLOAD,
            uploader_id=user.id,
        )
    except UnsupportedMimeTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except DeletedDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if result.duplicate:
        # 200, not 201: no resource was created; the body points at the
        # existing document.
        response.status_code = status.HTTP_200_OK
    return DocumentUploadResponse(
        id=result.document.id,
        sha256=result.document.sha256,
        status=result.document.status,
        duplicate=result.duplicate,
    )


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List and search documents",
)
async def list_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[
        str | None,
        Query(
            description=(
                "Full-text search in websearch syntax (quoted phrases, OR, "
                "-exclusion), stemmed in both Dutch and English. Adds "
                "`snippet` and `rank` to each item and orders by rank."
            ),
            openapi_examples={
                "dutch_stem": {
                    "summary": "Dutch stemming",
                    "description": "Finds documents containing 'rekeningen'.",
                    "value": "rekening",
                },
                "phrase": {
                    "summary": "Exact phrase",
                    "value": '"energierekening mei"',
                },
                "boolean": {
                    "summary": "Either term, excluding one",
                    "value": "factuur OR invoice -concept",
                },
            },
        ),
    ] = None,
    kind: Annotated[str | None, Query(description="Kind slug, e.g. `invoice`.")] = None,
    sender_id: Annotated[int | None, Query()] = None,
    recipient_id: Annotated[int | None, Query()] = None,
    tag: Annotated[
        list[str] | None,
        Query(description="Tag slug; repeat the parameter to require all of them (AND)."),
    ] = None,
    project: Annotated[
        list[str] | None,
        Query(description="Project slug; repeat the parameter for OR (documents in any of them)."),
    ] = None,
    language: Annotated[DocumentLanguage | None, Query()] = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    date_from: Annotated[
        date | None, Query(description="Inclusive lower bound on document_date.")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Inclusive upper bound on document_date.")
    ] = None,
    source: Annotated[DocumentSource | None, Query()] = None,
    sort: Annotated[
        DocumentSort,
        Query(description="Order field for the non-search list. Ignored when `q` is set."),
    ] = DEFAULT_DOCUMENT_SORT,
    direction: Annotated[
        SortDirection,
        Query(description="Order direction for `sort`. Ignored when `q` is set."),
    ] = DEFAULT_SORT_DIRECTION,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    """Paginated document list; all filters AND-compose, including with `q`.

    Without `q`, results are ordered by `sort`/`direction` (default document_date
    newest first, unknown dates last, then created_at). With `q`, by search rank
    (`sort`/`direction` are ignored).
    """
    query = build_document_query(
        q,
        DocumentFilters(
            kind_slug=kind,
            sender_id=sender_id,
            recipient_id=recipient_id,
            tag_slugs=tuple(tag or []),
            project_slugs=tuple(project or []),
            language=language,
            status=status_filter,
            review_status=review_status,
            date_from=date_from,
            date_to=date_to,
            source=source,
            sort=sort,
            direction=direction,
        ),
    )

    total = (await session.execute(query.count)).scalar_one()
    result = await session.execute(query.statement.limit(limit).offset(offset))

    if query.has_rank:
        items = [
            DocumentListItem(**_list_item_fields(document), snippet=snippet_value, rank=rank_value)
            for document, rank_value, snippet_value in result.all()
        ]
    else:
        items = [
            DocumentListItem(**_list_item_fields(document)) for document in result.scalars().all()
        ]
    return DocumentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/documents/deleted",
    response_model=DeletedDocumentListResponse,
    summary="List soft-deleted documents (Recently Deleted)",
)
async def list_deleted_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeletedDocumentListResponse:
    """The Recently-Deleted holding area: documents with ``deleted_at`` set,
    newest-deleted first, each annotated with when it will be purged.

    This deliberately *inverts* the ``deleted_at IS NULL`` predicate every other
    read path applies, so it does not reuse the shared search filter.
    """
    retention_days = get_settings().deleted_retention_days
    now = datetime.now(UTC)

    condition = Document.deleted_at.is_not(None)
    total = (
        await session.execute(select(func.count()).select_from(Document).where(condition))
    ).scalar_one()
    result = await session.execute(
        select(Document)
        .where(condition)
        .order_by(Document.deleted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        _deleted_item(document, retention_days=retention_days, now=now)
        for document in result.scalars().all()
    ]
    return DeletedDocumentListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        retention_days=retention_days,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="Document detail",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def get_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    include_deleted: Annotated[bool, Query()] = False,
) -> DocumentDetail:
    """Full metadata, OCR text, extraction provenance, and the audit trail.

    ``include_deleted=true`` opts into returning a soft-deleted document (with
    ``deleted_at`` set) instead of 404ing it — the Recently-Deleted view uses
    this to open a trashed document read-only. The default keeps the invariant
    every list/search path relies on: deleted documents 404.
    """
    document = await _get_document_or_404(session, document_id, include_deleted=include_deleted)
    return await _detail(session, document)


@router.get(
    "/documents/{document_id}/markdown",
    response_model=MarkdownResponse,
    summary="Per-page markdown rendering of a document",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def get_document_markdown(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    include_deleted: Annotated[bool, Query()] = False,
) -> MarkdownResponse:
    """Assembled per-page markdown ordered by page number; empty when the document has none.

    ``include_deleted=true`` renders a soft-deleted document's text so the
    read-only Recently-Deleted detail view is not blank (see ``get_document``).
    """
    await _get_document_or_404(session, document_id, include_deleted=include_deleted)
    rows = (
        (
            await session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            )
        )
        .scalars()
        .all()
    )
    pages = [MarkdownPage(page_number=row.page_number, markdown=row.markdown) for row in rows]
    return MarkdownResponse(page_count=len(pages), pages=pages)


@router.get(
    "/documents/{document_id}/series",
    summary="Recurring-series stats + comparison for this document",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def get_document_series(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Summarise the (sender, kind) series this document belongs to and where
    this document sits within it. ``status:"insufficient"`` when the document
    has no sender/kind or too few siblings."""
    document = await _get_document_or_404(session, document_id)
    settings = get_settings()
    if document.sender_id is None or document.kind_id is None:
        return {"status": "insufficient", "count": 0, "document_ids": [document_id]}
    filters = DocumentFilters(
        sender_id=document.sender_id,
        kind_slug=document.kind.slug if document.kind else None,
    )
    summary = await summarize_series(
        session,
        filters=filters,
        settings=settings,
        reference=document.amount_total,
        reference_date=document.document_date,
        reference_currency=document.currency,
    )
    return serialise_summary(summary, include_points=True)


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="Edit document metadata",
    responses={
        404: {"description": "Unknown or deleted document"},
        422: {"description": "Unknown kind slug or invalid field value"},
    },
)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Apply a partial metadata edit; only fields present in the body change.

    Edited fields are appended to ``extra["user_edited_fields"]`` so
    re-extraction never overwrites them, and a ``user_edited`` ingestion
    event records the change.
    """
    document = await _get_document_or_404(session, document_id)
    edited = await apply_document_update(session, document, payload, edited_by="user")
    if not edited:
        return await _detail(session, document)
    # A corrected field can resolve (or introduce) a validation finding, so
    # re-run the deterministic rules and update review_status before committing —
    # this is what clears a fixed "implausible date" warning on save.
    await revalidate_after_edit(session, document, get_settings())
    await session.commit()
    # updated_at has a SQL onupdate (func.now()); SQLAlchemy expires it after the
    # UPDATE since it can't know the server-computed value — refresh it eagerly
    # here so _detail() doesn't trigger a lazy load of an already-expired attribute.
    await session.refresh(
        document, ["kind", "sender", "recipient", "tags", "projects", "events", "updated_at"]
    )
    return await _detail(session, document)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a document",
    responses={404: {"description": "Unknown or already deleted document"}},
)
async def delete_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Set ``deleted_at`` and record a ``deleted`` event; the row and file stay.

    Deleted documents 404 on every read endpoint and drop out of every list and
    search. They surface only in GET /api/documents/deleted until either restored
    (POST .../restore) or hard-deleted by the daily purge after the retention
    window.
    """
    document = await _get_document_or_404(session, document_id)
    document.deleted_at = datetime.now(UTC)
    session.add(IngestionEvent(document_id=document.id, event="deleted", detail={}))
    await session.commit()


@router.post(
    "/documents/{document_id}/restore",
    response_model=DocumentDetail,
    summary="Restore a soft-deleted document",
    responses={404: {"description": "Unknown or not-currently-deleted document"}},
)
async def restore_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Clear ``deleted_at`` so the document reappears everywhere, and record a
    ``restored`` event.

    404s unless the document exists and is *currently* soft-deleted — restoring a
    live document is a no-op the caller should treat as an error. Restore never
    collides with a re-upload: uploading content matching a soft-deleted document
    is rejected with 409 (``DeletedDuplicateError``), so at most one row ever
    holds a given sha256 and there is nothing to reconcile.
    """
    document = await _get_deleted_document_or_404(session, document_id)
    document.deleted_at = None
    session.add(IngestionEvent(document_id=document.id, event="restored", detail={}))
    await session.commit()
    # updated_at has a SQL onupdate (func.now()); expired after the UPDATE, so
    # refresh it (and the events relationship) before _detail reads them.
    await session.refresh(document, ["events", "updated_at"])
    return await _detail(session, document)


@router.delete(
    "/documents/{document_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete a soft-deleted document",
    responses={404: {"description": "Unknown or not-currently-deleted document"}},
)
async def purge_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Hard-delete a document that is already in the trash: drop its row and
    unlink its stored files, the on-demand equivalent of the daily purge job.

    404s unless the document exists and is *currently* soft-deleted — you must
    soft-delete first, so this can never one-step nuke a live document (mirrors
    restore's guard). Chunks, comments, pages, events, note versions, and
    series/tag/project links cascade at the DB level. The row is committed gone
    *before* files are unlinked, so an unlink failure leaves at worst an orphaned
    file (harmless, reclaimable) rather than a live row whose file has vanished —
    identical ordering to ``purge_deleted_documents`` in jobs.py.
    """
    document = await _get_deleted_document_or_404(session, document_id)
    sha256 = document.sha256
    # Core bulk delete (not session.delete) so children cascade at the DB level,
    # exactly like the purge job — and so it never triggers an ORM lazy-load of a
    # lazy="raise" relationship (e.g. comments) on the way out.
    await session.execute(delete(Document).where(Document.id == document.id))
    await session.commit()
    remove_stored_files(sha256)


@router.post(
    "/documents/{document_id}/extract",
    response_model=ExtractionQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue metadata re-extraction",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def queue_extraction(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionQueuedResponse:
    """Defer the W6 extraction task for this document and return immediately.

    Works on documents in any state (including already indexed); the run
    honours ``extra["user_edited_fields"]`` and never removes tags. Track
    the outcome via the document's ``extraction`` provenance and audit
    events (GET detail) or GET /api/jobs.
    """
    document = await _get_document_or_404(session, document_id)
    job_id = await extract_document.defer_async(document_id=document.id)
    return ExtractionQueuedResponse(queued=True, job_id=job_id)


@router.post(
    "/documents/{document_id}/verify",
    response_model=DocumentDetail,
    summary="Mark a document's metadata as reviewed/verified",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def verify_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Set review_status=verified and record an audit event."""
    document = await _get_document_or_404(session, document_id)
    document.review_status = ReviewStatus.VERIFIED
    session.add(IngestionEvent(document_id=document.id, event="review_verified", detail={}))
    await session.commit()
    await session.refresh(document)
    return await _detail(session, document)


@router.get(
    "/documents/{document_id}/original",
    response_class=FileResponse,
    summary="Download the original file",
    responses={404: {"description": "Unknown or deleted document, or file missing"}},
)
async def download_original(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    disposition: DispositionParam = "attachment",
    include_deleted: Annotated[bool, Query()] = False,
) -> FileResponse:
    """The stored original, with its real content type and original filename.

    ``?disposition=inline`` serves ``Content-Disposition: inline`` (keeping
    the filename) so the detail page can embed it; the default stays
    ``attachment`` for download links. ``include_deleted=true`` serves a
    soft-deleted document's file so the read-only trash view can preview it.
    """
    document = await _get_document_or_404(session, document_id, include_deleted=include_deleted)
    path = path_for(document.sha256)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="original file missing from storage"
        )
    return _file_response(
        path,
        media_type=document.mime_type,
        filename=document.original_filename or f"document-{document.id}",
        disposition=disposition,
    )


@router.get(
    "/documents/{document_id}/searchable.pdf",
    response_class=FileResponse,
    summary="Download the searchable PDF",
    responses={404: {"description": "No searchable PDF for this document"}},
)
async def download_searchable_pdf(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    disposition: DispositionParam = "attachment",
    include_deleted: Annotated[bool, Query()] = False,
) -> FileResponse:
    """The OCR-produced searchable PDF; 404 when the document has none.

    ``?disposition=inline`` serves it for in-browser viewing (the detail
    page's iframe preview); the default stays ``attachment``.
    ``include_deleted=true`` serves it for a soft-deleted document (trash view).
    """
    document = await _get_document_or_404(session, document_id, include_deleted=include_deleted)
    path = derived_path(document.sha256) / SEARCHABLE_PDF_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no searchable PDF for this document"
        )
    stem = (
        Path(document.original_filename).stem
        if document.original_filename
        else f"document-{document.id}"
    )
    return _file_response(
        path,
        media_type="application/pdf",
        filename=f"{stem}-searchable.pdf",
        disposition=disposition,
    )


@router.get(
    "/documents/{document_id}/thumbnail",
    response_class=FileResponse,
    summary="First-page thumbnail (WebP)",
    responses={404: {"description": "No thumbnail (not generated yet, or text-only)"}},
)
async def get_thumbnail(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    include_deleted: Annotated[bool, Query()] = False,
) -> FileResponse:
    """The ~480px-wide first-page WebP rendered by the background worker.

    ``include_deleted=true`` serves a soft-deleted document's thumbnail so the
    read-only trash view can show its poster image."""
    document = await _get_document_or_404(session, document_id, include_deleted=include_deleted)
    path = derived_path(document.sha256) / THUMBNAIL_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no thumbnail for this document"
        )
    return FileResponse(path, media_type="image/webp")


async def _get_document_or_404(
    session: AsyncSession, document_id: int, *, include_deleted: bool = False
) -> Document:
    """The document, or 404 if it does not exist.

    Soft-deleted documents also 404 unless ``include_deleted`` is set — the
    opt-in used by the Recently-Deleted read path to view a trashed document.
    """
    document = await session.get(Document, document_id)
    if document is None or (document.deleted_at is not None and not include_deleted):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


async def _get_deleted_document_or_404(session: AsyncSession, document_id: int) -> Document:
    """The document, or 404 unless it exists and is *currently* soft-deleted.

    The inverse of ``_get_document_or_404``: used by restore, where a live (or
    unknown) document is not a valid target.
    """
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="deleted document not found"
        )
    return document


def _deleted_item(document: Document, *, retention_days: int, now: datetime) -> DeletedDocumentItem:
    """A Recently-Deleted row: the list fields plus purge timing. ``deleted_at``
    is never None here (callers filter ``deleted_at IS NOT NULL``)."""
    assert document.deleted_at is not None  # guaranteed by the query predicate
    purge_at = document.deleted_at + timedelta(days=retention_days)
    days_remaining = max(0, (purge_at - now).days)
    return DeletedDocumentItem(
        **_list_item_fields(document),
        deleted_at=document.deleted_at,
        purge_at=purge_at,
        days_remaining=days_remaining,
    )


def _list_item_fields(document: Document) -> dict[str, Any]:
    """The DocumentListItem field values shared by list and detail responses."""
    return {
        "id": document.id,
        "title": document.title,
        "summary": document.summary,
        "kind": (
            KindOut(slug=document.kind.slug, name=document.kind.name) if document.kind else None
        ),
        "sender": (
            SenderOut(id=document.sender.id, name=document.sender.name) if document.sender else None
        ),
        "recipient": (
            RecipientOut(id=document.recipient.id, name=document.recipient.name)
            if document.recipient
            else None
        ),
        "tags": [
            TagOut(slug=tag.slug, name=tag.name)
            for tag in sorted(document.tags, key=lambda tag: tag.slug)
        ],
        "topics": list(document.topics or []),
        "projects": [
            ProjectRef(slug=project.slug, name=project.name)
            for project in sorted(document.projects, key=lambda project: project.slug)
        ],
        "document_date": document.document_date,
        "due_date": document.due_date,
        "expiry_date": document.expiry_date,
        "language": document.language,
        "status": document.status,
        "mime_type": document.mime_type,
        "page_count": document.page_count,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "has_searchable_pdf": document.searchable_pdf,
        "has_thumbnail": (derived_path(document.sha256) / THUMBNAIL_NAME).is_file(),
        "review_status": document.review_status,
        "review_findings": _review_findings(document),
        "amount_total": document.amount_total,
        "currency": document.currency,
    }


def _review_findings(document: Document) -> list[ValidationFindingSummary]:
    """Compact validation findings for list rows — only when the document still
    needs review, so clean rows stay lean."""
    if document.review_status != ReviewStatus.NEEDS_REVIEW:
        return []
    validation = document.extra.get("validation") if isinstance(document.extra, dict) else None
    findings = validation.get("findings", []) if isinstance(validation, dict) else []
    return [
        ValidationFindingSummary(
            rule=finding.get("rule", ""),
            field=finding.get("field"),
            message=finding.get("message", ""),
        )
        for finding in findings
        if isinstance(finding, dict)
    ]


async def _detail(session: AsyncSession, document: Document) -> DocumentDetail:
    """Build the detail response; exposes extraction provenance, not raw extra.

    Comments are queried explicitly (``Document.comments`` is ``lazy="raise"``,
    since a normal document load never wants them) rather than eager-loaded on
    every read of ``Document`` — this is the one place the detail payload needs
    them.
    """
    comments = (
        (
            await session.execute(
                select(DocumentComment)
                .where(DocumentComment.document_id == document.id)
                .order_by(DocumentComment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return DocumentDetail(
        **_list_item_fields(document),
        deleted_at=document.deleted_at,
        ocr_text=document.ocr_text,
        ocr_confidence=document.ocr_confidence,
        source=document.source,
        original_filename=document.original_filename,
        sha256=document.sha256,
        extraction=document.extra.get("extraction"),
        user_edited_fields=list(document.extra.get("user_edited_fields", [])),
        validation=document.extra.get("validation"),
        events=[
            IngestionEventOut(event=event.event, detail=event.detail, created_at=event.created_at)
            for event in sorted(document.events, key=lambda event: event.id)
        ],
        comments=[CommentOut.model_validate(comment) for comment in comments],
    )
