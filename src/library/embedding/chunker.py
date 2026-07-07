"""Split document text into embedding-sized chunks.

OCR text carries no reliable page boundaries (PDF pages are joined with blank
lines, indistinguishable from paragraph breaks), so we pack whitespace-split
words greedily into windows of at most ``max_chars`` with a trailing
``overlap`` carried into the next window. Internal whitespace is collapsed to
single spaces — fine for both embedding and snippet display.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from library.docx import DOCX_MIME

_BLANK_LINE_RE = re.compile(r"\n\s*\n+")

# MIME types whose text layer is Markdown (structure-aware chunking applies).
# Word ``.docx`` is stored under its own MIME but its OCR passthrough yields the
# Markdown conversion, so it chunks like ``text/markdown``.
_MARKDOWN_MIME_TYPES: frozenset[str] = frozenset({"text/markdown", DOCX_MIME})

_Chunker = Callable[..., list[str]]


def _overlap_tail(words: list[str], overlap: int) -> list[str]:
    """Smallest suffix of ``words`` whose joined length stays within ``overlap``."""
    if overlap <= 0:
        return []
    tail: list[str] = []
    length = 0
    for word in reversed(words):
        added = len(word) + (1 if tail else 0)
        if length + added > overlap:
            break
        tail.insert(0, word)
        length += added
    return tail


def chunk_text(text: str, *, max_chars: int, overlap: int) -> list[str]:
    """Pack ``text`` into ``<= max_chars`` chunks with ``overlap`` carry-over.

    Returns ``[]`` for blank input and a single chunk when the text already
    fits. A lone word longer than ``max_chars`` becomes its own (oversized)
    chunk — the embedder truncates server-side.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append(" ".join(current))
            current = _overlap_tail(current, overlap)
            current_len = len(" ".join(current))
            current.append(word)
            current_len += len(word) + (1 if current_len else 0)
        else:
            current.append(word)
            current_len += added
    if current:
        chunks.append(" ".join(current))
    return chunks


def _overlap_blocks(blocks: list[str], overlap: int) -> list[str]:
    """Smallest suffix of whole ``blocks`` whose joined length stays within ``overlap``."""
    if overlap <= 0:
        return []
    tail: list[str] = []
    length = 0
    for block in reversed(blocks):
        added = len(block) + (2 if tail else 0)
        if tail and length + added > overlap:
            break
        tail.insert(0, block)
        length += added
    return tail


def chunk_markdown(text: str, *, max_chars: int, overlap: int) -> list[str]:
    """Pack Markdown into ``<= max_chars`` chunks on blank-line block boundaries.

    Unlike ``chunk_text``, this preserves structure: headings, list items, and
    intra-block newlines survive because blocks (paragraphs separated by blank
    lines) are packed whole, joined with ``\\n\\n``, and overlap carries whole
    blocks. A single block larger than ``max_chars`` falls back to the
    word-packer (``chunk_text``). Returns ``[]`` for blank input.
    """
    blocks = [block for block in (b.strip() for b in _BLANK_LINE_RE.split(text)) if block]
    if not blocks:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in blocks:
        if len(block) > max_chars:
            # Oversized block: flush what we have, then word-pack the block.
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            chunks.extend(chunk_text(block, max_chars=max_chars, overlap=overlap))
            continue
        added = len(block) + (2 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append("\n\n".join(current))
            current = _overlap_blocks(current, overlap)
            current_len = len("\n\n".join(current))
            added = len(block) + (2 if current else 0)
            current.append(block)
            current_len += added
        else:
            current.append(block)
            current_len += added
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunker_for_mime(mime_type: str) -> _Chunker:
    """Pick the structure-aware chunker for Markdown-bearing MIME types.

    ``text/markdown`` and Word ``.docx`` (whose passthrough text is Markdown)
    use ``chunk_markdown``; everything else uses the plain word-packer.
    """
    return chunk_markdown if mime_type in _MARKDOWN_MIME_TYPES else chunk_text
