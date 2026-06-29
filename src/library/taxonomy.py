"""Taxonomy listing service: kinds, senders, recipients, and tags with counts.

One shared implementation behind two surfaces — the REST endpoints
(``GET /api/kinds|senders|recipients|tags``, see ``library.api.taxonomy``)
and the MCP ``list_kinds``/``list_senders``/``list_recipients``/``list_tags``
tools — so the counting rules (deleted documents excluded, zero-count
entries included) cannot drift apart.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, Recipient, Sender, Tag, document_tags


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
