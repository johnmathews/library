"""Tests for the business-matter service (slugify + list_matters counts)."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.matters import list_matters, slugify
from library.models import Document, DocumentSource, Matter


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Car Insurance", "car-insurance"),
        ("  Health, Insurance!  ", "health-insurance"),
        ("Already-Slugged", "already-slugged"),
        ("multiple   spaces___and---dashes", "multiple-spaces-and-dashes"),
        ("ünïcode & symbols #1", "n-code-symbols-1"),
        ("", "matter"),
        ("!!!", "matter"),
        ("x" * 100, "x" * 64),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    result = slugify(value)
    assert result == expected
    assert len(result) <= 64


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Document))
        await session.execute(delete(Matter))
        await session.commit()
        yield session


async def _document(
    session: AsyncSession,
    marker: str,
    *,
    deleted: bool = False,
    matters: list[Matter] | None = None,
) -> Document:
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        deleted_at=datetime.now(UTC) if deleted else None,
        matters=matters or [],
    )
    session.add(document)
    await session.commit()
    return document


@pytest.mark.integration
async def test_list_matters_counts_exclude_soft_deleted(session: AsyncSession) -> None:
    matter = Matter(slug="car-insurance", name="Car insurance")
    await _document(session, "live", matters=[matter])
    await _document(session, "dead", deleted=True, matters=[matter])

    matters = await list_matters(session)

    assert len(matters) == 1
    assert matters[0].slug == "car-insurance"
    assert matters[0].document_count == 1  # soft-deleted doc excluded


@pytest.mark.integration
async def test_list_matters_includes_zero_count_and_orders_by_name(
    session: AsyncSession,
) -> None:
    zebra = Matter(slug="zebra", name="Zebra")
    apple = Matter(slug="apple", name="Apple", hint="fruit filings")
    session.add_all([zebra, apple])
    await session.commit()

    await _document(session, "doc", matters=[apple])

    matters = await list_matters(session)

    assert [m.name for m in matters] == ["Apple", "Zebra"]
    counts = {m.slug: m.document_count for m in matters}
    assert counts == {"apple": 1, "zebra": 0}
    # hint is surfaced on the count row so the classifier/UI can read it.
    assert next(m for m in matters if m.slug == "apple").hint == "fruit filings"


@pytest.mark.integration
async def test_list_matters_hides_archived_unless_requested(session: AsyncSession) -> None:
    active = Matter(slug="active", name="Active")
    archived = Matter(slug="archived", name="Archived", archived_at=datetime.now(UTC))
    session.add_all([active, archived])
    await session.commit()

    visible = await list_matters(session)
    assert {m.slug for m in visible} == {"active"}

    everything = await list_matters(session, include_archived=True)
    assert {m.slug for m in everything} == {"active", "archived"}
