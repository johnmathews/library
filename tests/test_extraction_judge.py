"""Unit tests for the extraction judge (Anthropic client mocked)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from library.config import Settings
from library.extraction.judge import FieldVerdict, JudgeResult, judge
from library.models import Document


def _doc() -> Document:
    # Normal constructor: mapped columns are data descriptors, so __new__ +
    # object.__setattr__ would not read back. tags is a relationship; pass [].
    return Document(
        ocr_text="Factuur Eneco totaal € 12,00",
        title="Eneco factuur",
        summary="s",
        amount_total=None,
        currency=None,
        document_date=None,
        due_date=None,
        expiry_date=None,
        language=None,
        kind_id=None,
        sender_id=None,
        tags=[],
        extra={},
    )


@pytest.mark.asyncio
async def test_judge_returns_parsed_verdicts() -> None:
    result = JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None)])
    response = SimpleNamespace(parsed_output=result)
    client = SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(return_value=response)))
    settings = Settings(anthropic_api_key="k")

    verdicts = await judge(_doc(), client=client, settings=settings)

    assert verdicts.verdicts[0].field == "title"
    assert verdicts.verdicts[0].verdict == "correct"
    client.messages.parse.assert_awaited_once()
