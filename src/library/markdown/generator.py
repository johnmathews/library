"""Call Claude vision to render document pages as markdown, grounded on OCR text.

One ``messages.parse`` call per page-image batch; page numbers are assigned
positionally and absolutely so a mis-numbered or short response can never
invent a page without an image. API errors propagate (the SDK retried 5xx);
the caller decides what a failure means for the document.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from library.config import Settings
from library.extraction.extractor import estimate_cost_usd
from library.markdown.schema import DocumentMarkdown
from library.models import Document

logger = logging.getLogger(__name__)

# Bump when the prompt or schema changes meaningfully; stored per run.
PROMPT_VERSION: str = "2026-06-21.1"

# Markdown output can be large (a full multi-page document); allow room.
MAX_OUTPUT_TOKENS: int = 8_192
# OCR grounding text is truncated to cap spend; layout comes from the images.
MAX_GROUNDING_CHARS: int = 12_000

SYSTEM_PROMPT: str = """\
You convert scanned/photographed document pages into clean GitHub-flavored
markdown for "Library", a self-hosted family document archive (Dutch, English,
or mixed household paperwork).

For EACH page image you are given, produce faithful markdown:
- Reproduce real tables as markdown tables. Reconstruct borderless/columnar
  tables from the visual layout into proper markdown tables.
- Use headings, lists, and emphasis to match the document's structure.
- Transcribe text in the document's own language; do not translate or
  summarize. Do not invent content that is not on the page.
- The accompanying OCR text is a spelling/figure reference for exact numbers,
  names, and codes -- prefer it when the image is ambiguous, but trust the
  image for layout and structure.

Return one entry per input page, in order, with page_number starting at 1 for
the first image you were given in this request.
"""


class MarkdownSkipped(Exception):
    """Markdown generation cannot run/produce output; skip gracefully."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class GeneratedPage:
    page_number: int  # absolute, 1-based
    markdown: str


@dataclass(frozen=True)
class MarkdownResult:
    pages: list[GeneratedPage]
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _image_block(jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii"),
        },
    }


async def generate_markdown(
    document: Document,
    ocr_text: str,
    page_images: list[bytes],
    *,
    client: AsyncAnthropic,
    settings: Settings,
) -> MarkdownResult:
    """Render ``page_images`` to per-page markdown; raise MarkdownSkipped if none."""
    batch_size = max(settings.markdown_page_batch, 1)
    grounding = ocr_text.strip()[:MAX_GROUNDING_CHARS]
    pages: list[GeneratedPage] = []
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    offset = 0

    for start in range(0, len(page_images), batch_size):
        batch = page_images[start : start + batch_size]
        content: list[dict] = [_image_block(image) for image in batch]
        content.append(
            {
                "type": "text",
                "text": (
                    "Convert these page images to markdown. "
                    "OCR text reference for this document:\n\n" + grounding
                ),
            }
        )
        response = await client.messages.parse(
            model=settings.markdown_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=DocumentMarkdown,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cost += estimate_cost_usd(
            settings.markdown_model, response.usage.input_tokens, response.usage.output_tokens
        )
        parsed = response.parsed_output
        returned = sorted(parsed.pages, key=lambda p: p.page_number) if parsed else []
        for index, page in enumerate(returned[: len(batch)]):
            pages.append(GeneratedPage(page_number=offset + index + 1, markdown=page.markdown))
        offset += len(batch)

    if not pages:
        raise MarkdownSkipped("input_unusable", "model returned no pages")

    return MarkdownResult(
        pages=pages,
        model=settings.markdown_model,
        prompt_version=PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
