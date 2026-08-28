"""Propose and merge duplicate recipient rows.

The recipients table has the same drift as the free-form tags: several rows
spelling one name several ways, splitting one person's documents. Normalisation
groups them; merging is always an explicit command, because two genuinely
different people can normalise alike.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Recipient

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Grouping only."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", name)).strip().lower()


async def duplicate_recipient_groups(
    session: AsyncSession,
) -> list[tuple[str, list[tuple[int, str, int]]]]:
    """Groups of recipients whose names normalise alike, largest group first."""
    rows = (
        await session.execute(
            select(Recipient.id, Recipient.name, func.count(Document.id))
            .outerjoin(Document, Document.recipient_id == Recipient.id)
            .group_by(Recipient.id, Recipient.name)
            .order_by(Recipient.id)
        )
    ).all()

    grouped: dict[str, list[tuple[int, str, int]]] = {}
    for recipient_id, name, document_count in rows:
        grouped.setdefault(normalise_name(name), []).append(
            (recipient_id, name, int(document_count))
        )
    duplicates = [(key, members) for key, members in grouped.items() if len(members) > 1]
    duplicates.sort(key=lambda pair: len(pair[1]), reverse=True)
    return duplicates


async def merge_recipients(session: AsyncSession, keep_id: int, drop_ids: Sequence[int]) -> int:
    """Repoint every document from ``drop_ids`` onto ``keep_id`` and delete them.

    Returns the number of documents moved. ``keep_id`` is filtered out of
    ``drop_ids`` so a caller passing the survivor cannot delete it.
    """
    targets = [rid for rid in drop_ids if rid != keep_id]
    if not targets:
        return 0
    moved = (
        await session.execute(
            update(Document).where(Document.recipient_id.in_(targets)).values(recipient_id=keep_id)
        )
    ).rowcount
    await session.execute(delete(Recipient).where(Recipient.id.in_(targets)))
    return int(moved)
