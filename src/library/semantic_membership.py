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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.models import (
    AuthoredSeries,
    AuthoredSeriesExclusion,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    DocumentChunk,
    MemberOrigin,
    SeriesMode,
    SuggestionState,
)


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


# --- DB-backed membership engine (Smart Groups: scoring + backfill sweep) ---


async def _member_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = await session.execute(
        select(AuthoredSeriesMember.document_id).where(
            AuthoredSeriesMember.authored_series_id == group_id
        )
    )
    return [r[0] for r in rows]


async def _exclusion_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = await session.execute(
        select(AuthoredSeriesExclusion.document_id).where(
            AuthoredSeriesExclusion.authored_series_id == group_id
        )
    )
    return [r[0] for r in rows]


async def evaluate_group(
    session: AsyncSession,
    settings: Settings,
    group_id: int,
    candidate_ids: Sequence[int],
    *,
    extra_positive_ids: Sequence[int] = (),
) -> list[tuple[int, MembershipScore]]:
    """Score candidates against a group's members (+ optional anchors) and exclusions.

    Returns only the candidates that ``belong``, sorted by ``sim_pos`` descending.
    """
    if not candidate_ids:
        return []
    positive_ids = list(dict.fromkeys([*await _member_ids(session, group_id), *extra_positive_ids]))
    negative_ids = await _exclusion_ids(session, group_id)
    needed = list(dict.fromkeys([*positive_ids, *negative_ids, *candidate_ids]))
    vectors = await document_vectors(session, needed)
    positives = [vectors[i] for i in positive_ids if i in vectors]
    negatives = [vectors[i] for i in negative_ids if i in vectors]
    if not positives:
        return []
    hits: list[tuple[int, MembershipScore]] = []
    for candidate_id in candidate_ids:
        vec = vectors.get(candidate_id)
        if vec is None:
            continue
        score = score_vector(
            vec,
            positives,
            negatives,
            tau=settings.semantic_group_min_similarity,
            margin=settings.semantic_group_neg_margin,
        )
        if score.belongs:
            hits.append((candidate_id, score))
    hits.sort(key=lambda pair: pair[1].sim_pos, reverse=True)
    return hits


async def _eligible_candidate_ids(session: AsyncSession, group_id: int) -> list[int]:
    """Non-deleted, amount-bearing docs not already a member or exclusion of the group."""
    member_sub = select(AuthoredSeriesMember.document_id).where(
        AuthoredSeriesMember.authored_series_id == group_id
    )
    excl_sub = select(AuthoredSeriesExclusion.document_id).where(
        AuthoredSeriesExclusion.authored_series_id == group_id
    )
    rows = await session.execute(
        select(Document.id).where(
            Document.deleted_at.is_(None),
            Document.amount_total.isnot(None),
            Document.id.notin_(member_sub),
            Document.id.notin_(excl_sub),
        )
    )
    return [r[0] for r in rows]


async def sweep_backfill(
    session: AsyncSession,
    settings: Settings,
    group_id: int,
    anchor_ids: Sequence[int] = (),
) -> list[tuple[int, MembershipScore]]:
    """Score the whole library and write ``pending`` suggestions for matches.

    ``anchor_ids`` seed the positive set alongside the group's real members —
    useful for a brand-new group that has few or no members yet. Capped at
    ``min(settings.series_suggestion_limit, 100)`` (also keeps the write, and
    any API payload built from the result, within the list-size limits
    elsewhere in the app — ``backfill`` on the create-series response 422s
    above 100 entries, so this holds even if ``series_suggestion_limit`` is
    misconfigured higher). Returns the (possibly capped) hits that were
    written.

    The suggestion write is an upsert that no-ops on a ``(series, document)``
    conflict, so re-sweeping a group that still has the same doc pending is
    idempotent rather than raising ``IntegrityError`` on the unique
    constraint.
    """
    candidate_ids = await _eligible_candidate_ids(session, group_id)
    hits = await evaluate_group(
        session, settings, group_id, candidate_ids, extra_positive_ids=anchor_ids
    )
    hits = hits[: min(settings.series_suggestion_limit, 100)]
    for document_id, score in hits:
        statement = (
            pg_insert(AuthoredSeriesSuggestion)
            .values(
                authored_series_id=group_id,
                document_id=document_id,
                state=SuggestionState.PENDING.value,
                score=score.sim_pos,
            )
            .on_conflict_do_nothing(constraint="authored_series_suggestions_series_document")
        )
        await session.execute(statement)
    await session.commit()
    return hits


async def auto_add_document(
    session: AsyncSession, settings: Settings, document_id: int
) -> list[int]:
    """Silently add a newly-indexed document to every semantic group it belongs to."""
    if not settings.semantic_group_enabled:
        return []
    group_ids = [
        r[0]
        for r in await session.execute(
            select(AuthoredSeries.id).where(AuthoredSeries.mode == SeriesMode.SEMANTIC)
        )
    ]
    joined: list[int] = []
    for group_id in group_ids:
        # Not in the group already? evaluate_group's candidate list handles member/excl skip.
        existing = set(await _member_ids(session, group_id)) | set(
            await _exclusion_ids(session, group_id)
        )
        if document_id in existing:
            continue
        hits = await evaluate_group(session, settings, group_id, [document_id])
        if hits:
            # Upsert (no-op on conflict) for symmetry with sweep_backfill /
            # exclusion writes: guards against a concurrent-add race hitting
            # the (series, document) unique constraint with an IntegrityError.
            # The member/exclusion pre-check above already gates this, so the
            # on_conflict is belt-and-suspenders.
            await session.execute(
                pg_insert(AuthoredSeriesMember)
                .values(
                    authored_series_id=group_id,
                    document_id=document_id,
                    origin=MemberOrigin.AUTO.value,
                )
                .on_conflict_do_nothing(constraint="authored_series_members_series_document")
            )
            joined.append(group_id)
    if joined:
        await session.commit()
    return joined
