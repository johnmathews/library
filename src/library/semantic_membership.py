"""Semantic membership for Smart Groups (semantic authored series).

Membership is learned from bge-m3 chunk embeddings. A document is represented by
the L2-normalized mean of its chunk embeddings; it *belongs* to a group when its
nearest member (positive) is within a cosine threshold and closer than any pruned
document (negative). Every decision here is mechanical — the LLM never decides
membership (see docs/smart-groups.md and the odd-one-out precedent in series.py).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import DocumentChunk


async def document_vectors(
    session: AsyncSession, document_ids: Sequence[int]
) -> dict[int, list[float]]:
    """L2-normalized mean chunk-embedding per document (docs without chunks omitted)."""
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentChunk.document_id, DocumentChunk.embedding).where(
                DocumentChunk.document_id.in_(list(document_ids))
            )
        )
    ).all()
    sums: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    for document_id, embedding in rows:
        vec = list(embedding)
        acc = sums.get(document_id)
        if acc is None:
            sums[document_id] = vec
            counts[document_id] = 1
        else:
            for i, value in enumerate(vec):
                acc[i] += value
            counts[document_id] += 1
    result: dict[int, list[float]] = {}
    for document_id, acc in sums.items():
        count = counts[document_id]
        mean = [value / count for value in acc]
        norm = math.sqrt(sum(value * value for value in mean))
        result[document_id] = [value / norm for value in mean] if norm > 0 else mean
    return result
