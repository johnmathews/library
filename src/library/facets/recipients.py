"""Propose and merge duplicate recipient rows.

The recipients table has the same drift as the free-form tags: several rows
spelling one name several ways, splitting one person's documents. Normalisation
groups them; merging is always an explicit command, because two genuinely
different people can normalise alike.

A recipient can carry a ``user_id`` link (see :class:`library.models.Recipient`):
``get_or_create_user_recipient`` in ``library.extraction.apply`` auto-links a
recipient row to a :class:`~library.models.User`, and Ask's ``own_recipients``
(``library.ask.context``) treats that link as "the user's own" documents. A
merge that drops a user-linked recipient without carrying the link forward
would silently sever that identity — and the next ingested document addressed
to that user would recreate a brand-new linked recipient, re-splitting the
exact person this module exists to unify. ``merge_recipients`` therefore
transfers an unambiguous link onto the survivor and refuses (raising
``ValueError``) rather than guessing when the drop set or the survivor
disagree about which user is linked.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
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

    Before touching anything, checks ``user_id`` on ``keep_id`` and every drop
    target:

    * no dropped row carries a ``user_id`` — proceeds exactly as before.
    * exactly one distinct non-null ``user_id`` appears among the drops, and
      ``keep_id``'s ``user_id`` is either ``None`` or equal to it — the link is
      transferred onto ``keep_id`` before the delete.
    * ``keep_id`` already carries a *different* non-null ``user_id``, or the
      drop set carries more than one distinct non-null ``user_id`` — refuses
      by raising ``ValueError`` naming the conflicting recipient ids, and
      nothing is deleted or moved.
    """
    targets = [rid for rid in drop_ids if rid != keep_id]
    if not targets:
        return 0

    linked = (
        (
            await session.execute(
                select(Recipient.id, Recipient.user_id).where(Recipient.id.in_([keep_id, *targets]))
            )
        )
        .tuples()
        .all()
    )
    user_id_by_recipient: dict[int, int | None] = dict(linked)
    keep_user_id = user_id_by_recipient.get(keep_id)
    drop_links = {
        recipient_id: user_id
        for recipient_id, user_id in linked
        if recipient_id in targets and user_id is not None
    }
    distinct_user_ids = set(drop_links.values())

    if distinct_user_ids:
        if len(distinct_user_ids) > 1:
            conflicting = sorted(drop_links)
            raise ValueError(
                f"cannot merge into recipient {keep_id}: drop recipients {conflicting} "
                "are linked to different users"
            )
        (transfer_user_id,) = distinct_user_ids
        if keep_user_id is not None and keep_user_id != transfer_user_id:
            conflicting = sorted(drop_links)
            raise ValueError(
                f"cannot merge into recipient {keep_id}: it is linked to a different user "
                f"than drop recipients {conflicting}"
            )
        if keep_user_id is None:
            await session.execute(
                update(Recipient).where(Recipient.id == keep_id).values(user_id=transfer_user_id)
            )

    # `AsyncSession.execute` is typed as returning `Result`, which has no
    # `rowcount`; this is a plain UPDATE, so the runtime object is a
    # `CursorResult`. Narrowed rather than ignored so the attribute stays checked.
    moved = cast(
        CursorResult[Any],
        await session.execute(
            update(Document).where(Document.recipient_id.in_(targets)).values(recipient_id=keep_id)
        ),
    ).rowcount
    await session.execute(delete(Recipient).where(Recipient.id.in_(targets)))
    return int(moved)
