"""Adding a comment to a document, shared by every surface that adds one.

``POST /api/documents/{id}/comments`` and the MCP ``add_comment`` tool both
call ``add_comment``, so the comment row, its ``comment_added`` audit event
and the re-embed that makes it searchable from Ask happen the same way
whoever adds it.

This module does not speak HTTP. A refusal raises ``CommentError`` (or its
subclass ``CommentDocumentNotFoundError``) before anything is written; the
route answers 404 for a missing document, the MCP tool returns the message
as a tool error.
"""

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from library.jobs import embed_document
from library.models import Document, DocumentComment, IngestionEvent
from library.schemas import CommentIn


class CommentError(ValueError):
    """A comment that cannot be added as asked; ``str(exc)`` is the reason."""


class CommentDocumentNotFoundError(CommentError):
    """The document does not exist or is in the trash."""


async def add_comment(
    session: AsyncSession, *, document_id: int, author_id: int | None, body: str
) -> DocumentComment:
    """Add a comment by ``author_id``, commit, and queue a re-embed.

    ``body`` is validated and normalised by ``CommentIn`` — the route's own
    request model, so both callers accept the same bodies (stripped;
    blank refused). The re-embed is deferred only after the commit, so the
    embed job never runs before the comment it has to index exists. Returns
    the committed, refreshed comment.
    """
    try:
        body = CommentIn(body=body).body
    except ValidationError as exc:
        raise CommentError("comment body must not be blank") from exc
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise CommentDocumentNotFoundError(f"document {document_id} not found")

    comment = DocumentComment(document_id=document_id, author_id=author_id, body=body)
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
    return comment
