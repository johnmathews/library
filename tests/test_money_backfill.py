"""Selection for the amount backfill, its failure guard, and response handling."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import Settings, get_settings
from library.models import AmountKind, Document, DocumentSource, DocumentStatus
from library.money import backfill as backfill_module
from library.money.backfill import documents_needing_amount_kind, run_amount_backfill

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


def _seed(database_url: str, rows: list[tuple[Decimal | None, AmountKind | None]]) -> list[int]:
    async def _work(session: AsyncSession) -> list[int]:
        ids: list[int] = []
        for amount, kind in rows:
            marker = f"backfill:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                amount_total=amount,
                amount_kind=kind,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def test_only_amount_bearing_documents_without_a_kind_are_selected(
    api_database_url: str,
) -> None:
    needs, has_kind, no_amount = _seed(
        api_database_url,
        [
            (Decimal("10.00"), None),
            (Decimal("20.00"), AmountKind.PAYMENT_MADE),
            (None, None),
        ],
    )
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=None))
    )
    assert needs in selected
    assert has_kind not in selected
    assert no_amount not in selected


def test_the_limit_is_respected(api_database_url: str) -> None:
    _seed(api_database_url, [(Decimal("1.00"), None), (Decimal("2.00"), None)])
    selected = asyncio.run(
        _run(api_database_url, lambda s: documents_needing_amount_kind(s, limit=1))
    )
    assert len(selected) == 1


def test_a_classification_error_does_not_abort_the_run(
    api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live network error (or a DataError from an over-long reference)
    from one document must not take the rest of the run — every amount-bearing
    document that still has no ``amount_kind`` — with it. Without the
    per-document SAVEPOINT guard in ``run_amount_backfill``, an exception
    raised while classifying one document propagates straight out of the loop
    and the second, later-inserted document is never reached at all.
    """

    async def _seed_two(session: AsyncSession) -> list[tuple[int, str]]:
        seeded: list[tuple[int, str]] = []
        for amount in (Decimal("10.00"), Decimal("20.00")):
            marker = f"backfill-error:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                amount_total=amount,
            )
            session.add(doc)
            await session.flush()
            seeded.append((doc.id, marker))
        return seeded

    seeded = asyncio.run(_run(api_database_url, _seed_two))
    (failing_id, failing_title), (_other_id, other_title) = seeded
    assert failing_id < _other_id, "the failing document must be inserted (and so processed) first"
    calls: list[str | None] = []

    async def stub(
        settings: Settings,
        *,
        title: str | None,
        sender: str | None,
        kind: str | None,
        amount: str | None,
        currency: str | None,
        excerpt: str | None,
        client: object = None,
        backend: str = "api",
    ) -> tuple[str | None, str | None, int, int] | None:
        calls.append(title)
        if title == failing_title:
            raise RuntimeError("simulated classification failure")
        return "payment_due", None, 10, 5

    monkeypatch.setattr(backfill_module, "classify_amount", stub)
    settings = get_settings()
    classified, empty, skipped = asyncio.run(
        _run(api_database_url, lambda s: run_amount_backfill(s, settings, limit=None))
    )
    assert failing_title in calls, "the failing document must have been attempted"
    assert other_title in calls, "the run must have continued past the failing document"
    assert skipped >= 1
    assert classified >= 1
    assert empty == 0


# --- the amount classifier's response handling -------------------------------
#
# The first live `backfill-amounts --limit 5` against the real archive logged
# "amount classifier returned unparseable JSON" five times out of five and
# classified nothing. The model had wrapped its JSON in a ```json fence while
# the code called `messages.create()` and `json.loads`-ed the raw text. Every
# test below this line exists because none above it could have caught that:
# they all fed `_parse` a hand-written JSON string, and the API call shape was
# never exercised at all.


def test_a_fenced_payload_still_classifies() -> None:
    """The exact production shape: correct JSON inside a ```json fence."""
    payload = '```json\n{"amount_kind": "payment_due", "reference": "INV-1"}\n```'
    assert backfill_module._parse(payload) == ("payment_due", "INV-1")


def test_a_payload_wrapped_in_prose_still_classifies() -> None:
    payload = 'Here is the classification:\n{"amount_kind": "payment_made"}\nHope that helps.'
    assert backfill_module._parse(payload) == ("payment_made", None)


def test_a_payload_that_is_not_json_at_all_stays_undecided() -> None:
    """Undecided, not raised: the document stays in the queue for a later run."""
    assert backfill_module._parse("I could not tell.") == (None, None)


async def test_the_api_backend_asks_for_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins the fix: the API path must use `messages.parse` with the schema.

    A `messages.create` call returning fenced text is exactly what shipped, so
    this asserts the call shape and not merely the returned tuple — a stub that
    answered both methods would let the defect back in unnoticed.
    """
    seen: dict[str, object] = {}

    class _Messages:
        async def parse(self, **kwargs: object) -> object:
            seen.update(kwargs)
            return SimpleNamespace(
                parsed_output=backfill_module.AmountClassification(
                    amount_kind="payment_due", reference="INV-9"
                ),
                usage=SimpleNamespace(input_tokens=11, output_tokens=3),
            )

        async def create(self, **kwargs: object) -> object:  # pragma: no cover - must not run
            raise AssertionError("the API backend must use messages.parse, not messages.create")

    client = cast(Any, SimpleNamespace(messages=_Messages()))
    result = await backfill_module.classify_amount(
        Settings(),
        title="t",
        sender="s",
        kind="invoice",
        amount="9.99",
        currency="EUR",
        excerpt="x",
        client=client,
    )

    assert result == ("payment_due", "INV-9", 11, 3)
    assert seen["output_format"] is backfill_module.AmountClassification


async def test_an_unparseable_structured_response_raises() -> None:
    """`parsed_output` of None is a real failure, not a silent undecided."""

    class _Messages:
        async def parse(self, **kwargs: object) -> object:
            return SimpleNamespace(
                parsed_output=None, usage=SimpleNamespace(input_tokens=1, output_tokens=1)
            )

    client = cast(Any, SimpleNamespace(messages=_Messages()))
    with pytest.raises(backfill_module.AmountParseError):
        await backfill_module.classify_amount(
            Settings(),
            title="t",
            sender="s",
            kind="invoice",
            amount="9.99",
            currency="EUR",
            excerpt="x",
            client=client,
        )
