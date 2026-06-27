"""Tests for the project/collection service (slugify + list_projects counts)."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import Document, DocumentSource, Project
from library.projects import list_projects, slugify


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Taxes 2026", "taxes-2026"),
        ("  Hello, World!  ", "hello-world"),
        ("Already-Slugged", "already-slugged"),
        ("multiple   spaces___and---dashes", "multiple-spaces-and-dashes"),
        ("ünïcode & symbols #1", "n-code-symbols-1"),
        ("", "project"),
        ("!!!", "project"),
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
        await session.execute(delete(Project))
        await session.commit()
        yield session


async def _document(
    session: AsyncSession,
    marker: str,
    *,
    deleted: bool = False,
    projects: list[Project] | None = None,
) -> Document:
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        deleted_at=datetime.now(UTC) if deleted else None,
        projects=projects or [],
    )
    session.add(document)
    await session.commit()
    return document


@pytest.mark.integration
async def test_list_projects_counts_exclude_soft_deleted(session: AsyncSession) -> None:
    project = Project(slug="house", name="House")
    await _document(session, "live", projects=[project])
    await _document(session, "dead", deleted=True, projects=[project])

    projects = await list_projects(session)

    assert len(projects) == 1
    assert projects[0].slug == "house"
    assert projects[0].document_count == 1  # soft-deleted doc excluded


@pytest.mark.integration
async def test_list_projects_includes_zero_count_and_orders_by_name(
    session: AsyncSession,
) -> None:
    zebra = Project(slug="zebra", name="Zebra")
    apple = Project(slug="apple", name="Apple")
    session.add_all([zebra, apple])
    await session.commit()

    await _document(session, "doc", projects=[apple])

    projects = await list_projects(session)

    assert [p.name for p in projects] == ["Apple", "Zebra"]
    counts = {p.slug: p.document_count for p in projects}
    assert counts == {"apple": 1, "zebra": 0}


@pytest.mark.integration
async def test_list_projects_hides_archived_unless_requested(session: AsyncSession) -> None:
    active = Project(slug="active", name="Active")
    archived = Project(slug="archived", name="Archived", archived_at=datetime.now(UTC))
    session.add_all([active, archived])
    await session.commit()

    visible = await list_projects(session)
    assert {p.slug for p in visible} == {"active"}

    everything = await list_projects(session, include_archived=True)
    assert {p.slug for p in everything} == {"active", "archived"}
