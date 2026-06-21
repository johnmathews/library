"""Batch-only LLM grader for extracted metadata.

Sends the document's OCR text plus its current extracted fields to a stronger
model and asks, per field, whether the extracted value is supported by the
source. Used by the eval harness; never called from the live pipeline.
"""

import json
import logging
from typing import Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from library.config import Settings
from library.extraction.extractor import MAX_TEXT_CHARS
from library.models import Document

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: int = 2_048

SYSTEM_PROMPT: str = """\
You grade metadata extracted from a household document for "Library".
You are given the document's text and a JSON object of extracted fields.
For each field present in the extraction, judge whether its value is supported
by the document text:
- "correct": the value is clearly supported by the text.
- "wrong": the text supports a different value.
- "unsupported": the text does not contain enough to confirm the value.
Return one verdict per provided field. Be strict: only "correct" when the text
clearly backs the value. Keep notes to one short line.
"""


class FieldVerdict(BaseModel):
    """The judge's verdict on one extracted field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    verdict: Literal["correct", "wrong", "unsupported"]
    note: str | None


class JudgeResult(BaseModel):
    """All per-field verdicts for one document."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[FieldVerdict]


def _extracted_fields(document: Document) -> dict[str, Any]:
    """The current extracted values, keyed by storage field name, non-null only."""
    fields: dict[str, Any] = {
        "title": document.title,
        "summary": document.summary,
        "document_date": document.document_date,
        "due_date": document.due_date,
        "expiry_date": document.expiry_date,
        "amount_total": document.amount_total,
        "currency": document.currency,
        "language": getattr(document.language, "value", document.language),
        "sender_id": document.sender_id,
        "kind_id": document.kind_id,
        "tags": sorted(tag.slug for tag in document.tags),
    }
    return {
        k: (str(v) if v is not None else None) for k, v in fields.items() if v not in (None, [])
    }


async def judge(document: Document, *, client: AsyncAnthropic, settings: Settings) -> JudgeResult:
    """Grade one document's extraction against its source text."""
    text = (document.ocr_text or "")[:MAX_TEXT_CHARS]
    extracted = _extracted_fields(document)
    content = [
        {"type": "text", "text": f"DOCUMENT TEXT:\n{text}"},
        {
            "type": "text",
            "text": f"EXTRACTED FIELDS (JSON):\n{json.dumps(extracted, ensure_ascii=False)}",
        },
    ]
    response = await client.messages.parse(
        model=settings.extraction_judge_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=JudgeResult,
    )
    parsed = response.parsed_output
    if parsed is None:
        logger.warning("judge returned no parseable output for document %s", document.id)
        return JudgeResult(verdicts=[])
    return parsed
