"""Comment CRUD API (U3): free-text, dated notes attached to an existing document.

A comment annotates another document (unlike a note, which is its own
``source=note`` Document — see ``notes.py``). Every create/edit/delete writes
an ``IngestionEvent`` on the parent document and defers a re-embed so the
comment's text becomes searchable from /ask (see ``jobs.embed_document`` and
``docs/api.md``).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.jobs import embed_document
from library.models import Document, DocumentComment, IngestionEvent, User
from library.schemas import CommentIn, CommentOut

router: APIRouter = APIRouter(prefix="/documents/{document_id}/comments", tags=["comments"])


async def _get_document_or_404(session: AsyncSession, document_id: int) -> Document:
    """The document, or 404 if it does not exist or is deleted."""
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


async def _get_comment_or_404(
    session: AsyncSession, document_id: int, comment_id: int
) -> DocumentComment:
    """The comment scoped to its document, or 404 if either is unknown."""
    comment = await session.get(DocumentComment, comment_id)
    if comment is None or comment.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="comment not found")
    return comment


@router.get("", response_model=list[CommentOut], summary="List a document's comments")
async def list_comments(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CommentOut]:
    """A document's comments, newest first."""
    await _get_document_or_404(session, document_id)
    rows = (
        (
            await session.execute(
                select(DocumentComment)
                .where(DocumentComment.document_id == document_id)
                .order_by(DocumentComment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [CommentOut.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a document",
)
async def create_comment(
    document_id: int,
    payload: CommentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> CommentOut:
    """Add a comment and queue a re-embed so it becomes searchable from /ask."""
    await _get_document_or_404(session, document_id)
    comment = DocumentComment(document_id=document_id, author_id=user.id, body=payload.body)
    session.add(comment)
    await session.flush()
    session.add(
        IngestionEvent(
            document_id=document_id, event="comment_added", detail={"comment_id": comment.id}
        )
    )
    await session.commit()
    await session.refresh(comment)
    await embed_document.defer_async(document_id=document_id)
    return CommentOut.model_validate(comment)


@router.patch(
    "/{comment_id}",
    response_model=CommentOut,
    summary="Edit a comment",
    responses={404: {"description": "Unknown or foreign comment"}},
)
async def update_comment(
    document_id: int,
    comment_id: int,
    payload: CommentIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentOut:
    """Edit a comment's body and queue a re-embed."""
    await _get_document_or_404(session, document_id)
    comment = await _get_comment_or_404(session, document_id, comment_id)
    comment.body = payload.body
    session.add(
        IngestionEvent(
            document_id=document_id, event="comment_edited", detail={"comment_id": comment_id}
        )
    )
    await session.commit()
    await session.refresh(comment)
    await embed_document.defer_async(document_id=document_id)
    return CommentOut.model_validate(comment)


@router.delete(
    "/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment",
    responses={404: {"description": "Unknown or foreign comment"}},
)
async def delete_comment(
    document_id: int,
    comment_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a comment and queue a re-embed (drops its chunk from search)."""
    await _get_document_or_404(session, document_id)
    comment = await _get_comment_or_404(session, document_id, comment_id)
    await session.delete(comment)
    session.add(
        IngestionEvent(
            document_id=document_id, event="comment_deleted", detail={"comment_id": comment_id}
        )
    )
    await session.commit()
    await embed_document.defer_async(document_id=document_id)
