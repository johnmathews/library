"""Tests for the standalone LLM matter-classifier pass.

The Anthropic client is faked (no network); the DB is real, bound to
``api_database_url``, so we exercise the merge-only apply against
``document.matters`` and the ``extra`` provenance stamp end-to-end.
"""

import hashlib
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

import library.matter_classifier as matter_classifier
from library.config import Settings
from library.matter_classifier import MatterClassificationResult, apply_matter_classification
from library.models import Document, DocumentSource, Matter

_UNSET = object()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        matter_classifier_model="claude-haiku-4-5",
        matter_classification_daily_budget_usd=1.0,
    )


class _FakeMessages:
    def __init__(self, response: object | None, error: Exception | None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    async def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(
        self,
        *,
        slugs: list[str] | None = None,
        parsed: object = _UNSET,
        error: Exception | None = None,
    ) -> None:
        if parsed is _UNSET:
            parsed = (
                None if error is not None else MatterClassificationResult(matched_slugs=slugs or [])
            )
        response = SimpleNamespace(
            parsed_output=parsed,
            usage=SimpleNamespace(input_tokens=200, output_tokens=20),
        )
        self.messages = _FakeMessages(response, error)


@pytest.fixture(autouse=True)
def _cheap_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: no spend today, so the gate is open unless a test overrides it."""

    async def _spend(session: object, event: object) -> float:
        return 0.0

    monkeypatch.setattr(matter_classifier, "todays_spend_usd", _spend)


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
    matters: list[Matter] | None = None,
    extra: dict | None = None,
) -> Document:
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        title="Aegon car insurance renewal",
        summary="Your annual motor policy premium is due.",
        matters=matters or [],
        extra=extra or {},
    )
    session.add(document)
    await session.commit()
    return document


async def _vocab(session: AsyncSession) -> dict[str, Matter]:
    car = Matter(slug="car-insurance", name="Car insurance", hint="motor policies")
    health = Matter(slug="health-insurance", name="Health insurance")
    subs = Matter(slug="subscriptions", name="Subscriptions")
    session.add_all([car, health, subs])
    await session.commit()
    return {"car-insurance": car, "health-insurance": health, "subscriptions": subs}


@pytest.mark.integration
async def test_known_slugs_attached_merge_only(session: AsyncSession) -> None:
    vocab = await _vocab(session)
    # Pre-existing matter must survive; a newly returned one is added.
    document = await _document(session, "doc1", matters=[vocab["subscriptions"]])

    client = _FakeClient(slugs=["car-insurance"])
    await apply_matter_classification(session, document, _settings(), client=client)

    assert {m.slug for m in document.matters} == {"subscriptions", "car-insurance"}
    stamp = document.extra["matter_classification"]
    assert stamp["matched_slugs"] == ["car-insurance"]
    assert stamp["attached_slugs"] == ["car-insurance"]
    assert stamp["model"] == "claude-haiku-4-5"
    assert stamp["prompt_version"] == matter_classifier.PROMPT_VERSION
    assert stamp["cost_usd"] > 0
    assert stamp["input_tokens"] == 200
    assert stamp["output_tokens"] == 20


@pytest.mark.integration
async def test_already_attached_slug_not_duplicated(session: AsyncSession) -> None:
    vocab = await _vocab(session)
    document = await _document(session, "doc2", matters=[vocab["car-insurance"]])

    client = _FakeClient(slugs=["car-insurance"])
    await apply_matter_classification(session, document, _settings(), client=client)

    assert [m.slug for m in document.matters] == ["car-insurance"]  # no duplicate
    assert document.extra["matter_classification"]["attached_slugs"] == []


@pytest.mark.integration
async def test_unknown_slugs_ignored(session: AsyncSession) -> None:
    await _vocab(session)
    document = await _document(session, "doc3")

    client = _FakeClient(slugs=["car-insurance", "hallucinated-matter"])
    await apply_matter_classification(session, document, _settings(), client=client)

    # Only the real slug is attached; the hallucinated one is neither created nor attached.
    assert {m.slug for m in document.matters} == {"car-insurance"}
    all_matters = (await session.execute(Matter.__table__.select())).all()
    assert "hallucinated-matter" not in {row.slug for row in all_matters}
    assert document.extra["matter_classification"]["attached_slugs"] == ["car-insurance"]


@pytest.mark.integration
async def test_empty_result_attaches_nothing(session: AsyncSession) -> None:
    await _vocab(session)
    document = await _document(session, "doc4")

    client = _FakeClient(slugs=[])
    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []
    assert document.extra["matter_classification"]["attached_slugs"] == []


@pytest.mark.integration
async def test_user_edited_matters_skipped(session: AsyncSession) -> None:
    await _vocab(session)
    document = await _document(session, "doc5", extra={"user_edited_fields": ["matters"]})

    client = _FakeClient(slugs=["car-insurance"])
    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []  # untouched
    assert client.messages.calls == []  # no API call
    assert "matter_classification" not in document.extra  # no provenance write


@pytest.mark.integration
async def test_empty_vocabulary_skips_without_calling(session: AsyncSession) -> None:
    # No matters defined at all.
    document = await _document(session, "doc6")

    client = _FakeClient(slugs=["car-insurance"])
    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []
    assert client.messages.calls == []  # nothing to classify into
    assert "matter_classification" not in document.extra


@pytest.mark.integration
async def test_budget_exceeded_skips_fail_open(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _vocab(session)
    document = await _document(session, "doc7")

    async def _over(session: object, event: object) -> float:
        return 99.0

    monkeypatch.setattr(matter_classifier, "todays_spend_usd", _over)
    client = _FakeClient(slugs=["car-insurance"])

    # Must not raise.
    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []
    assert client.messages.calls == []  # over budget: no API call
    assert "matter_classification" not in document.extra


@pytest.mark.integration
async def test_client_error_fails_open(session: AsyncSession) -> None:
    await _vocab(session)
    document = await _document(session, "doc8")

    client = _FakeClient(error=RuntimeError("boom"))

    # Fail-open: returns without raising and without a partial write.
    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []
    assert "matter_classification" not in document.extra


@pytest.mark.integration
async def test_unparseable_response_fails_open(session: AsyncSession) -> None:
    await _vocab(session)
    document = await _document(session, "doc9")

    client = _FakeClient(parsed=None)  # parsed_output is None

    await apply_matter_classification(session, document, _settings(), client=client)

    assert document.matters == []
    assert "matter_classification" not in document.extra


@pytest.mark.integration
async def test_records_spend_event_and_real_budget_accrues(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful pass records a matter_classification_completed event carrying
    cost_usd, so the REAL todays_spend_usd accrues and the daily budget gates a
    later call. Guards the no-op-budget regression: the gate reads these events,
    so the classifier must emit one."""
    from sqlalchemy import select

    from library.extraction.apply import todays_spend_usd
    from library.matter_classifier import CLASSIFICATION_EVENT
    from library.models import IngestionEvent

    # Use the REAL spend function, not the autouse stub, so accrual is exercised.
    monkeypatch.setattr(matter_classifier, "todays_spend_usd", todays_spend_usd)

    await _vocab(session)
    first = await _document(session, "budget-doc-1")
    # A budget so small any real spend exceeds it after one call.
    settings = Settings(
        _env_file=None,
        matter_classifier_model="claude-haiku-4-5",
        matter_classification_daily_budget_usd=0.0000001,
    )

    await apply_matter_classification(session, first, settings, client=_FakeClient(slugs=[]))
    await session.commit()

    events = (
        (
            await session.execute(
                select(IngestionEvent).where(
                    IngestionEvent.document_id == first.id,
                    IngestionEvent.event == CLASSIFICATION_EVENT,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert float(events[0].detail["cost_usd"]) > 0

    # Second document: the real gate now sees today's spend over the tiny budget.
    second = await _document(session, "budget-doc-2")
    client = _FakeClient(slugs=["car-insurance"])
    await apply_matter_classification(session, second, settings, client=client)
    assert client.messages.calls == []  # gated by accrued spend
    assert "matter_classification" not in second.extra


def test_schema_forbids_unknown_fields() -> None:
    with pytest.raises(ValueError):
        MatterClassificationResult(matched_slugs=[], bogus="x")  # type: ignore[call-arg]
    result = MatterClassificationResult(matched_slugs=["car-insurance"])
    assert result.matched_slugs == ["car-insurance"]
