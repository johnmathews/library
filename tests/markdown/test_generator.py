"""Tests for the markdown generator with a mocked Anthropic client."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.config import Settings
from library.markdown.generator import MarkdownSkipped, generate_markdown
from library.markdown.schema import DocumentMarkdown, PageMarkdown


class _FakeMessages:
    def __init__(self, responses: list[DocumentMarkdown]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self._responses.pop(0)
        usage = SimpleNamespace(input_tokens=100, output_tokens=200)
        return SimpleNamespace(parsed_output=parsed, usage=usage)


class _FakeClient:
    def __init__(self, responses: list[DocumentMarkdown]) -> None:
        self.messages = _FakeMessages(responses)


def _settings(batch: int = 10) -> Settings:
    return Settings(_env_file=None, markdown_page_batch=batch, markdown_model="claude-haiku-4-5")


async def test_single_batch_assigns_absolute_pages() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient(
        [
            DocumentMarkdown(
                pages=[
                    PageMarkdown(page_number=1, markdown="# A"),
                    PageMarkdown(page_number=2, markdown="# B"),
                ]
            )
        ]
    )
    result = await generate_markdown(
        doc, "ocr", [b"img1", b"img2"], client=client, settings=_settings()
    )
    assert [(p.page_number, p.markdown) for p in result.pages] == [(1, "# A"), (2, "# B")]
    assert result.input_tokens == 100 and result.output_tokens == 200
    assert result.cost_usd > 0


async def test_batches_offset_page_numbers() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient(
        [
            DocumentMarkdown(
                pages=[
                    PageMarkdown(page_number=1, markdown="p1"),
                    PageMarkdown(page_number=2, markdown="p2"),
                ]
            ),
            DocumentMarkdown(pages=[PageMarkdown(page_number=1, markdown="p3")]),
        ]
    )
    result = await generate_markdown(
        doc, "ocr", [b"a", b"b", b"c"], client=client, settings=_settings(batch=2)
    )
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert [p.markdown for p in result.pages] == ["p1", "p2", "p3"]
    assert len(client.messages.calls) == 2


async def test_no_pages_raises_skip() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient([DocumentMarkdown(pages=[])])
    with pytest.raises(MarkdownSkipped):
        await generate_markdown(doc, "ocr", [b"a"], client=client, settings=_settings())
