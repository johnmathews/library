"""Documents REST API: upload, list/search, detail, edit, delete, files.

Authentication is enforced at include level in app.py (session cookie or
bearer token); see docs/api.md §1.9.

Search semantics live in ``library.search`` (shared with the MCP server);
see docs/api.md §1.3.3 for the user-facing description.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.extraction.apply import get_or_create_tag, upsert_sender
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.jobs import extract_document
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    Kind,
    User,
)
from library.ocr.tesseract import SEARCHABLE_PDF_NAME
from library.schemas import (
    DocumentDetail,
    DocumentListItem,
    DocumentListResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    ExtractionQueuedResponse,
    IngestionEventOut,
    KindOut,
    SenderOut,
    TagOut,
)
from library.search import DocumentFilters, build_document_query
from library.storage import derived_path, path_for
from library.thumbnails import THUMBNAIL_NAME

router: APIRouter = APIRouter(tags=["documents"])

# PATCH body field -> name recorded in extra["user_edited_fields"] (the
# storage-level names the W6 extraction contract checks).
_EDITED_FIELD_NAMES: dict[str, str] = {"kind_slug": "kind_id", "sender": "sender_id"}

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
    tag: Annotated[
        list[str] | None,
        Query(description="Tag slug; repeat the parameter to require all of them (AND)."),
    ] = None,
    language: Annotated[DocumentLanguage | None, Query()] = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    date_from: Annotated[
        date | None, Query(description="Inclusive lower bound on document_date.")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Inclusive upper bound on document_date.")
    ] = None,
    source: Annotated[DocumentSource | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    """Paginated document list; all filters AND-compose, including with `q`.

    Without `q`, results are ordered by document_date (newest first, unknown
    dates last), then created_at. With `q`, by search rank.
    """
    query = build_document_query(
        q,
        DocumentFilters(
            kind_slug=kind,
            sender_id=sender_id,
            tag_slugs=tuple(tag or []),
            language=language,
            status=status_filter,
            date_from=date_from,
            date_to=date_to,
            source=source,
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
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="Document detail",
    responses={404: {"description": "Unknown or deleted document"}},
)
async def get_document(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetail:
    """Full metadata, OCR text, extraction provenance, and the audit trail."""
    document = await _get_document_or_404(session, document_id)
    return _detail(document)


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
    provided = payload.model_dump(exclude_unset=True)
    if not provided:
        return _detail(document)

    edited: list[str] = []
    if "kind_slug" in provided:
        slug = provided.pop("kind_slug")
        if slug is None:
            document.kind_id = None
        else:
            kind = (
                await session.execute(select(Kind).where(Kind.slug == slug))
            ).scalar_one_or_none()
            if kind is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"unknown kind slug: {slug!r}",
                )
            document.kind_id = kind.id
        edited.append(_EDITED_FIELD_NAMES["kind_slug"])
    if "sender" in provided:
        name = provided.pop("sender")
        document.sender_id = None if name is None else (await upsert_sender(session, name)).id
        edited.append(_EDITED_FIELD_NAMES["sender"])
    if "tags" in provided:
        slugs = provided.pop("tags")
        if slugs is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="tags cannot be null; send [] to clear them",
            )
        document.tags = [await get_or_create_tag(session, slug) for slug in dict.fromkeys(slugs)]
        edited.append("tags")
    if provided.get("language", "") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="language cannot be null",
        )
    for field, value in provided.items():
        setattr(document, field, value)
        edited.append(field)

    user_edited = list(document.extra.get("user_edited_fields", []))
    user_edited.extend(name for name in edited if name not in user_edited)
    document.extra = {**document.extra, "user_edited_fields": user_edited}
    session.add(
        IngestionEvent(document_id=document.id, event="user_edited", detail={"fields": edited})
    )
    await session.commit()
    await session.refresh(document, ["kind", "sender", "tags", "events"])
    return _detail(document)


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

    Deleted documents 404 on every endpoint. Restore is out of scope for
    now (clear ``deleted_at`` in the database).
    """
    document = await _get_document_or_404(session, document_id)
    document.deleted_at = datetime.now(UTC)
    session.add(IngestionEvent(document_id=document.id, event="deleted", detail={}))
    await session.commit()


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
) -> FileResponse:
    """The stored original, with its real content type and original filename.

    ``?disposition=inline`` serves ``Content-Disposition: inline`` (keeping
    the filename) so the detail page can embed it; the default stays
    ``attachment`` for download links.
    """
    document = await _get_document_or_404(session, document_id)
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
) -> FileResponse:
    """The OCR-produced searchable PDF; 404 when the document has none.

    ``?disposition=inline`` serves it for in-browser viewing (the detail
    page's iframe preview); the default stays ``attachment``.
    """
    document = await _get_document_or_404(session, document_id)
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
) -> FileResponse:
    """The ~480px-wide first-page WebP rendered by the background worker."""
    document = await _get_document_or_404(session, document_id)
    path = derived_path(document.sha256) / THUMBNAIL_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no thumbnail for this document"
        )
    return FileResponse(path, media_type="image/webp")


async def _get_document_or_404(session: AsyncSession, document_id: int) -> Document:
    """The document, or 404 if it does not exist or is soft-deleted."""
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


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
        "tags": [
            TagOut(slug=tag.slug, name=tag.name)
            for tag in sorted(document.tags, key=lambda tag: tag.slug)
        ],
        "document_date": document.document_date,
        "language": document.language,
        "status": document.status,
        "mime_type": document.mime_type,
        "page_count": document.page_count,
        "created_at": document.created_at,
        "has_searchable_pdf": document.searchable_pdf,
        "has_thumbnail": (derived_path(document.sha256) / THUMBNAIL_NAME).is_file(),
    }


def _detail(document: Document) -> DocumentDetail:
    """Build the detail response; exposes extraction provenance, not raw extra."""
    return DocumentDetail(
        **_list_item_fields(document),
        ocr_text=document.ocr_text,
        ocr_confidence=document.ocr_confidence,
        amount_total=document.amount_total,
        currency=document.currency,
        due_date=document.due_date,
        expiry_date=document.expiry_date,
        source=document.source,
        original_filename=document.original_filename,
        sha256=document.sha256,
        extraction=document.extra.get("extraction"),
        user_edited_fields=list(document.extra.get("user_edited_fields", [])),
        events=[
            IngestionEventOut(event=event.event, detail=event.detail, created_at=event.created_at)
            for event in sorted(document.events, key=lambda event: event.id)
        ],
    )
