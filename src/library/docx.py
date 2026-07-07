"""Convert Word ``.docx`` uploads to Markdown at the ingest boundary.

A ``.docx`` is detected by content sniffing as ``DOCX_MIME`` (a zip whose
``filetype.guess`` returns the Office wordprocessing type). Rather than build a
bespoke OCR route for it, ingest converts it to Markdown — an already
first-class type — and keeps the ``.docx`` as the stored original (source of
truth), writing the Markdown as a derived artifact named
``CONVERTED_MARKDOWN_NAME`` alongside it (mirroring how HEIC keeps the original
and derives a JPEG). See ``docs/ingestion.md`` and ``library.ingest``.

The conversion is the "light path": ``mammoth`` (semantic HTML from the docx
structure) -> shared ``html_to_markdown`` (strip noise + ``markdownify`` ATX).
Headings, tables, lists, and free text survive; ``mammoth`` warnings (e.g.
unmapped styles) are non-fatal and ignored.
"""

import io

from library.markdown.html import html_to_markdown

DOCX_MIME: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Derived-artifact filename for the Markdown rendered from a ``.docx`` original,
# analogous to ``library.images.CONVERTED_JPEG_NAME`` for HEIC.
CONVERTED_MARKDOWN_NAME: str = "converted.md"


def docx_to_markdown(content: bytes) -> str:
    """Convert ``.docx`` bytes to Markdown via mammoth + ``html_to_markdown``.

    ``mammoth`` is imported lazily so that importing this module for its
    constants (``DOCX_MIME`` etc., needed across the pipeline's MIME-routing
    surfaces) stays cheap and dependency-light.
    """
    import mammoth

    html = mammoth.convert_to_html(io.BytesIO(content)).value
    return html_to_markdown(html)
