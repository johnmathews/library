"""Model/migration tests for ``held_emails`` (hold-for-review queue).

Exercises the migration via the shared migrated database and the partial
unique index ``ix_held_emails_message_id_held``: at most one *open* (``held``)
row per ``message_id``, with resolved rows and NULL ``message_id`` rows exempt.
The database is session-scoped and shared, so every test uses unique
``message_id`` values instead of truncating the table.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import HeldEmail, HeldEmailStatus

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


def _message_id() -> str:
    return f"<{uuid.uuid4()}@example.com>"


def _held_email(**overrides: object) -> HeldEmail:
    base: dict[str, object] = {
        "message_id": _message_id(),
        "sender": "sender@example.com",
        "subject": "Quarterly invoice",
        "verdict": "llm_hold",
        "imap_folder": "INBOX",
    }
    base.update(overrides)
    return HeldEmail(**base)  # type: ignore[arg-type]


async def test_round_trip_with_trace(session: AsyncSession) -> None:
    """A HeldEmail persists and reads back with its JSON trace and defaults."""
    trace = {
        "message_id": "<abc@example.com>",
        "from": "sender@example.com",
        "items": [
            {"kind": "attachment", "filename": "invoice.pdf", "verdict": "keep"},
            {"kind": "body", "filename": None, "verdict": "filtered"},
        ],
    }
    received = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)
    held = _held_email(
        received_at=received,
        reason="newsletter blast",
        trace=trace,
        imap_uid="4711",
    )
    session.add(held)
    await session.commit()

    loaded = (await session.execute(select(HeldEmail).where(HeldEmail.id == held.id))).scalar_one()
    assert loaded.message_id == held.message_id
    assert loaded.sender == "sender@example.com"
    assert loaded.subject == "Quarterly invoice"
    assert loaded.received_at == received
    assert loaded.verdict == "llm_hold"
    assert loaded.reason == "newsletter blast"
    assert loaded.trace == trace
    assert loaded.imap_folder == "INBOX"
    assert loaded.imap_uid == "4711"
    assert loaded.status is HeldEmailStatus.HELD
    assert loaded.created_at is not None
    assert loaded.owner_id is None
    assert loaded.resolved_by_id is None
    assert loaded.resolved_at is None
    assert loaded.document_ids == []
    assert loaded.last_error is None


async def test_duplicate_held_message_id_rejected(session: AsyncSession) -> None:
    """Two open (held) rows with the same message_id violate the partial index."""
    message_id = _message_id()
    session.add(_held_email(message_id=message_id))
    await session.commit()

    session.add(_held_email(message_id=message_id))
    with pytest.raises(IntegrityError, match="ix_held_emails_message_id_held"):
        await session.commit()
    await session.rollback()


async def test_resolved_and_held_message_id_coexist(session: AsyncSession) -> None:
    """A dismissed row does not block a new held row for the same message_id."""
    message_id = _message_id()
    session.add(
        _held_email(
            message_id=message_id,
            status=HeldEmailStatus.DISMISSED,
            resolved_at=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        )
    )
    await session.commit()

    session.add(_held_email(message_id=message_id))
    await session.commit()

    rows = (
        (await session.execute(select(HeldEmail).where(HeldEmail.message_id == message_id)))
        .scalars()
        .all()
    )
    assert {row.status for row in rows} == {HeldEmailStatus.DISMISSED, HeldEmailStatus.HELD}


async def test_null_message_id_rows_coexist(session: AsyncSession) -> None:
    """The partial index excludes NULL message_ids: several held rows may lack one."""
    first = _held_email(message_id=None, subject="no message-id 1")
    second = _held_email(message_id=None, subject="no message-id 2")
    session.add_all([first, second])
    await session.commit()

    assert first.id != second.id
    assert first.status is HeldEmailStatus.HELD
    assert second.status is HeldEmailStatus.HELD
