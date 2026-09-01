"""The backfill selects the right documents. The model itself is stubbed."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import Settings, get_settings
from library.facets import backfill as backfill_module
from library.facets.apply import LabellingOutcome
from library.facets.backfill import documents_needing_labels, run_backfill
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.models import Document, DocumentSource, DocumentStatus

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


def _seed_document(database_url: str) -> int:
    """One more indexed document, so a run has something after the failing one."""

    async def _work(session: AsyncSession) -> int:
        marker = f"backfill:{uuid.uuid4()}"
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            status=DocumentStatus.INDEXED,
        )
        session.add(document)
        await session.flush()
        return document.id

    return asyncio.run(_run(database_url, _work))


def test_an_unlabelled_document_is_selected(api_database_url: str, seeded_document_id: int) -> None:
    ids = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    assert seeded_document_id in ids


def test_a_labelled_document_is_skipped_unless_relabelling(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = f"backfill-{uuid.uuid4().hex[:8]}"

    async def _label(session: AsyncSession) -> None:
        await create_facet(session, key, "Backfill")
        await create_value(session, key, "alpha", "Alpha")
        await set_document_label(session, seeded_document_id, key, "alpha")

    asyncio.run(_run(api_database_url, _label))

    skipped = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    included = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=True, limit=None))
    )
    assert seeded_document_id not in skipped
    assert seeded_document_id in included


def test_one_documents_database_error_does_not_abort_the_run(
    api_database_url: str, seeded_document_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A statement-level error on one document must not poison the whole run.

    Without the per-document SAVEPOINT the aborted transaction makes every
    later ``session.commit()`` raise InFailedSqlTransaction, so a single
    over-long suggestion would end an archive-wide pass.
    """
    _seed_document(api_database_url)  # a second unlabelled document to run on
    ids = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_labels(s, relabel=False, limit=None))
    )
    assert len(ids) >= 2, "this test needs at least two unlabelled documents"
    failing = ids[0]
    seen: list[int] = []

    async def stub(
        session: AsyncSession,
        settings: Settings,
        document_id: int,
        *,
        client: object = None,
        backend: str = "api",
    ) -> LabellingOutcome:
        seen.append(document_id)
        if document_id == failing:
            # Stands in for the StringDataRightTruncation an over-long
            # suggested_label produced: any statement-level error will do.
            await session.execute(text("SELECT 1 / 0"))
        return LabellingOutcome(
            document_id=document_id, applied={"category": "alpha"}, unknown=(), suggested=()
        )

    monkeypatch.setattr(backfill_module, "label_and_apply", stub)
    settings = get_settings()
    labelled, empty, skipped = asyncio.run(
        _run(
            api_database_url,
            lambda s: run_backfill(s, settings, relabel=False, limit=None),
        )
    )
    assert seen == ids, "the run stopped at the failing document"
    assert skipped == 1
    assert empty == 0
    assert labelled == len(ids) - 1


def test_a_document_with_nothing_applied_counts_as_empty_not_labelled(
    api_database_url: str, seeded_document_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production incident: every document logged an unparseable payload
    and applied nothing, yet the run reported ``labelled 5, skipped 0``. A
    document that ran without error but produced zero applied labels must be
    counted distinctly, not folded into ``labelled``.
    """

    async def stub(
        session: AsyncSession,
        settings: Settings,
        document_id: int,
        *,
        client: object = None,
        backend: str = "api",
    ) -> LabellingOutcome:
        return LabellingOutcome(
            document_id=document_id, applied={}, unknown=("category",), suggested=()
        )

    monkeypatch.setattr(backfill_module, "label_and_apply", stub)
    settings = get_settings()
    labelled, empty, skipped = asyncio.run(
        _run(
            api_database_url,
            lambda s: run_backfill(s, settings, relabel=False, limit=None),
        )
    )
    assert labelled == 0
    assert empty == 1
    assert skipped == 0
