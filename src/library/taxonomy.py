"""Taxonomy listing service: kinds, senders, recipients, and tags with counts.

One shared implementation behind two surfaces — the REST endpoints
(``GET /api/kinds|senders|recipients|tags``, see ``library.api.taxonomy``)
and the MCP ``list_kinds``/``list_senders``/``list_recipients``/``list_tags``
tools — so the counting rules (deleted documents excluded, zero-count
entries included) cannot drift apart.
"""

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, Recipient, Sender, Tag, document_tags


@dataclass(frozen=True)
class CreateEntityResult[Entity: (Sender, Recipient)]:
    """Outcome of a reference-entity create.

    - ``created`` — a new row (``entity`` is it).
    - ``exists`` — a case-insensitive name match already existed (``entity`` is
      the existing row); no duplicate is made.
    - ``empty_name`` — the name was blank after trimming.
    """

    status: Literal["created", "exists", "empty_name"]
    entity: Entity | None = None


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_kind(value: str) -> str:
    """Lowercase, non-alphanumeric runs to hyphens, trimmed, max 64 chars.

    Mirrors ``library.projects.slugify`` (same rules so behaviour can't drift),
    but falls back to ``"kind"`` when nothing usable remains.
    """
    slug = _NON_ALNUM.sub("-", value.lower()).strip("-")
    slug = slug[:64].strip("-")
    return slug or "kind"


def standardize_kind_name(value: str) -> str:
    """Collapse internal whitespace and apply sentence case.

    Matches the seeded display-name convention ("Invoice", "Utility bill",
    "Parking ticket"): the first character is upper-cased and the rest
    lower-cased, so casing variants of one kind ("QUOTE", "quote") all store
    the same name.
    """
    cleaned = " ".join(value.split())
    return cleaned[:1].upper() + cleaned[1:].lower()


def _levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings (insertions/deletions/substitutions).

    A small standalone implementation (no extra dependency) used only to flag
    near-duplicate kind names, where the inputs are short.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def _is_near_duplicate(a: str, b: str) -> bool:
    """Whether two normalised names are confusably similar but not identical.

    The threshold scales down for very short names so genuinely distinct short
    kinds aren't wrongly blocked: distance ≤ 1 when the shorter name is ≤ 4
    characters, otherwise ≤ 2. (Identical names are an exact dedupe, not a
    near-duplicate.)
    """
    distance = _levenshtein(a, b)
    if distance == 0:
        return False
    threshold = 1 if min(len(a), len(b)) <= 4 else 2
    return distance <= threshold


@dataclass(frozen=True)
class KindCount:
    """One document kind with the number of (non-deleted) documents in it."""

    slug: str
    name: str
    document_count: int


@dataclass(frozen=True)
class SenderCount:
    """One sender with the number of (non-deleted) documents from it."""

    id: int
    name: str
    document_count: int


@dataclass(frozen=True)
class RecipientCount:
    """One recipient with the number of (non-deleted) documents addressed to it."""

    id: int
    name: str
    document_count: int


@dataclass(frozen=True)
class TagCount:
    """One tag with the number of (non-deleted) documents carrying it."""

    slug: str
    name: str
    document_count: int


async def list_kinds(session: AsyncSession) -> list[KindCount]:
    """All kinds ordered by slug; counts exclude soft-deleted documents."""
    statement = (
        select(Kind.slug, Kind.name, func.count(Document.id))
        .join(
            Document,
            (Document.kind_id == Kind.id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Kind.id)
        .order_by(Kind.slug)
    )
    rows = (await session.execute(statement)).all()
    return [KindCount(slug=slug, name=name, document_count=count) for slug, name, count in rows]


@dataclass(frozen=True)
class CreateKindResult:
    """Outcome of :func:`create_kind`.

    - ``created`` — a brand-new kind was added (``kind`` is the new row).
    - ``exists`` — an exact (case/whitespace-insensitive) match was found;
      ``kind`` is the existing row, no row was created.
    - ``near_duplicate`` — the name is confusably similar to an existing kind;
      nothing was created and ``existing`` names the matched kind so the caller
      can surface it.
    - ``empty_name`` — the name was blank after trimming.
    """

    status: Literal["created", "exists", "near_duplicate", "empty_name"]
    kind: Kind | None = None
    existing: Kind | None = None


async def create_kind(session: AsyncSession, name: str) -> CreateKindResult:
    """Create a document kind from a human name, deduping and casing it.

    The name is whitespace-collapsed and slugified (mirroring
    ``projects.slugify``); the stored display name is sentence-cased to match
    the seeded set. An exact match on slug or normalised name returns the
    existing kind (``exists``) rather than creating a duplicate. A name within a
    small edit distance of an existing kind is refused (``near_duplicate``) so
    typos/plurals don't fragment the taxonomy. Owns its transaction (commits on
    create), mirroring the recipient CRUD services.
    """
    cleaned = " ".join(name.split())
    if not cleaned:
        return CreateKindResult(status="empty_name")

    slug = slugify_kind(cleaned)
    normalised = cleaned.lower()
    existing_kinds = (await session.execute(select(Kind))).scalars().all()

    for kind in existing_kinds:
        if kind.slug == slug or kind.name.lower() == normalised:
            return CreateKindResult(status="exists", kind=kind)
    for kind in existing_kinds:
        if _is_near_duplicate(kind.name.lower(), normalised):
            return CreateKindResult(status="near_duplicate", existing=kind)

    kind = Kind(slug=slug, name=standardize_kind_name(cleaned))
    session.add(kind)
    await session.commit()
    await session.refresh(kind)
    return CreateKindResult(status="created", kind=kind)


async def list_senders(session: AsyncSession) -> list[SenderCount]:
    """All senders ordered by name; counts exclude soft-deleted documents."""
    statement = (
        select(Sender.id, Sender.name, func.count(Document.id))
        .join(
            Document,
            (Document.sender_id == Sender.id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Sender.id)
        .order_by(Sender.name)
    )
    rows = (await session.execute(statement)).all()
    return [
        SenderCount(id=sender_id, name=name, document_count=count)
        for sender_id, name, count in rows
    ]


async def list_recipients(session: AsyncSession) -> list[RecipientCount]:
    """All recipients ordered by name; counts exclude soft-deleted documents."""
    statement = (
        select(Recipient.id, Recipient.name, func.count(Document.id))
        .join(
            Document,
            (Document.recipient_id == Recipient.id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Recipient.id)
        .order_by(Recipient.name)
    )
    rows = (await session.execute(statement)).all()
    return [
        RecipientCount(id=recipient_id, name=name, document_count=count)
        for recipient_id, name, count in rows
    ]


# --------------------------------------------------------- recipient management
#
# Admin-only mutations behind PATCH/DELETE /api/admin/recipients (see
# library.api.admin). Both services own their transaction (explicit commit,
# mirroring the users CRUD) and return a small result object so the route can
# map the outcome onto the right HTTP status without leaking HTTP concerns here.


@dataclass(frozen=True)
class RenameResult:
    """Outcome of :func:`rename_recipient`.

    - ``renamed`` — name updated in place (``recipient`` is the renamed row).
    - ``merged`` — collided with ``merge=True``; ``recipient`` is the surviving
      target the documents were moved onto (this recipient was deleted).
    - ``collision`` — collided with ``merge=False``; ``recipient`` is the
      conflicting target and ``document_count`` its visible document count.
    - ``not_found`` — no recipient with ``recipient_id``.
    - ``empty_name`` — the new name was blank after trimming.
    """

    status: Literal["renamed", "merged", "collision", "not_found", "empty_name"]
    recipient: Recipient | None = None
    document_count: int = 0


class _Unset:
    """Sentinel for ``reassign_to``: the caller supplied no target at all.

    Distinct from an explicit ``None`` (null the documents): a missing target on
    an in-use recipient is refused, whereas an explicit ``None`` nulls and deletes.
    """


UNSET = _Unset()


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of :func:`reassign_and_delete_recipient`.

    - ``deleted`` — documents reassigned (or nulled) and the recipient removed.
    - ``not_found`` — no recipient with ``recipient_id``.
    - ``target_not_found`` — ``reassign_to`` does not name a recipient.
    - ``self_reassign`` — ``reassign_to`` equals ``recipient_id``.
    - ``in_use`` — recipient still has ``document_count`` documents and no
      reassignment target was supplied.
    """

    status: Literal["deleted", "not_found", "target_not_found", "self_reassign", "in_use"]
    document_count: int = 0


async def _recipient_document_count(session: AsyncSession, recipient_id: int) -> int:
    """Non-deleted documents addressed to a recipient (matches list_recipients)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.recipient_id == recipient_id, Document.deleted_at.is_(None))
        )
    ).scalar_one()


async def rename_recipient(
    session: AsyncSession, recipient_id: int, new_name: str, merge: bool
) -> RenameResult:
    """Rename a recipient, merging into an existing name on collision when asked.

    A case-insensitive name match against any *other* recipient is a collision:
    with ``merge=False`` it is reported (the caller warns the user); with
    ``merge=True`` this recipient's documents are reassigned to the matched
    target, this recipient is deleted, and the target is returned. With no
    collision the name is updated in place (a pure casing change included).
    """
    cleaned = new_name.strip()
    if not cleaned:
        return RenameResult(status="empty_name")
    recipient = await session.get(Recipient, recipient_id)
    if recipient is None:
        return RenameResult(status="not_found")

    target = (
        await session.execute(
            select(Recipient).where(
                func.lower(Recipient.name) == cleaned.lower(),
                Recipient.id != recipient_id,
            )
        )
    ).scalar_one_or_none()

    if target is not None:
        if not merge:
            count = await _recipient_document_count(session, target.id)
            return RenameResult(status="collision", recipient=target, document_count=count)
        # Reassign every document (incl. soft-deleted, so nothing is orphaned by
        # the FK's ON DELETE SET NULL) onto the surviving target, then drop this one.
        await session.execute(
            update(Document)
            .where(Document.recipient_id == recipient_id)
            .values(recipient_id=target.id)
        )
        await session.delete(recipient)
        await session.commit()
        return RenameResult(status="merged", recipient=target)

    recipient.name = cleaned
    await session.commit()
    return RenameResult(status="renamed", recipient=recipient)


async def reassign_and_delete_recipient(
    session: AsyncSession, recipient_id: int, reassign_to: int | None | _Unset = UNSET
) -> DeleteResult:
    """Delete a recipient, first moving its documents to ``reassign_to`` (or NULL).

    ``reassign_to`` is three-state: a recipient id (move the documents there),
    explicit ``None`` (null the documents), or :data:`UNSET` (no target given).
    A recipient with no documents is deleted outright. One that still has
    documents requires an *explicit* choice: with :data:`UNSET` the deletion is
    refused (``in_use``); otherwise the documents are moved (to the target, or to
    NULL) and the recipient removed.
    """
    recipient = await session.get(Recipient, recipient_id)
    if recipient is None:
        return DeleteResult(status="not_found")

    provided = not isinstance(reassign_to, _Unset)
    target_id: int | None = reassign_to if isinstance(reassign_to, int) else None
    if target_id is not None:
        if target_id == recipient_id:
            return DeleteResult(status="self_reassign")
        if await session.get(Recipient, target_id) is None:
            return DeleteResult(status="target_not_found")

    count = await _recipient_document_count(session, recipient_id)
    if count > 0 and not provided:
        return DeleteResult(status="in_use", document_count=count)

    # Move every document (incl. soft-deleted) off this recipient before deleting.
    await session.execute(
        update(Document).where(Document.recipient_id == recipient_id).values(recipient_id=target_id)
    )
    await session.delete(recipient)
    await session.commit()
    return DeleteResult(status="deleted")


async def create_recipient(session: AsyncSession, name: str) -> "CreateEntityResult[Recipient]":
    """Create a recipient from a name, deduping case-insensitively.

    Trims and collapses whitespace; an existing case-insensitive name match is
    returned (``exists``) rather than creating a duplicate (the ``name`` column
    is unique). Owns its transaction, mirroring the other reference CRUD.
    """
    cleaned = " ".join(name.split())
    if not cleaned:
        return CreateEntityResult(status="empty_name")
    existing = (
        await session.execute(
            select(Recipient).where(func.lower(Recipient.name) == cleaned.lower())
        )
    ).scalar_one_or_none()
    if existing is not None:
        return CreateEntityResult(status="exists", entity=existing)
    recipient = Recipient(name=cleaned)
    session.add(recipient)
    await session.commit()
    await session.refresh(recipient)
    return CreateEntityResult(status="created", entity=recipient)


# ------------------------------------------------------------- sender management
#
# Admin-only mutations behind POST/PATCH/DELETE /api/admin/senders, mirroring the
# recipient CRUD above (Sender has the same shape: unique name, Document.sender_id
# nullable ON DELETE SET NULL). Each service owns its transaction.


async def _sender_document_count(session: AsyncSession, sender_id: int) -> int:
    """Non-deleted documents from a sender (matches list_senders counts)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.sender_id == sender_id, Document.deleted_at.is_(None))
        )
    ).scalar_one()


async def create_sender(session: AsyncSession, name: str) -> "CreateEntityResult[Sender]":
    """Create a sender from a name, deduping case-insensitively (see create_recipient)."""
    cleaned = " ".join(name.split())
    if not cleaned:
        return CreateEntityResult(status="empty_name")
    existing = (
        await session.execute(select(Sender).where(func.lower(Sender.name) == cleaned.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return CreateEntityResult(status="exists", entity=existing)
    sender = Sender(name=cleaned)
    session.add(sender)
    await session.commit()
    await session.refresh(sender)
    return CreateEntityResult(status="created", entity=sender)


@dataclass(frozen=True)
class SenderRenameResult:
    """Outcome of :func:`rename_sender` (mirrors :class:`RenameResult`)."""

    status: Literal["renamed", "merged", "collision", "not_found", "empty_name"]
    sender: Sender | None = None
    document_count: int = 0


async def rename_sender(
    session: AsyncSession, sender_id: int, new_name: str, merge: bool
) -> SenderRenameResult:
    """Rename a sender, merging into an existing name on collision when asked.

    Mirrors :func:`rename_recipient`: a case-insensitive name match against
    another sender is a collision, reported with ``merge=False`` or (with
    ``merge=True``) resolved by reassigning this sender's documents onto the
    target and deleting this sender.
    """
    cleaned = new_name.strip()
    if not cleaned:
        return SenderRenameResult(status="empty_name")
    sender = await session.get(Sender, sender_id)
    if sender is None:
        return SenderRenameResult(status="not_found")

    target = (
        await session.execute(
            select(Sender).where(
                func.lower(Sender.name) == cleaned.lower(),
                Sender.id != sender_id,
            )
        )
    ).scalar_one_or_none()

    if target is not None:
        if not merge:
            count = await _sender_document_count(session, target.id)
            return SenderRenameResult(status="collision", sender=target, document_count=count)
        await session.execute(
            update(Document).where(Document.sender_id == sender_id).values(sender_id=target.id)
        )
        await session.delete(sender)
        await session.commit()
        return SenderRenameResult(status="merged", sender=target)

    sender.name = cleaned
    await session.commit()
    return SenderRenameResult(status="renamed", sender=sender)


async def reassign_and_delete_sender(
    session: AsyncSession, sender_id: int, reassign_to: int | None | _Unset = UNSET
) -> DeleteResult:
    """Delete a sender, first moving its documents to ``reassign_to`` (or NULL).

    Three-state ``reassign_to`` exactly as :func:`reassign_and_delete_recipient`.
    """
    sender = await session.get(Sender, sender_id)
    if sender is None:
        return DeleteResult(status="not_found")

    provided = not isinstance(reassign_to, _Unset)
    target_id: int | None = reassign_to if isinstance(reassign_to, int) else None
    if target_id is not None:
        if target_id == sender_id:
            return DeleteResult(status="self_reassign")
        if await session.get(Sender, target_id) is None:
            return DeleteResult(status="target_not_found")

    count = await _sender_document_count(session, sender_id)
    if count > 0 and not provided:
        return DeleteResult(status="in_use", document_count=count)

    await session.execute(
        update(Document).where(Document.sender_id == sender_id).values(sender_id=target_id)
    )
    await session.delete(sender)
    await session.commit()
    return DeleteResult(status="deleted")


# --------------------------------------------------------------- kind management
#
# Admin-only mutations behind PATCH/DELETE /api/admin/kinds/{slug}. Kinds are
# identified by their stable, unique ``slug``: rename edits the display ``name``
# only (the slug never changes, so anything keyed on it keeps working), and there
# is no name-merge — a name collision with another kind is refused. Delete
# reassigns Document.kind_id (target named by slug) exactly like the others.


async def _kind_document_count(session: AsyncSession, kind_id: int) -> int:
    """Non-deleted documents of a kind (matches list_kinds counts)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.kind_id == kind_id, Document.deleted_at.is_(None))
        )
    ).scalar_one()


@dataclass(frozen=True)
class KindRenameResult:
    """Outcome of :func:`rename_kind`.

    - ``renamed`` — display name updated in place (``kind`` is the row); slug
      is never touched.
    - ``collision`` — another kind already uses that name (``kind`` is the
      conflicting kind); refused because duplicate display names are confusing
      and there is no kind-merge.
    - ``not_found`` — no kind with the slug.
    - ``empty_name`` — the new name was blank after trimming.
    """

    status: Literal["renamed", "collision", "not_found", "empty_name"]
    kind: Kind | None = None


async def rename_kind(session: AsyncSession, slug: str, new_name: str) -> KindRenameResult:
    """Rename a kind's display name (slug immutable); refuse a name collision.

    The slug is a stable machine identifier, so only ``name`` changes. A
    case-insensitive name match against another kind is refused (``collision``)
    rather than merged — kinds have no merge semantics.
    """
    cleaned = standardize_kind_name(new_name) if new_name.strip() else ""
    if not cleaned:
        return KindRenameResult(status="empty_name")
    kind = (await session.execute(select(Kind).where(Kind.slug == slug))).scalar_one_or_none()
    if kind is None:
        return KindRenameResult(status="not_found")

    target = (
        await session.execute(
            select(Kind).where(func.lower(Kind.name) == cleaned.lower(), Kind.slug != slug)
        )
    ).scalar_one_or_none()
    if target is not None:
        return KindRenameResult(status="collision", kind=target)

    kind.name = cleaned
    await session.commit()
    return KindRenameResult(status="renamed", kind=kind)


async def reassign_and_delete_kind(
    session: AsyncSession, slug: str, reassign_to: str | None | _Unset = UNSET
) -> DeleteResult:
    """Delete a kind, first moving its documents to the ``reassign_to`` kind (or NULL).

    Kinds are named by slug throughout their API, so ``reassign_to`` is a target
    *slug* (three-state: a slug to move onto, explicit ``None`` to null, or
    :data:`UNSET`). Behaviour otherwise matches
    :func:`reassign_and_delete_recipient`.
    """
    kind = (await session.execute(select(Kind).where(Kind.slug == slug))).scalar_one_or_none()
    if kind is None:
        return DeleteResult(status="not_found")

    provided = not isinstance(reassign_to, _Unset)
    target_id: int | None = None
    if isinstance(reassign_to, str):
        if reassign_to == slug:
            return DeleteResult(status="self_reassign")
        target = (
            await session.execute(select(Kind).where(Kind.slug == reassign_to))
        ).scalar_one_or_none()
        if target is None:
            return DeleteResult(status="target_not_found")
        target_id = target.id

    count = await _kind_document_count(session, kind.id)
    if count > 0 and not provided:
        return DeleteResult(status="in_use", document_count=count)

    await session.execute(
        update(Document).where(Document.kind_id == kind.id).values(kind_id=target_id)
    )
    await session.delete(kind)
    await session.commit()
    return DeleteResult(status="deleted")


async def list_tags(session: AsyncSession) -> list[TagCount]:
    """All tags ordered by name; counts exclude soft-deleted documents."""
    statement = (
        select(Tag.slug, Tag.name, func.count(Document.id))
        .join(document_tags, document_tags.c.tag_id == Tag.id, isouter=True)
        .join(
            Document,
            (Document.id == document_tags.c.document_id) & Document.deleted_at.is_(None),
            isouter=True,
        )
        .group_by(Tag.id)
        .order_by(Tag.name)
    )
    rows = (await session.execute(statement)).all()
    return [TagCount(slug=slug, name=name, document_count=count) for slug, name, count in rows]
