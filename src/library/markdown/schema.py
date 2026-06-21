"""Structured-output schema for vision markdown generation.

``DocumentMarkdown`` is the ``output_format`` passed to
``client.messages.parse()``. One ``PageMarkdown`` per input page image.
"""

from pydantic import BaseModel, ConfigDict


class PageMarkdown(BaseModel):
    """Markdown for one rendered page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int
    markdown: str


class DocumentMarkdown(BaseModel):
    """All pages of one document, in order."""

    model_config = ConfigDict(extra="forbid")

    pages: list[PageMarkdown]
