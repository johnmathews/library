"""Proposing recipient duplicates, and merging them without losing documents."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.facets.recipients import duplicate_recipient_groups, merge_recipients
from library.models import Document, DocumentSource, DocumentStatus, Recipient

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _seed(database_url: str, names: list[str]) -> list[int]:
    async def _work(session: AsyncSession) -> list[int]:
        ids: list[int] = []
        for name in names:
            recipient = Recipient(name=name)
            session.add(recipient)
            await session.flush()
            marker = f"recipient:{name}:{uuid.uuid4()}"
            session.add(
                Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    recipient_id=recipient.id,
                    title=marker,
                )
            )
            ids.append(recipient.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def test_spelling_variants_group_together(api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:6].upper()
    _seed(api_database_url, [f"{tag} Smith", f"{tag}. Smith", f"{tag}  smith"])
    groups = asyncio.run(_run(api_database_url, duplicate_recipient_groups))
    matching = [g for key, g in groups if tag.lower() in key]
    assert matching and len(matching[0]) == 3


def test_merging_repoints_documents_and_removes_the_duplicates(
    api_database_url: str,
) -> None:
    tag = uuid.uuid4().hex[:6].upper()
    keep, drop_a, drop_b = _seed(api_database_url, [f"{tag} A", f"{tag} B", f"{tag} C"])

    moved = asyncio.run(
        _run(api_database_url, lambda s: merge_recipients(s, keep, [drop_a, drop_b]))
    )
    assert moved == 2

    remaining = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Recipient.id).where(Recipient.id.in_([drop_a, drop_b]))),
        )
    )
    assert list(remaining.scalars()) == []

    counted = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document.id).where(Document.recipient_id == keep)),
        )
    )
    assert len(list(counted.scalars())) == 3
