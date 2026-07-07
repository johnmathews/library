"""Shared HTML -> Markdown conversion.

Markdown is a first-class type downstream (extraction passthrough,
``chunk_markdown``, and the viewer renders it), so converting HTML to Markdown
preserves tables/headings/formatting where raw ``text/html`` — which the
pipeline cannot process — would not. ``script``/``style`` subtrees are removed
first so their contents don't leak into the text.

Two ingest paths share this: the email HTML body (``library.email_ingest``) and
the ``.docx`` attachment/upload path (``library.docx``).
"""

from bs4 import BeautifulSoup
from markdownify import markdownify


def html_to_markdown(html: str) -> str:
    """Convert an HTML fragment to Markdown, dropping ``script``/``style`` noise."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return markdownify(str(soup), heading_style="ATX").strip()
