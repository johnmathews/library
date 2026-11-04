"""The one document-metadata edit path, shared by every surface that edits.

The PATCH /api/documents/{id} route and the Ask agent's write tool apply
partial metadata edits with the same semantics: sender/recipient upsert by
name, full-replacement of tags/projects/matters, ``extra["user_edited_fields"]``
bookkeeping (so re-extraction never overwrites a human/agent edit), a
mining-ready correction record, and a ``user_edited`` audit event.

``edit_document`` is the entry point callers use: apply, revalidate, commit
through the allocation guard, and re-embed when a chunk-header field changed.
It exists once so a further caller (the MCP edit tool) cannot drift from the
other two. ``apply_document_update`` is its uncommitted core.
``preview_update`` renders the current-vs-proposed view a caller shows before
asking to apply.

This module does not speak HTTP. A refused edit raises ``DocumentEditError``
(the PATCH route answers 422, Ask returns a tool-result error); an allocated
document's amount edit raises ``spend_lines.AllocationError`` (PATCH answers
400). The audit event records ``edited_by`` (``"user"``, ``"ask"``, ...) and
``user_id`` — which surface made the edit, and which person.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import (
    get_or_create_tag,
    revalidate_document,
    upsert_recipient,
    upsert_sender,
)
from library.jobs import embed_document
from library.matters import get_or_create_matter
from library.models import Document, IngestionEvent, Kind, ReviewStatus
from library.projects import get_or_create_project
from library.schemas import DocumentUpdate
from library.spend_lines import AMOUNT_ALLOCATED_REFUSAL, commit_allocation


class DocumentEditError(ValueError):
    """An edit that cannot be applied as asked; ``str(exc)`` is the reason.

    Raised for an unknown kind slug and for ``null`` on a field that cannot be
    cleared that way (tags/projects/matters/language). Always raised BEFORE
    anything is mutated, so a caller that keeps using the session (Ask commits
    it to persist the turn) cannot write half an edit. Each caller translates
    it: the PATCH route to a 422 with the message as ``detail``, Ask to a
    tool-result ``error``.
    """


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
    user_id: int | None = None,
) -> list[str]:
    """Apply a partial metadata edit to ``document`` in place; return the list
    of changed storage-level field names.

    Performs sender/recipient upserts, full-replacement of tags/projects, the
    ``extra["user_edited_fields"]`` + ``corrections`` bookkeeping, and adds a
    ``user_edited`` audit event (plus a ``project_changed`` event when project
    membership changed) recording ``edited_by`` and ``user_id`` (the editing
    person; ``None`` when none is known). Does NOT commit — callers other than
    tests go through ``edit_document``.

    Raises ``DocumentEditError`` on an unknown kind slug or a null value for a
    non-clearable field (tags/projects/matters/language). Every check runs
    before the first mutation, so a refused edit leaves ``document`` and the
    session untouched.
    """
    provided = update.model_dump(exclude_unset=True)
    if not provided:
        return []

    # Validate everything first, in the order the fields are applied below, so
    # a refusal never follows a partial mutation (sender applied, then tags
    # refused) that a caller's later commit would persist unaudited — Ask
    # commits its session after every turn to store the conversation.
    kind: Kind | None = None
    if provided.get("kind_slug") is not None:
        slug = provided["kind_slug"]
        kind = (await session.execute(select(Kind).where(Kind.slug == slug))).scalar_one_or_none()
        if kind is None:
            raise DocumentEditError(f"unknown kind slug: {slug!r}")
    for list_field in ("tags", "projects", "matters"):
        if list_field in provided and provided[list_field] is None:
            raise DocumentEditError(f"{list_field} cannot be null; send [] to clear them")
    if provided.get("language", "") is None:
        raise DocumentEditError("language cannot be null")

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
        provided.pop("kind_slug")
        document.kind_id = None if kind is None else kind.id
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
        document.tags = [await get_or_create_tag(session, slug) for slug in dict.fromkeys(slugs)]
        edited.append("tags")
    if "projects" in provided:
        identifiers = provided.pop("projects")
        document.projects = [
            await get_or_create_project(session, identifier)
            for identifier in dict.fromkeys(identifiers)
        ]
        edited.append("projects")
    if "matters" in provided:
        identifiers = provided.pop("matters")
        # Resolve then dedup by the *matter*, not the raw string: distinct
        # inputs ("Car insurance" and "car-insurance") can resolve to the same
        # row, and appending it twice would violate the document_matters PK.
        resolved = [
            await get_or_create_matter(session, identifier)
            for identifier in dict.fromkeys(identifiers)
        ]
        document.matters = list({matter.id: matter for matter in resolved}.values())
        edited.append("matters")
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
            detail={"fields": edited, "edited_by": edited_by, "user_id": user_id},
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


#: Storage-level field names that appear in a chunk's ``context_header``
#: (see ``jobs.compose_context_header``). An edit touching any of them makes
#: every stored header for that document stale, so it must be re-embedded.
#:
#: The mixed naming is NOT a mistake and must not be "tidied": the list this is
#: compared against is ``apply_document_update``'s return value, which maps
#: ``sender``/``kind_slug`` through ``_EDITED_FIELD_NAMES`` to ``sender_id``/
#: ``kind_id`` but appends plain scalar fields under their own names. Verified
#: by execution: sender -> ['sender_id'], kind_slug -> ['kind_id'],
#: title -> ['title'], document_date -> ['document_date'].
HEADER_FIELDS: frozenset[str] = frozenset({"sender_id", "kind_id", "title", "document_date"})


def header_fields_changed(edited: Sequence[str]) -> bool:
    """Whether an ``apply_document_update`` result touched a header field."""
    return bool(HEADER_FIELDS.intersection(edited))


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


async def edit_document(
    session: AsyncSession,
    document: Document,
    update: DocumentUpdate,
    *,
    settings: Settings,
    edited_by: str,
    user_id: int | None,
) -> list[str]:
    """Apply, revalidate, commit and (when needed) re-embed one metadata edit.

    The whole sequence every editing surface runs; returns the changed
    storage-level field names (``[]`` for an empty update, which writes and
    commits nothing).

    1. ``apply_document_update`` — the mutation, plus the ``user_edited``
       audit event recording ``edited_by`` and ``user_id``.
    2. ``revalidate_after_edit`` — a corrected field can clear (or raise) a
       validation finding, so ``review_status`` is recomputed in the same
       transaction; this is what clears a fixed "implausible date" on save.
    3. ``commit_allocation``, not ``session.commit()`` — editing
       ``amount_total`` on a document whose spend lines are allocated against
       the old amount is refused by migration 0035's deferred mirror trigger at
       COMMIT, as a bare ``DBAPIError``; the helper turns that one refusal into
       ``AllocationError`` (docs/charts.md §10.1) and re-raises anything else.
    4. A chunk's ``context_header`` embeds sender/date/kind/title, so an edit
       touching one makes every stored header stale: defer ``embed_document``,
       and only then, so a summary or tags edit does not wake the embedder.

    Raises ``DocumentEditError`` (before anything is mutated) or
    ``AllocationError``. The latter follows a rollback, which expires every ORM
    object in the session: neither this function nor the caller may read
    ``document`` after it (a lazy load there is MissingGreenlet), which is why
    the id is held as a plain int here.
    """
    document_id = document.id
    edited = await apply_document_update(
        session, document, update, edited_by=edited_by, user_id=user_id
    )
    if not edited:
        return []
    await revalidate_after_edit(session, document, settings)
    await commit_allocation(session, refusal=AMOUNT_ALLOCATED_REFUSAL)
    if header_fields_changed(edited):
        await embed_document.defer_async(document_id=document_id)
    return edited


def preview_current(document: Document, field: str) -> Any:
    """Human-readable current value of a ``DocumentUpdate`` field.

    Names and slugs, not ids, because this is what a person is asked to
    approve. Any *relationship* field must get a branch here: previews are
    serialised with ``json.dumps(..., default=str)``, so a bare ORM object does
    not fail loudly — it renders as "<Matter object at 0x...>".
    """
    if field == "sender":
        return document.sender.name if document.sender else None
    if field == "recipient":
        return document.recipient.name if document.recipient else None
    if field == "kind_slug":
        return document.kind.slug if document.kind else None
    if field == "tags":
        return sorted(tag.slug for tag in document.tags)
    if field == "projects":
        return sorted(project.slug for project in document.projects)
    if field == "matters":
        return sorted(matter.slug for matter in document.matters)
    return getattr(document, field, None)


def preview_update(document: Document, update: DocumentUpdate) -> dict[str, dict[str, Any]]:
    """Current vs proposed value for each field ``update`` sets; writes nothing.

    Keyed by ``DocumentUpdate`` field name (``kind_slug``, ``sender``, ...) in
    model field order. ``proposed`` is the validated value in JSON form (an
    amount is a decimal string, a date ISO text, ``currency`` upper-cased), so
    the preview shows what would be stored rather than echoing the input. It
    does not check what only applying can (an unknown kind slug, an allocated
    amount): those surface from ``edit_document``.
    """
    proposed = update.model_dump(mode="json", exclude_unset=True)
    return {
        name: {"current": preview_current(document, name), "proposed": value}
        for name, value in proposed.items()
    }
