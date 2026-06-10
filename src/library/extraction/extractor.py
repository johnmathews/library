"""Call Claude to extract document metadata via structured outputs.

``extract()`` is pure orchestration around the Anthropic SDK: build input
content (OCR text, or the original file when OCR produced nothing usable),
call ``client.messages.parse()`` on the primary model, and escalate once to
the bigger model on low confidence or a parse/validation failure. API
errors propagate (the SDK already retried 429/5xx); the caller decides what
a failure means for the document.
"""

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic

from library.config import Settings
from library.extraction.schema import KIND_SLUGS, ExtractedMetadata
from library.models import Document
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)

# Bump whenever the system prompt or schema changes meaningfully; stored per
# run so old-prompt documents can be found and re-extracted later.
PROMPT_VERSION: str = "2026-06-10.1"

# USD per million tokens (input, output), June 2026 list prices.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}

# OCR text is truncated to this many characters before being sent: metadata
# lives on the first pages, and this caps per-document spend.
MAX_TEXT_CHARS: int = 8_000
# Below this many stripped characters the OCR text is considered garbage and
# the original file is sent instead (when it is a PDF or image).
MIN_TEXT_CHARS: int = 20
# Largest original we are willing to base64 into a request.
MAX_FILE_BYTES: int = 5 * 1024 * 1024

MAX_OUTPUT_TOKENS: int = 2_048

SYSTEM_PROMPT: str = f"""\
You extract metadata for "Library", a self-hosted family document archive.
Documents are household paperwork in Dutch, English, or a mix: invoices,
receipts, certificates, utility bills, parking tickets, warranties, manuals,
letters, contracts, tickets. Input is OCR text (possibly noisy) or the
original document/image.

Rules:
- kind_slug: one of {", ".join(KIND_SLUGS)}. Use "other" only when nothing
  else fits.
- title: short and descriptive, in the document's own language.
- summary: at most two sentences, in the document's own language.
- sender_name: the organisation or person that issued the document, in a
  short canonical form (e.g. "Eneco", not "Eneco Services B.V., afdeling
  facturatie"). null when unclear.
- Dates are ISO YYYY-MM-DD strings; null when absent. document_date is the
  issue date of the document.
- amount_total: the document's main total as a plain decimal string, e.g.
  "1234.56", with currency as an ISO 4217 code, e.g. "EUR". Both null when
  not applicable.
- tags: at most 8 lowercase slug tags ("hyphenated-like-this"), useful for
  finding the document later. No duplicates of kind or sender.
- language: nld, eng, mixed, or unknown — the language of the document.
- confidence: "low" when the text is garbled or you had to guess key fields,
  "high" only when the document is clear.
- reasoning_note: one short line when something needed judgement; else null.
"""

# Claude-accepted image media types we can send as-is.
_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})


class ExtractionSkipped(Exception):
    """Extraction cannot run for this document; skip it gracefully."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


class ExtractionParseError(Exception):
    """The model returned no parseable structured output."""


@dataclass(frozen=True)
class CallUsage:
    """Token usage and estimated cost of one API call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ExtractionOutcome:
    """A successful extraction plus its provenance and cost."""

    metadata: ExtractedMetadata
    model: str
    prompt_version: str
    input_mode: str  # "text" | "document" | "image"
    escalated: bool
    calls: list[CallUsage] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    @property
    def cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.calls)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost of one call from the static pricing table."""
    if model not in MODEL_PRICING_USD_PER_MTOK:
        logger.warning("no pricing for model %s; recording cost 0", model)
    input_price, output_price = MODEL_PRICING_USD_PER_MTOK.get(model, (0.0, 0.0))
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


def build_user_content(document: Document, ocr_text: str) -> tuple[list[dict[str, Any]], str]:
    """Build the user message content and report the input mode used.

    Prefers truncated OCR text; falls back to sending the original file as a
    base64 ``document``/``image`` block when the text is empty or garbage.
    Raises :class:`ExtractionSkipped` when neither input is usable.
    """
    text = ocr_text.strip()
    if len(text) >= MIN_TEXT_CHARS:
        return [{"type": "text", "text": text[:MAX_TEXT_CHARS]}], "text"

    mime = document.mime_type
    if mime == "application/pdf":
        path = path_for(document.sha256)
        media_type = "application/pdf"
        block_type = "document"
    elif mime in _IMAGE_MEDIA_TYPES:
        path = path_for(document.sha256)
        media_type = mime
        block_type = "image"
    elif mime in ("image/heic", "image/heif"):
        # Claude does not accept HEIC; the ingest step wrote a JPEG conversion.
        path = derived_dir(document.sha256) / "converted.jpg"
        media_type = "image/jpeg"
        block_type = "image"
    else:
        raise ExtractionSkipped(
            "input_unusable", f"no usable OCR text and mime {mime} cannot be sent directly"
        )

    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ExtractionSkipped("input_unusable", f"original artifact missing: {path}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ExtractionSkipped(
            "file_too_large", f"{len(raw)} bytes exceeds the {MAX_FILE_BYTES} byte direct-input cap"
        )

    block: dict[str, Any] = {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(raw).decode("ascii"),
        },
    }
    return [
        block,
        {"type": "text", "text": "Extract the metadata for this document."},
    ], block_type


async def _attempt(
    client: AsyncAnthropic, model: str, content: list[dict[str, Any]]
) -> tuple[ExtractedMetadata, CallUsage]:
    """One ``messages.parse`` call; raises on parse/validation failure."""
    response = await client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractedMetadata,
    )
    usage = CallUsage(
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=estimate_cost_usd(
            model, response.usage.input_tokens, response.usage.output_tokens
        ),
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ExtractionParseError(f"{model} returned no parseable output")
    return parsed, usage


async def extract(
    document: Document,
    ocr_text: str,
    *,
    client: AsyncAnthropic,
    settings: Settings,
) -> ExtractionOutcome:
    """Extract metadata for one document, escalating once when warranted.

    Escalates to ``settings.extraction_escalation_model`` when the primary
    model reports low confidence or fails to produce valid structured output
    (``ValidationError``/JSON errors are ``ValueError`` subclasses). API
    errors propagate to the caller.
    """
    content, input_mode = build_user_content(document, ocr_text)
    calls: list[CallUsage] = []

    metadata: ExtractedMetadata | None = None
    try:
        metadata, usage = await _attempt(client, settings.extraction_model, content)
        calls.append(usage)
    except (ExtractionParseError, ValueError) as exc:
        logger.warning(
            "document %s: %s failed structured parse (%s); escalating",
            document.id,
            settings.extraction_model,
            exc,
        )

    if metadata is not None and metadata.confidence != "low":
        return ExtractionOutcome(
            metadata=metadata,
            model=settings.extraction_model,
            prompt_version=PROMPT_VERSION,
            input_mode=input_mode,
            escalated=False,
            calls=calls,
        )

    metadata, usage = await _attempt(client, settings.extraction_escalation_model, content)
    calls.append(usage)
    return ExtractionOutcome(
        metadata=metadata,
        model=settings.extraction_escalation_model,
        prompt_version=PROMPT_VERSION,
        input_mode=input_mode,
        escalated=True,
        calls=calls,
    )
