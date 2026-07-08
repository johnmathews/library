"""Unit tests for the optional per-email LLM label pass (library.email_label).

Pure unit tests: the Anthropic client and the budget query are faked, so no
network and no database. The wiring into poll_mailbox is covered in
tests/test_email_ingest.py.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import library.email_label as email_label
from library.config import Settings
from library.email_label import (
    EmailLabelResult,
    ItemLabel,
    LabelItem,
    label_email_items,
)


def _settings() -> Settings:
    return Settings(email_label_model="claude-haiku-4-5", email_label_daily_budget_usd=2.0)


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
    def __init__(self, *, parsed: object | None = None, error: Exception | None = None) -> None:
        response = SimpleNamespace(
            parsed_output=parsed,
            usage=SimpleNamespace(input_tokens=120, output_tokens=40),
        )
        self.messages = _FakeMessages(response, error)


@pytest.fixture(autouse=True)
def _cheap_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: no spend today, so the budget gate is open unless overridden."""

    async def _spend(session: object, event: str) -> float:
        return 0.0

    monkeypatch.setattr(email_label, "todays_spend_usd", _spend)


ITEMS = [
    LabelItem(index=0, filename="invoice.pdf", mime="application/pdf", size=50_000),
    LabelItem(index=1, filename="logo.png", mime="image/png", size=1_200),
]


async def _label(client: _FakeClient, items: list[LabelItem] = ITEMS) -> object:
    return await label_email_items(
        session=object(),
        client=client,  # type: ignore[arg-type]
        settings=_settings(),
        subject="Your invoice",
        sender="biller@example.com",
        body_snippet="Please find the invoice attached.",
        items=items,
    )


def test_schema_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ItemLabel(index=0, verdict="keep", bogus="x")  # type: ignore[call-arg]
    # A valid parse round-trips.
    result = EmailLabelResult(items=[ItemLabel(index=0, verdict="keep")])
    assert result.items[0].verdict == "keep"


async def test_verdicts_mapped_by_index() -> None:
    parsed = EmailLabelResult(
        items=[
            ItemLabel(index=0, verdict="keep"),
            ItemLabel(index=1, verdict="probably_noise", reason="signature logo"),
        ]
    )
    outcome = await _label(_FakeClient(parsed=parsed))

    assert outcome.skip_reason is None
    assert outcome.verdicts == {0: ("keep", None), 1: ("probably_noise", "signature logo")}
    assert outcome.usage is not None
    assert outcome.usage.item_count == 2
    assert outcome.usage.cost_usd > 0


async def test_budget_gate_skips_without_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _over(session: object, event: str) -> float:
        return 99.0

    monkeypatch.setattr(email_label, "todays_spend_usd", _over)
    client = _FakeClient(parsed=EmailLabelResult(items=[]))

    outcome = await _label(client)

    assert outcome.skip_reason == "budget"
    assert outcome.verdicts == {}
    assert outcome.usage is None
    assert client.messages.calls == []  # no API call when over budget


async def test_api_error_fails_open() -> None:
    outcome = await _label(_FakeClient(error=RuntimeError("boom")))

    assert outcome.skip_reason == "error"
    assert outcome.verdicts == {}  # keep everything
    assert outcome.usage is None


async def test_budget_read_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # A DB hiccup in the budget read must NOT propagate — the label pass runs
    # before the ingest loop, so a raised exception would abort the whole message
    # and leave real attachments un-ingested. It must keep everything instead.
    async def _boom(session: object, event: str) -> float:
        raise RuntimeError("db down")

    monkeypatch.setattr(email_label, "todays_spend_usd", _boom)
    client = _FakeClient(parsed=EmailLabelResult(items=[ItemLabel(index=0, verdict="keep")]))

    outcome = await _label(client)

    assert outcome.skip_reason == "error"
    assert outcome.verdicts == {}  # keep everything
    assert client.messages.calls == []  # never reached the API


async def test_no_parseable_output_fails_open() -> None:
    outcome = await _label(_FakeClient(parsed=None))

    assert outcome.skip_reason == "error"
    assert outcome.verdicts == {}


async def test_index_mismatch_discarded_but_billed() -> None:
    # The model returns a verdict for an index we did not ask about: untrustworthy,
    # so keep everything — but the call still cost money, so usage is reported.
    parsed = EmailLabelResult(items=[ItemLabel(index=0, verdict="probably_noise", reason="x")])
    outcome = await _label(_FakeClient(parsed=parsed))  # ITEMS has indices {0, 1}

    assert outcome.skip_reason == "error"
    assert outcome.verdicts == {}
    assert outcome.usage is not None  # billed even though discarded


async def test_no_items_returns_empty() -> None:
    outcome = await _label(_FakeClient(parsed=EmailLabelResult(items=[])), items=[])

    assert outcome == email_label.LabelOutcome({}, None, None)
