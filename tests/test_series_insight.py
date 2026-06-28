"""Tests for LLM series-description generation, caching, and serialisation."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.config import Settings
from library.models import (
    Document,
    DocumentSource,
    Kind,
    OverrideAction,
    Sender,
    SeriesInsight,
    SeriesMembershipOverride,
)
from library.search import DocumentFilters
from library.series import SeriesSummary, serialise_summary, summarize_series
from library.series_insight import (
    MAX_OVERRIDE_EXAMPLES,
    OverrideExample,
    build_series_prompt,
    generate_description,
    load_override_examples,
    refresh_series_insight,
)

pytestmark = pytest.mark.integration


# --- Fake Anthropic client -------------------------------------------------


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Response:
    content: list[_Block]
    usage: _Usage


class FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        return _Response(content=[_Block(self._text)], usage=_Usage(420, 33))


class FakeAnthropic:
    def __init__(self, text: str = "Bills have risen about 30% since January.") -> None:
        self.messages = FakeMessages(text)


# --- Fixtures + seeding ----------------------------------------------------


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(SeriesInsight))
        await session.execute(delete(Document))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


async def _sender(session: AsyncSession, name: str) -> Sender:
    existing = (
        await session.execute(select(Sender).where(Sender.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


async def _seed(
    session: AsyncSession,
    marker: str,
    *,
    sender: Sender,
    kind_slug: str,
    document_date: date,
    amount: str,
    title: str | None = None,
    currency: str = "EUR",
) -> int:
    kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        title=title,
        document_date=document_date,
        amount_total=Decimal(amount),
        currency=currency,
    )
    session.add(document)
    await session.commit()
    return document.id


def _settings() -> Settings:
    return Settings(series_min_documents=3, series_typical_pct=0.10, series_flat_pct=0.05)


def _FakeSummary() -> SeriesSummary:
    """A minimal ``status:"ok"`` summary for pure prompt-construction tests."""
    return SeriesSummary(
        status="ok",
        sender="Vattenfall",
        kind="utility-bill",
        sender_id=1,
        kind_id=2,
        currency="EUR",
        other_currencies=[],
        cadence="monthly",
        count=3,
        distribution=None,
        reference=None,
        trend=None,
        year_over_year=None,
        document_ids=[1, 2, 3],
        points=[],
        titles={},
        description=None,
    )


async def _seed_three(session: AsyncSession, sender_name: str = "Vattenfall") -> Sender:
    sender = await _sender(session, sender_name)
    await _seed(
        session,
        "a",
        sender=sender,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 3),
        amount="100.00",
        title="Jan bill",
    )
    await _seed(
        session,
        "b",
        sender=sender,
        kind_slug="utility-bill",
        document_date=date(2025, 2, 2),
        amount="100.00",
        title="Feb bill",
    )
    await _seed(
        session,
        "c",
        sender=sender,
        kind_slug="utility-bill",
        document_date=date(2025, 3, 4),
        amount="130.00",
        title="Mar bill",
    )
    return sender


# --- Tests -----------------------------------------------------------------


async def test_build_series_prompt_includes_stats_and_timeline(session: AsyncSession) -> None:
    await _seed_three(session)
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    prompt = build_series_prompt(summary)
    assert "Vattenfall" in prompt
    assert "utility-bill" in prompt
    assert "EUR" in prompt
    assert "2025-01-03=100.00" in prompt  # timeline carries dated amounts


def test_build_series_prompt_renders_override_hints() -> None:
    """Pure prompt construction: pinned/excluded examples become labelled hints."""
    overrides = [
        OverrideExample(action=OverrideAction.PIN, document_id=12, title="Stray March bill"),
        OverrideExample(action=OverrideAction.EXCLUDE, document_id=27, title="One-off deposit"),
    ]
    prompt = build_series_prompt(_FakeSummary(), overrides=overrides)
    assert "manually" in prompt.lower()
    assert "Stray March bill" in prompt
    assert "#12" in prompt
    assert "One-off deposit" in prompt
    assert "#27" in prompt


def test_build_series_prompt_has_no_override_block_when_empty() -> None:
    prompt = build_series_prompt(_FakeSummary())
    assert "manually" not in prompt.lower()


async def test_load_override_examples_caps_per_direction(session: AsyncSession) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    # More pins than the cap allows.
    for n in range(MAX_OVERRIDE_EXAMPLES + 3):
        doc_id = await _seed(
            session,
            f"pin-{n}",
            sender=sender,
            kind_slug="utility-bill",
            document_date=date(2025, 6, 1),
            amount="90.00",
            title=f"Pinned {n}",
        )
        session.add(
            SeriesMembershipOverride(
                sender_id=sender.id,
                kind_id=kind_id,
                currency="EUR",
                document_id=doc_id,
                action=OverrideAction.PIN,
            )
        )
    await session.commit()
    examples = await load_override_examples(session, sender.id, kind_id, "EUR")
    assert len(examples) == MAX_OVERRIDE_EXAMPLES
    assert all(e.action == OverrideAction.PIN for e in examples)


async def test_load_override_examples_reflected_in_prompt(session: AsyncSession) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    pinned = await _seed(
        session,
        "outsider",
        sender=sender,
        kind_slug="invoice",
        document_date=date(2025, 4, 1),
        amount="200.00",
        title="Misfiled bill",
    )
    session.add(
        SeriesMembershipOverride(
            sender_id=sender.id,
            kind_id=kind_id,
            currency="EUR",
            document_id=pinned,
            action=OverrideAction.PIN,
        )
    )
    await session.commit()
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    examples = await load_override_examples(session, sender.id, kind_id, summary.currency)
    prompt = build_series_prompt(summary, overrides=examples)
    assert "Misfiled bill" in prompt


async def test_generate_description_returns_text_and_tokens(session: AsyncSession) -> None:
    await _seed_three(session)
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
    )
    client = FakeAnthropic("Steady around €100, then a spike to €130 in March.")
    text, in_tok, out_tok = await generate_description(client, "claude-haiku-4-5", summary)
    assert text == "Steady around €100, then a spike to €130 in March."
    assert (in_tok, out_tok) == (420, 33)
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"


async def test_refresh_inserts_row(session: AsyncSession) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    client = FakeAnthropic("Energy bills rose 30% in March.")

    row = await refresh_series_insight(session, _settings(), sender.id, kind_id, client=client)
    assert row is not None
    assert row.description == "Energy bills rose 30% in March."
    assert row.currency == "EUR"
    assert row.member_count == 3
    assert row.model == "claude-haiku-4-5"
    assert row.input_tokens == 420 and row.output_tokens == 33
    assert row.cost_usd > 0  # haiku is priced in the table

    rows = (await session.execute(select(SeriesInsight))).scalars().all()
    assert len(rows) == 1


async def test_refresh_is_idempotent_upsert(session: AsyncSession) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )

    await refresh_series_insight(
        session, _settings(), sender.id, kind_id, client=FakeAnthropic("First version.")
    )
    await refresh_series_insight(
        session, _settings(), sender.id, kind_id, client=FakeAnthropic("Updated version.")
    )

    rows = (await session.execute(select(SeriesInsight))).scalars().all()
    assert len(rows) == 1  # second call updated, not inserted
    assert rows[0].description == "Updated version."


async def test_refresh_updates_preexisting_row_via_on_conflict(session: AsyncSession) -> None:
    """A row already present (e.g. from a concurrent job) is updated, not duplicated."""
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    # Simulate a competing job having already written a (stale) row.
    session.add(
        SeriesInsight(
            sender_id=sender.id,
            kind_id=kind_id,
            currency="EUR",
            description="Stale prose from a previous run.",
            model="claude-haiku-4-5",
            member_count=2,
        )
    )
    await session.commit()

    row = await refresh_series_insight(
        session, _settings(), sender.id, kind_id, client=FakeAnthropic("Fresh prose.")
    )
    assert row is not None
    assert row.description == "Fresh prose."
    assert row.member_count == 3
    rows = (await session.execute(select(SeriesInsight))).scalars().all()
    assert len(rows) == 1  # ON CONFLICT updated the existing row


async def test_refresh_skips_insufficient_series(session: AsyncSession) -> None:
    sender = await _sender(session, "Eneco")
    await _seed(
        session,
        "only",
        sender=sender,
        kind_slug="utility-bill",
        document_date=date(2025, 1, 1),
        amount="50.00",
    )
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    client = FakeAnthropic()

    row = await refresh_series_insight(session, _settings(), sender.id, kind_id, client=client)
    assert row is None
    assert client.messages.calls == []  # no LLM call when stats are insufficient
    assert (await session.execute(select(SeriesInsight))).scalars().all() == []


async def test_refresh_skips_when_extraction_disabled(session: AsyncSession) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    settings = Settings(
        series_min_documents=3,
        series_typical_pct=0.10,
        series_flat_pct=0.05,
        extraction_enabled=False,
    )
    client = FakeAnthropic()
    row = await refresh_series_insight(session, settings, sender.id, kind_id, client=client)
    assert row is None
    assert client.messages.calls == []


async def test_summarize_attaches_cached_description_and_serialises_titles(
    session: AsyncSession,
) -> None:
    sender = await _seed_three(session)
    kind_id = (
        (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one().id
    )
    await refresh_series_insight(
        session,
        _settings(),
        sender.id,
        kind_id,
        client=FakeAnthropic("Cached prose about the energy bills."),
    )

    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
        reference="latest",
    )
    assert summary.description == "Cached prose about the energy bills."
    assert summary.sender_id == sender.id
    assert summary.kind_id == kind_id

    body = serialise_summary(summary, include_points=True)
    assert body["description"] == "Cached prose about the energy bills."
    assert body["sender_id"] == sender.id
    assert body["kind_id"] == kind_id
    titles = {point["title"] for point in body["points"]}
    assert {"Jan bill", "Feb bill", "Mar bill"} == titles


async def test_serialise_omits_description_when_absent(session: AsyncSession) -> None:
    await _seed_three(session)
    summary = await summarize_series(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", sender_contains="vattenfall"),
        settings=_settings(),
    )
    body = serialise_summary(summary)
    assert summary.description is None
    assert "description" not in body
