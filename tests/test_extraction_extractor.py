"""Tests for the Claude extractor with a mocked Anthropic client.

The SDK client object is mocked directly (``messages.parse`` as an
AsyncMock) — no HTTP-level mocking and no live API calls.
"""

import base64
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from library import storage
from library.config import Settings, get_settings
from library.extraction.extractor import (
    MAX_TEXT_CHARS_LONG,
    SYSTEM_PROMPT,
    ExtractionSkipped,
    extract,
)
from library.extraction.schema import ExtractedMetadata
from library.models import Document, DocumentSource


def make_metadata(**overrides: Any) -> ExtractedMetadata:
    base: dict[str, Any] = {
        "kind_slug": "invoice",
        "sender_name": "Eneco",
        "recipient_name": "John",
        "title": "Energierekening mei 2026",
        "summary": "Maandfactuur voor energie.",
        "document_date": "2026-05-15",
        "amount_total": "123.45",
        "currency": "EUR",
        "due_date": None,
        "expiry_date": None,
        "language": "nld",
        "tags": ["energie"],
        "confidence": "high",
        "reasoning_note": None,
    }
    base.update(overrides)
    return ExtractedMetadata.model_validate(base)


def make_response(
    metadata: ExtractedMetadata | None, input_tokens: int = 1_000, output_tokens: int = 200
) -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=metadata,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def make_client(*responses: object) -> SimpleNamespace:
    """A fake AsyncAnthropic exposing only messages.parse."""
    return SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(side_effect=responses)))


def make_document(mime_type: str = "application/pdf", sha256: str = "0" * 64) -> Document:
    return Document(sha256=sha256, mime_type=mime_type, source=DocumentSource.UPLOAD)


@pytest.fixture
def settings() -> Settings:
    return Settings(anthropic_api_key="test-key")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def test_happy_path_uses_primary_model_once(settings: Settings) -> None:
    client = make_client(make_response(make_metadata()))

    outcome = await extract(
        make_document(),
        "Factuur Eneco mei 2026, totaal 123,45 EUR",
        client=client,
        settings=settings,
    )

    assert client.messages.parse.await_count == 1
    call = client.messages.parse.await_args
    assert call.kwargs["model"] == "claude-haiku-4-5"
    assert call.kwargs["output_format"] is ExtractedMetadata
    assert outcome.model == "claude-haiku-4-5"
    assert outcome.escalated is False
    assert outcome.input_mode == "text"
    # 1000 in @ $1/MTok + 200 out @ $5/MTok.
    assert outcome.cost_usd == pytest.approx(0.002)
    assert outcome.input_tokens == 1_000
    assert outcome.output_tokens == 200


async def test_low_confidence_escalates_to_sonnet(settings: Settings) -> None:
    client = make_client(
        make_response(make_metadata(confidence="low")),
        make_response(make_metadata(confidence="high"), input_tokens=2_000, output_tokens=400),
    )

    outcome = await extract(
        make_document(), "garbled but long enough text", client=client, settings=settings
    )

    assert client.messages.parse.await_count == 2
    models = [call.kwargs["model"] for call in client.messages.parse.await_args_list]
    assert models == ["claude-haiku-4-5", "claude-sonnet-4-6"]
    assert outcome.escalated is True
    assert outcome.model == "claude-sonnet-4-6"
    assert outcome.metadata.confidence == "high"
    # Both calls' usage is recorded: haiku 0.002 + sonnet (2000*3 + 400*15)/1e6.
    assert outcome.cost_usd == pytest.approx(0.002 + 0.012)
    assert outcome.input_tokens == 3_000
    assert outcome.output_tokens == 600


async def test_parse_failure_escalates_to_sonnet(settings: Settings) -> None:
    client = make_client(
        ValueError("response did not match the schema"),
        make_response(make_metadata()),
    )

    outcome = await extract(
        make_document(), "perfectly fine ocr text here", client=client, settings=settings
    )

    assert client.messages.parse.await_count == 2
    assert outcome.escalated is True
    assert outcome.model == "claude-sonnet-4-6"


async def test_empty_parsed_output_escalates(settings: Settings) -> None:
    client = make_client(make_response(None), make_response(make_metadata()))

    outcome = await extract(
        make_document(), "perfectly fine ocr text here", client=client, settings=settings
    )

    assert client.messages.parse.await_count == 2
    assert outcome.escalated is True


async def test_api_error_propagates(settings: Settings) -> None:
    client = make_client(RuntimeError("api exploded"))

    with pytest.raises(RuntimeError, match="api exploded"):
        await extract(
            make_document(), "perfectly fine ocr text here", client=client, settings=settings
        )


async def test_long_ocr_text_is_sampled(settings: Settings) -> None:
    client = make_client(make_response(make_metadata()))

    # Distinctive head and a tail marker placed well clear of the very end so
    # it survives the bounded join even after the cap.
    head = "HEADMARKER" + "a" * 90
    tail_marker = "TAILMARKER"
    source = head + "b" * (50_000 - len(head) - len(tail_marker) - 200) + tail_marker + "c" * 200

    await extract(make_document(), source, client=client, settings=settings)

    content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "text"
    sampled = content[0]["text"]
    # Sampling leaves explicit discontinuity markers and stays within budget.
    assert "[...]" in sampled
    assert len(sampled) <= MAX_TEXT_CHARS_LONG
    # Head is preserved at the very start.
    assert sampled.startswith("HEADMARKER")
    # A marker from near the end proves coverage extends well past the head.
    assert tail_marker in sampled


async def test_short_text_sent_whole(settings: Settings) -> None:
    client = make_client(make_response(make_metadata()))
    source = "Short but usable OCR text well under the cap."

    await extract(make_document(), source, client=client, settings=settings)

    content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"] == source
    assert "[...]" not in content[0]["text"]


async def test_empty_ocr_text_sends_pdf_document_block(settings: Settings, data_dir: Path) -> None:
    raw = b"%PDF-1.4 tiny fixture"
    stored = storage.store(raw)
    client = make_client(make_response(make_metadata()))

    outcome = await extract(
        make_document(sha256=stored.sha256), "   ", client=client, settings=settings
    )

    content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(content[0]["source"]["data"]) == raw
    assert outcome.input_mode == "document"


async def test_empty_ocr_text_sends_image_block_for_jpeg(
    settings: Settings, data_dir: Path
) -> None:
    raw = b"\xff\xd8\xff fake jpeg"
    stored = storage.store(raw)
    client = make_client(make_response(make_metadata()))

    outcome = await extract(
        make_document(mime_type="image/jpeg", sha256=stored.sha256),
        "",
        client=client,
        settings=settings,
    )

    content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert outcome.input_mode == "image"


async def test_heic_uses_derived_jpeg_conversion(settings: Settings, data_dir: Path) -> None:
    raw = b"heic original bytes"
    stored = storage.store(raw)
    converted = b"\xff\xd8\xff converted jpeg"
    (storage.derived_dir(stored.sha256) / "converted.jpg").write_bytes(converted)
    client = make_client(make_response(make_metadata()))

    await extract(
        make_document(mime_type="image/heic", sha256=stored.sha256),
        "",
        client=client,
        settings=settings,
    )

    content = client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(content[0]["source"]["data"]) == converted


async def test_oversized_file_is_skipped_gracefully(settings: Settings, data_dir: Path) -> None:
    stored = storage.store(b"x" * (5 * 1024 * 1024 + 1))
    client = make_client()

    with pytest.raises(ExtractionSkipped) as excinfo:
        await extract(make_document(sha256=stored.sha256), "", client=client, settings=settings)

    assert excinfo.value.reason == "file_too_large"
    assert client.messages.parse.await_count == 0


async def test_unusable_mime_with_no_text_is_skipped(settings: Settings) -> None:
    client = make_client()

    with pytest.raises(ExtractionSkipped) as excinfo:
        await extract(make_document(mime_type="text/plain"), "  ", client=client, settings=settings)

    assert excinfo.value.reason == "input_unusable"
    assert client.messages.parse.await_count == 0


def test_system_prompt_requests_english_output() -> None:
    """All free-text metadata must be emitted in English (translated if needed),
    regardless of the document's source language."""
    assert "in English" in SYSTEM_PROMPT
    # The source language is still detected and reported separately.
    assert "language: nld, eng, mixed, or unknown" in SYSTEM_PROMPT


def test_system_prompt_mentions_topics_and_general_kinds() -> None:
    """The prompt now frames a mixed archive of general reference material and
    instructs the topics field; the old household-paperwork framing is gone."""
    assert "topics" in SYSTEM_PROMPT
    assert "research" in SYSTEM_PROMPT
    assert "household paperwork" not in SYSTEM_PROMPT
