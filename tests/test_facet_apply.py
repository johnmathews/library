"""Turning proposals into rows: what is applied, what is withheld, what is queued."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import get_settings
from library.facets.apply import apply_proposals, document_fields, label_and_apply
from library.facets.labeller import LabelProposal
from library.facets.vocabulary import create_facet, create_value, document_labels
from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    FacetValueSuggestion,
    Kind,
    Sender,
)

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


def _setup(database_url: str) -> str:
    key = f"apply-{uuid.uuid4().hex[:8]}"

    async def _work(session: AsyncSession) -> None:
        await create_facet(session, key, "Apply")
        await create_value(session, key, "alpha", "Alpha")

    asyncio.run(_run(database_url, _work))
    return key


def test_a_confident_proposal_is_applied(api_database_url: str, seeded_document_id: int) -> None:
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "alpha", 0.9, "clear", None)]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {key: "alpha"}
    labels = asyncio.run(_run(api_database_url, lambda s: document_labels(s, seeded_document_id)))
    assert labels[key] == "alpha"


def test_a_low_confidence_proposal_is_withheld_not_guessed(
    api_database_url: str, seeded_document_id: int
) -> None:
    """A confidently wrong label silently moves money between charts. Withhold."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "alpha", 0.2, "unsure", None)]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {}
    assert key in outcome.unknown
    labels = asyncio.run(_run(api_database_url, lambda s: document_labels(s, seeded_document_id)))
    assert key not in labels


def test_a_suggestion_is_queued_and_no_value_is_created(
    api_database_url: str, seeded_document_id: int
) -> None:
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, None, 0.95, "a new idea", "telecoms")]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.suggested == ((key, "telecoms"),)
    rows = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(
                select(FacetValueSuggestion).where(
                    FacetValueSuggestion.document_id == seeded_document_id
                )
            ),
        )
    )
    assert [r.suggested_label for r in rows.scalars()] == ["telecoms"]


def test_applying_twice_is_idempotent(api_database_url: str, seeded_document_id: int) -> None:
    """Re-labelling must not raise on the suggestion unique constraint."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, None, 0.9, "again", "telecoms")]
    for _ in range(2):
        asyncio.run(
            _run(
                api_database_url,
                lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
            )
        )
    rows = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(
                select(FacetValueSuggestion).where(
                    FacetValueSuggestion.document_id == seeded_document_id
                )
            ),
        )
    )
    assert len(list(rows.scalars())) == 1


def test_label_and_apply_returns_none_for_a_missing_document(api_database_url: str) -> None:
    """A quiet skip, not an error — a backfill must not die on a stale id."""
    settings = get_settings()
    outcome = asyncio.run(
        _run(api_database_url, lambda s: label_and_apply(s, settings, 999_999_999))
    )
    assert outcome is None


def test_label_and_apply_returns_none_when_no_model_is_configured(
    api_database_url: str, seeded_document_id: int
) -> None:
    """No API key is a skip, mirroring series_insight.describe_series."""
    settings = get_settings().model_copy(update={"anthropic_api_key": None})
    outcome = asyncio.run(
        _run(api_database_url, lambda s: label_and_apply(s, settings, seeded_document_id))
    )
    assert outcome is None


def test_document_fields_maps_every_column_to_the_right_attribute(api_database_url: str) -> None:
    """Guards the select-tuple order. A swap here produces no error, only wrong
    facts fed to the model, so nothing else would catch it."""

    async def _seed(session: AsyncSession) -> int:
        sender = Sender(name=f"FieldsVendor-{uuid.uuid4().hex[:8]}")
        session.add(sender)
        await session.flush()
        kind = (await session.execute(select(Kind).limit(1))).scalar_one()
        marker = f"fields:{uuid.uuid4()}"
        doc = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            status=DocumentStatus.INDEXED,
            title="A distinctive title",
            summary="A distinctive summary.",
            sender_id=sender.id,
            kind_id=kind.id,
            amount_total=Decimal("12.34"),
            currency="EUR",
            ocr_text="Distinctive body text.",
        )
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _seed))
    fields = asyncio.run(_run(api_database_url, lambda s: document_fields(s, document_id)))
    assert fields is not None
    assert fields.title == "A distinctive title"
    assert fields.summary == "A distinctive summary."
    assert fields.sender is not None and fields.sender.startswith("FieldsVendor-")
    assert fields.kind is not None
    assert fields.amount == "12.34"
    assert fields.currency == "EUR"
    assert fields.excerpt == "Distinctive body text."


def test_document_fields_returns_none_for_a_missing_document(api_database_url: str) -> None:
    result = asyncio.run(_run(api_database_url, lambda s: document_fields(s, 999_999_999)))
    assert result is None


def test_a_proposal_can_be_both_applied_and_suggested(
    api_database_url: str, seeded_document_id: int
) -> None:
    """Recording a suggestion must not short-circuit the label decision."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "alpha", 0.9, "confident", "telecoms")]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {key: "alpha"}
    assert outcome.suggested == ((key, "telecoms"),)


def test_a_value_missing_from_the_vocabulary_is_unknown_not_a_crash(
    api_database_url: str, seeded_document_id: int
) -> None:
    """set_document_label raises UnknownValueError when the vocabulary changed
    between parsing and writing. A run over the archive must not die on it."""
    key = _setup(api_database_url)
    proposals = [LabelProposal(key, "vanished", 0.99, "was valid at parse time", None)]
    outcome = asyncio.run(
        _run(
            api_database_url,
            lambda s: apply_proposals(s, seeded_document_id, proposals, min_confidence=0.6),
        )
    )
    assert outcome.applied == {}
    assert key in outcome.unknown
