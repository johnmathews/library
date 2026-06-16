"""Split document text into embedding-sized chunks.

OCR text carries no reliable page boundaries (PDF pages are joined with blank
lines, indistinguishable from paragraph breaks), so we pack whitespace-split
words greedily into windows of at most ``max_chars`` with a trailing
``overlap`` carried into the next window. Internal whitespace is collapsed to
single spaces — fine for both embedding and snippet display.
"""

from __future__ import annotations


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
