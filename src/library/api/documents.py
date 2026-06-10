"""Document upload endpoint.

NO AUTHENTICATION YET — auth arrives in W8. Do not expose beyond a trusted
network until then.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import get_settings
from library.db import get_session
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.models import DocumentSource
from library.schemas import DocumentUploadResponse

router: APIRouter = APIRouter(tags=["documents"])


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
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
