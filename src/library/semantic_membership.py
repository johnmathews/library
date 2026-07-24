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
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class MembershipScore:
    sim_pos: float  # cosine to nearest positive
    sim_neg: float  # cosine to nearest negative (0.0 if none)
    belongs: bool


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    # pgvector rows surface numpy.float32 scalars; cast to a native float so
    # downstream JSON serialization (API responses, DB storage) never chokes
    # on a numpy type.
    return float(dot / (na * nb))


def score_vector(
    candidate: Sequence[float],
    positives: Sequence[Sequence[float]],
    negatives: Sequence[Sequence[float]],
    *,
    tau: float,
    margin: float,
) -> MembershipScore:
    """Nearest-positive-neighbour membership with a negative veto.

    Belongs iff the candidate is within ``tau`` cosine of some positive AND that
    similarity beats its nearest negative by more than ``margin``. Works with a
    handful of positives and zero negatives (cold start): the ``tau`` gate alone
    admits, and ``max`` over positives lets diverse sub-clusters each count.
    """
    if not positives:
        return MembershipScore(0.0, 0.0, False)
    sim_pos = max(_cosine(candidate, p) for p in positives)
    sim_neg = max((_cosine(candidate, n) for n in negatives), default=0.0)
    belongs = sim_pos >= tau and sim_pos > sim_neg + margin
    return MembershipScore(sim_pos=sim_pos, sim_neg=sim_neg, belongs=belongs)
