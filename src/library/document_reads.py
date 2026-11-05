"""The one document-read path, shared by every surface that reads a document.

The REST detail, markdown and list routes, the Ask agent's ``get_document``
tool, the MCP ``get_document`` and ``search_documents`` tools and the embed
stage all read a document's pages, comments and list fields. They read them
here, so a field added to one surface reaches the others and the text a
search matched on is the text a reader is shown.

- ``load_pages`` / ``load_comments``: the explicit queries behind
  ``Document.pages`` and ``Document.comments``, which are ``lazy="raise"``
  because a normal document load never wants them.
- ``document_text``: a document's readable text. That is its per-page
  markdown when it has any (what full-text search indexes, via
  ``pages_markdown``), else its OCR text. Paged by markdown page and capped
  at ``max_chars``, with what a caller needs to continue.
- ``list_item_fields`` / ``review_findings``: the ``DocumentFields`` values
  every list row and detail body carries.
- ``document_web_url``: the document's page in the web app.

This module does not speak HTTP or MCP: an impossible ``start_page`` raises
``DocumentTextRangeError`` and each caller words it for its own client.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, DocumentComment, DocumentPage, ReviewStatus
from library.schemas import (
    KindOut,
    MatterRef,
    ProjectRef,
    RecipientOut,
    SenderOut,
    TagOut,
    ValidationFindingSummary,
)
from library.storage import derived_path
from library.thumbnails import THUMBNAIL_NAME

#: How pages are joined into one text. Ask has always joined them this way;
#: ``document_text``'s page offsets depend on it.
PAGE_SEPARATOR: str = "\n\n"

#: Where a document's text came from: its markdown pages, its OCR text when it
#: has no pages (not converted yet, or markdown off), or nothing at all.
TextSource = Literal["pages", "ocr_text", "none"]


class DocumentTextRangeError(ValueError):
    """``start_page`` names a page the document's text does not have."""


def _more_text_after(full: str, end: int) -> bool:
    """Whether ``full`` has anything but whitespace past ``end``.

    A blank last page or trailing spaces are not more to read; counting them
    would send a caller back for a window of nothing.
    """
    return bool(full[end:].strip())


@dataclass(frozen=True, slots=True)
class DocumentText:
    """One window onto a document's text (see ``document_text``).

    ``start_page``/``end_page`` are the first and last markdown page with any
    text in the window (``None`` without pages); ``end_page_complete`` says
    whether ``end_page`` is in it whole. ``truncated`` is true when text
    other than whitespace follows the window, and ``next_start_page`` is the
    page to ask for next:
    the cut page itself when the window holds only its start (it is then read
    whole), the following page when the cut page is the window's first (it is
    longer than ``max_chars``, and only a larger ``max_chars`` returns the
    rest). ``total_chars`` is the length of the whole text, every page joined.
    """

    text: str
    source: TextSource
    page_count: int
    start_page: int | None
    end_page: int | None
    end_page_complete: bool
    truncated: bool
    next_start_page: int | None
    total_chars: int


async def load_pages(session: AsyncSession, document_id: int) -> Sequence[DocumentPage]:
    """The document's markdown pages, in page order."""
    result = await session.execute(
        select(DocumentPage)
        .where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number)
    )
    return result.scalars().all()


async def load_comments(
    session: AsyncSession, document_id: int, *, newest_first: bool
) -> Sequence[DocumentComment]:
    """The document's comments, by creation time (``newest_first`` or oldest first)."""
    order = DocumentComment.created_at.desc() if newest_first else DocumentComment.created_at
    result = await session.execute(
        select(DocumentComment).where(DocumentComment.document_id == document_id).order_by(order)
    )
    return result.scalars().all()


async def document_text(
    session: AsyncSession, document: Document, *, max_chars: int, start_page: int = 1
) -> DocumentText:
    """The document's text from ``start_page`` on, cut at ``max_chars``.

    Page markdown joined with ``PAGE_SEPARATOR`` when the document has pages,
    else its OCR text, which has no pages, so ``start_page`` must be 1. The
    window always holds as much text as fits: a page that does not fit whole
    is cut, never dropped (see ``DocumentText`` for how to continue).

    Raises ``DocumentTextRangeError`` for a ``start_page`` past the last page,
    or above 1 for a document without pages.
    """
    pages = await load_pages(session, document.id)
    if not pages:
        if start_page != 1:
            raise DocumentTextRangeError(
                f"document {document.id} has no page text; omit start_page"
            )
        full = document.ocr_text or ""
        return DocumentText(
            text=full[:max_chars],
            source="ocr_text" if full else "none",
            page_count=0,
            start_page=None,
            end_page=None,
            end_page_complete=True,
            truncated=_more_text_after(full, max_chars),
            next_start_page=None,
            total_chars=len(full),
        )

    last_page = pages[-1].page_number
    if start_page > last_page:
        raise DocumentTextRangeError(
            f"document {document.id} has {last_page} pages of text; "
            f"start_page must be at most {last_page}"
        )
    selected = [page for page in pages if page.page_number >= start_page]
    joined = PAGE_SEPARATOR.join(page.markdown for page in selected)
    text = joined[:max_chars]

    # Each selected page's [start, end) in `joined`; the window ends inside
    # (or right after) the last page that starts before `len(text)`.
    spans: list[tuple[int, int]] = []
    offset = 0
    for page in selected:
        spans.append((offset, offset + len(page.markdown)))
        offset += len(page.markdown) + len(PAGE_SEPARATOR)
    last_index = max(
        index for index, (start, _) in enumerate(spans) if start < len(text) or index == 0
    )
    end_complete = spans[last_index][1] <= len(text)
    truncated = _more_text_after(joined, len(text))

    next_index: int | None = None
    if truncated:
        resume_at_cut = not end_complete and last_index > 0
        next_index = last_index if resume_at_cut else last_index + 1
        if next_index >= len(selected):
            next_index = None

    return DocumentText(
        text=text,
        source="pages",
        page_count=len(pages),
        start_page=selected[0].page_number,
        end_page=selected[last_index].page_number,
        end_page_complete=end_complete,
        truncated=truncated,
        next_start_page=selected[next_index].page_number if next_index is not None else None,
        total_chars=len(PAGE_SEPARATOR.join(page.markdown for page in pages)),
    )


def list_item_fields(document: Document) -> dict[str, Any]:
    """The ``schemas.DocumentFields`` values shared by list rows and detail bodies."""
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
        "matters": [
            MatterRef(slug=matter.slug, name=matter.name)
            for matter in sorted(document.matters, key=lambda matter: matter.slug)
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
        "review_findings": review_findings(document),
        "amount_total": document.amount_total,
        "currency": document.currency,
        "amount_kind": document.amount_kind,
    }


def review_findings(document: Document) -> list[ValidationFindingSummary]:
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


def document_web_url(public_base_url: str | None, document_id: int) -> str | None:
    """The document's detail page in the web app, or ``None`` when the
    public URL is not configured (the same link push notifications carry)."""
    if not public_base_url:
        return None
    return f"{public_base_url.rstrip('/')}/documents/{document_id}"
