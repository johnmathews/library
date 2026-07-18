"""Reusable document-metadata mutation service.

The PATCH /api/documents/{id} route and the Ask agent's write tool both apply
partial metadata edits with the same semantics: sender/recipient upsert by
name, full-replacement of tags/projects, ``extra["user_edited_fields"]``
bookkeeping (so re-extraction never overwrites a human/agent edit), a
mining-ready correction record, and a ``user_edited`` audit event.

``apply_document_update`` is that shared core. It mutates ``document`` in
place and adds the audit event(s) to ``session`` but does NOT commit — the
caller owns the transaction (the route commits then refreshes; the Ask write
handler commits explicitly). ``edited_by`` ("user" vs "ask") is recorded in
the event detail so the source of every edit is auditable.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import (
    get_or_create_tag,
    revalidate_document,
    upsert_recipient,
    upsert_sender,
)
from library.matters import get_or_create_matter
from library.models import Document, IngestionEvent, Kind, ReviewStatus
from library.projects import get_or_create_project
from library.schemas import DocumentUpdate

# PATCH body field -> name recorded in extra["user_edited_fields"] (the
# storage-level names the W6 extraction contract checks).
_EDITED_FIELD_NAMES: dict[str, str] = {
    "kind_slug": "kind_id",
    "sender": "sender_id",
    "recipient": "recipient_id",
}


def current_value(document: Document, name: str) -> Any:
    """Read a storage-named field's current value off the document."""
    if name == "kind_id":
        return document.kind_id
    if name == "sender_id":
        return document.sender_id
    if name == "recipient_id":
        return document.recipient_id
    if name == "tags":
        return sorted(tag.slug for tag in document.tags)
    if name == "projects":
        return sorted(project.slug for project in document.projects)
    if name == "matters":
        return sorted(matter.slug for matter in document.matters)
    return getattr(document, name, None)


def _correction_records(
    document: Document, originals: dict[str, Any], edited: list[str]
) -> list[dict[str, Any]]:
    """Build mining-ready correction records for the fields just edited.

    ``originals`` maps storage field name -> value before the edit. Values are
    JSON-stringified (dates/Decimals -> str) so the records survive in JSONB.
    """
    extraction = document.extra.get("extraction") or {}
    text = document.ocr_text or ""

    def jsonable(value: Any) -> Any:
        return None if value is None else str(value)

    def excerpt(value: Any) -> str:
        needle = "" if value is None else str(value)
        idx = text.find(needle) if needle else -1
        if idx < 0:
            return ""
        start, end = max(0, idx - 40), min(len(text), idx + len(needle) + 40)
        return text[start:end]

    records: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for name in edited:
        new_value = current_value(document, name)
        records.append(
            {
                "field": name,
                "original_value": jsonable(originals.get(name)),
                "corrected_value": jsonable(new_value),
                "source_excerpt": excerpt(originals.get(name)),
                "prompt_version": extraction.get("prompt_version"),
                "model": extraction.get("model"),
                "corrected_at": now,
            }
        )
    return records


async def apply_document_update(
    session: AsyncSession,
    document: Document,
    update: DocumentUpdate,
    *,
    edited_by: str,
) -> list[str]:
    """Apply a partial metadata edit to ``document`` in place; return the list
    of changed storage-level field names.

    Performs sender/recipient upserts, full-replacement of tags/projects, the
    ``extra["user_edited_fields"]`` + ``corrections`` bookkeeping, and adds a
    ``user_edited`` audit event (plus a ``project_changed`` event when project
    membership changed) tagged with ``edited_by``. Does NOT commit.

    Raises ``HTTPException`` (422) on an unknown kind slug or a null value for a
    non-nullable field (tags/projects/language) — mirroring the PATCH route.
    """
    provided = update.model_dump(exclude_unset=True)
    if not provided:
        return []

    # Snapshot pre-edit values before any mutation so corrections are accurate.
    originals: dict[str, Any] = {}
    for body_field, storage in (
        ("kind_slug", "kind_id"),
        ("sender", "sender_id"),
        ("recipient", "recipient_id"),
    ):
        if body_field in provided:
            originals[storage] = current_value(document, storage)
    if "tags" in provided:
        originals["tags"] = current_value(document, "tags")
    if "projects" in provided:
        originals["projects"] = current_value(document, "projects")
    if "matters" in provided:
        originals["matters"] = current_value(document, "matters")
    for body_field in (
        "title",
        "summary",
        "document_date",
        "due_date",
        "expiry_date",
        "amount_total",
        "currency",
        "language",
    ):
        if body_field in provided:
            originals[body_field] = current_value(document, body_field)

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
    if "recipient" in provided:
        name = provided.pop("recipient")
        document.recipient_id = None if name is None else (await upsert_recipient(session, name)).id
        edited.append(_EDITED_FIELD_NAMES["recipient"])
    if "tags" in provided:
        slugs = provided.pop("tags")
        if slugs is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="tags cannot be null; send [] to clear them",
            )
        document.tags = [await get_or_create_tag(session, slug) for slug in dict.fromkeys(slugs)]
        edited.append("tags")
    if "projects" in provided:
        identifiers = provided.pop("projects")
        if identifiers is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="projects cannot be null; send [] to clear them",
            )
        document.projects = [
            await get_or_create_project(session, identifier)
            for identifier in dict.fromkeys(identifiers)
        ]
        edited.append("projects")
    if "matters" in provided:
        identifiers = provided.pop("matters")
        if identifiers is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="matters cannot be null; send [] to clear them",
            )
        # Resolve then dedup by the *matter*, not the raw string: distinct
        # inputs ("Car insurance" and "car-insurance") can resolve to the same
        # row, and appending it twice would violate the document_matters PK.
        resolved = [
            await get_or_create_matter(session, identifier)
            for identifier in dict.fromkeys(identifiers)
        ]
        document.matters = list({matter.id: matter for matter in resolved}.values())
        edited.append("matters")
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
    corrections = list(document.extra.get("corrections", []))
    corrections.extend(_correction_records(document, originals, edited))
    document.extra = {
        **document.extra,
        "user_edited_fields": user_edited,
        "corrections": corrections,
    }
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="user_edited",
            detail={"fields": edited, "edited_by": edited_by},
        )
    )
    if "projects" in edited:
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="project_changed",
                detail={"projects": current_value(document, "projects")},
            )
        )
    if "matters" in edited:
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="matter_changed",
                detail={"matters": current_value(document, "matters")},
            )
        )
    return edited


async def revalidate_after_edit(
    session: AsyncSession, document: Document, settings: Settings
) -> None:
    """Recompute validation after a user/agent edit and update ``review_status``.

    A metadata edit can resolve a finding (e.g. a corrected ``document_date``),
    so we re-run the deterministic rules and rewrite ``extra["validation"]``.
    Status policy, distinct from the extraction path:

    - any finding still firing  -> ``needs_review``
    - no findings, was verified  -> keep ``verified`` (never demote a human's
      explicit verification on an unrelated edit)
    - no findings, otherwise     -> ``unreviewed``

    Mutates ``document`` in place; does NOT commit — the caller owns the txn.
    """
    findings = await revalidate_document(session, document, settings)
    if findings:
        document.review_status = ReviewStatus.NEEDS_REVIEW
    elif document.review_status != ReviewStatus.VERIFIED:
        document.review_status = ReviewStatus.UNREVIEWED
