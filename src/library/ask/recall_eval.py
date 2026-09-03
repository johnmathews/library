"""Scoring for the recall eval: did retrieval actually reach the right documents?

The counterpart to ``disclosure_eval``. That module asks whether an answer owned
up to a gap it was shown; this one asks whether the documents that could answer
the question were retrieved at all.

Pure by design — stdlib only, no DB and no network — so CI runs it while the
live halves (``library eval-recall``, which needs the bge-m3 sidecar, and its
``--ask`` mode, which additionally needs Claude credentials) cannot run there.

**Recall, not precision, and deliberately so.** The question this eval exists to
answer is "can Ask reach the document at all", which is what findings #5, #6 and
#7 move. Precision matters too, but a retrieval change that adds a true positive
at rank 9 and a false positive at rank 10 is an improvement this eval should
report as one. Ranking quality is re-ranking's problem, deferred in
``docs/roadmap.md`` §1.2 — and this eval is the thing that would fire that
trigger.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Starting number of composite-Simpson intervals for :func:`blind_recall`.
#: **Even, and it must stay even** — the composite rule alternates 4/2 weights
#: and an odd count silently invalidates it. Asserted below rather than trusted.
#:
#: This is a starting point, not a tuned constant, because a tuned one was
#: wrong. The integrand is a polynomial in ``1 - u`` whose degree grows with the
#: pool's TOTAL CHUNK COUNT, so a step count checked against today's corpus
#: (177 chunks, exact to 1e-14) degrades as the corpus grows — measured at 1.9e-3
#: relative error by 1,600 chunks and 4.8e-2 by 3,200, in both directions, with
#: nothing to notice. Since this corpus is deliberately growing in chunks, the
#: step count refines until it converges instead.
_BLIND_QUADRATURE_STEPS: int = 512

#: Relative agreement required between successive refinements before the value
#: is trusted. Well inside anything the 0.35 ceiling could care about.
_BLIND_QUADRATURE_TOLERANCE: float = 1e-9

#: How many times the step count may double before giving up. 512 -> 32,768
#: covers a corpus far larger than this one; beyond that the caller is told
#: rather than handed a quietly wrong floor.
_BLIND_QUADRATURE_REFINEMENTS: int = 6


@dataclass(frozen=True, slots=True)
class RecallVerdict:
    """One case's result, carrying both sides so a human can read the failure.

    ``retrieved`` is kept (truncated to ``k``) because a case that fails is
    almost always diagnosed by looking at what came back *instead* — the
    near-miss distractors the corpus seeds on purpose.
    """

    case: str
    passed: bool
    expected: tuple[int, ...]
    retrieved: tuple[int, ...]
    found: tuple[int, ...]
    missed: tuple[int, ...]
    recall: float
    k: int


def score_recall(
    case: str, expected: Iterable[int], retrieved: Iterable[int], *, k: int
) -> RecallVerdict:
    """Score one case's retrieval as recall@k.

    ``retrieved`` is truncated to the first ``k`` ids before anything is
    measured: k IS the measurement, so a caller who over-fetches (and callers
    do — ``semantic_search``'s own ``top_k`` may exceed the k being scored)
    must not accidentally be credited for documents below the cut.

    ``expected`` is de-duplicated while preserving order, because a case that
    names the same document twice could otherwise never reach recall 1.0.

    An **empty** ``expected`` fails. It is not a vacuous pass: a case with
    nothing to find measures nothing, and reporting success for it is the
    failure mode ``disclosure_eval.score`` had to grow its own guard against.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    expected_ids = tuple(dict.fromkeys(expected))
    retrieved_ids = tuple(retrieved)[:k]

    if not expected_ids:
        return RecallVerdict(
            case=case,
            passed=False,
            expected=(),
            retrieved=retrieved_ids,
            found=(),
            missed=(),
            recall=0.0,
            k=k,
        )

    top = set(retrieved_ids)
    found = tuple(document_id for document_id in expected_ids if document_id in top)
    missed = tuple(document_id for document_id in expected_ids if document_id not in top)
    return RecallVerdict(
        case=case,
        passed=not missed,
        expected=expected_ids,
        retrieved=retrieved_ids,
        found=found,
        missed=missed,
        recall=len(found) / len(expected_ids),
        k=k,
    )


def blind_recall(expected_chunks: Iterable[int], crowder_chunks: Iterable[int], *, k: int) -> float:
    """What a retriever ranking at RANDOM would score on a case, given chunk counts.

    The floor of a case's range. The closer it sits to 1.0 the less the case can
    say, which is why ``tests/test_recall_scenarios.py`` holds every case under
    ``MAX_BLIND_RECALL``.

    **Why this is not ``k / pool_size``.** That formula — which this function
    replaces — models documents as exchangeable, drawn uniformly. They are not.
    ``semantic_search`` ranks a document by its **nearest chunk**
    (``search.py``'s ``DISTINCT ON (document_id) ORDER BY distance``), so under a
    null retriever a document with ``c`` chunks gets ``c`` independent draws at
    being near the query and a single-chunk document gets one. More chunks is
    more lottery tickets, and the uniform model cannot see it.

    While every fixture is one chunk the two agree exactly, which is why the old
    formula was correct for years. They diverge the moment lengths vary, and they
    diverge **in the passing direction**: three 5-chunk expected documents among
    37 single-chunk crowders at k=10 have a true floor of 0.70, where the uniform
    model reports 0.25 against a 0.35 ceiling. A case built the way issue #106
    describes — "the answer lives in a specific passage of a *long* document" —
    lands squarely in that hole and would have been scored as hard while
    measuring close to noise.

    **The model.** Give each chunk an independent Exp(1) clock and rank documents
    by their earliest chunk; the minimum of ``c`` iid Exp(1) clocks is Exp(``c``),
    so this is a Plackett-Luce race weighted by chunk count. Then::

        P(doc i in top k) = ∫₀^∞ c_i e^{-c_i t} · P(#{j≠i : T_j < t} < k) dt

    Substituting ``u = 1 - e^{-t}`` maps that to a smooth integrand on ``[0, 1]``,
    and the inner term is a Poisson-binomial CDF computed by an O(n·k) DP. The
    result is deterministic — no seed, no flake, unlike the Monte-Carlo it was
    cross-checked against.

    The corollary for whoever authors fixtures: **crowders long, expected
    documents no longer than their crowders.** Lengthening the *expected*
    documents raises the floor and makes a case easier while looking harder.
    """
    expected = list(expected_chunks)
    crowders = list(crowder_chunks)
    if not expected:
        raise ValueError("blind_recall needs at least one expected document")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if any(c < 1 for c in expected + crowders):
        raise ValueError("every document must have at least one chunk")

    weights = expected + crowders
    if k >= len(weights):
        # Every document is returned, so a random retriever scores perfectly.
        return 1.0

    # Group by chunk count: the integral depends only on c_i and the multiset of
    # the OTHER weights, so N expected documents sharing a count cost one
    # evaluation, not N. Without this the breadth case alone runs 12x longer.
    total = 0.0
    for count in set(expected):
        others = list(weights)
        others.remove(count)
        total += expected.count(count) * _probability_in_top_k(count, others, k)
    return total / len(expected)


def _probability_in_top_k(own_chunks: int, other_chunks: list[int], k: int) -> float:
    """Integrate the substituted integral from ``blind_recall`` to convergence.

    Refines rather than trusting a fixed step count. The integrand's degree grows
    with the pool's total chunk count, so any constant chosen against one corpus
    goes quietly wrong on a larger one — and this corpus is built to grow in
    chunks. Doubling until two successive estimates agree makes the step count a
    property of the input instead of a constant someone has to remember to revisit.
    """

    def integrand(u: float) -> float:
        remaining = 1.0 - u
        if remaining <= 0.0:
            # The correct limit, not a fudge: k < len(weights), so at least two
            # clocks must still be unfired and P(fewer than k fired) -> 0.
            return 0.0
        elapsed = -math.log(remaining)
        return (
            own_chunks
            * remaining ** (own_chunks - 1)
            * _fewer_than_k_fired(other_chunks, elapsed, k)
        )

    steps = _BLIND_QUADRATURE_STEPS
    estimate = _simpson(integrand, steps)
    for _ in range(_BLIND_QUADRATURE_REFINEMENTS):
        steps *= 2
        refined = _simpson(integrand, steps)
        if abs(refined - estimate) <= _BLIND_QUADRATURE_TOLERANCE * max(abs(refined), 1e-12):
            return refined
        estimate = refined
    raise ValueError(
        f"blind_recall did not converge at {steps} quadrature steps for a pool of "
        f"{sum(other_chunks) + own_chunks} chunks. The corpus has outgrown this "
        "integrator — raise _BLIND_QUADRATURE_REFINEMENTS, or switch to a "
        "Gauss-Legendre rule sized to the chunk count (the integrand is a "
        "polynomial, so that is exact). Do not widen the tolerance: the floor "
        "would then be wrong in an unknown direction."
    )


def _simpson(integrand: Callable[[float], float], steps: int) -> float:
    """Composite Simpson on [0, 1] over an even number of intervals."""
    if steps % 2:  # pragma: no cover - callers only ever double an even constant
        raise ValueError(f"composite Simpson needs an even interval count, got {steps}")
    step = 1.0 / steps
    acc = integrand(0.0) + integrand(1.0)
    for index in range(1, steps):
        acc += integrand(index * step) * (4.0 if index % 2 else 2.0)
    return acc * step / 3.0


def _fewer_than_k_fired(chunk_counts: list[int], elapsed: float, k: int) -> float:
    """P(fewer than ``k`` of the Exp(c) clocks have fired by ``elapsed``).

    Poisson-binomial DP over per-document firing probabilities. ``expm1`` rather
    than ``1 - exp`` because the probabilities are tiny for small ``elapsed``,
    where the naive form loses every significant digit to cancellation.
    """
    distribution = [1.0] + [0.0] * k
    for count in chunk_counts:
        fired = -math.expm1(-count * elapsed)
        for fired_so_far in range(k, 0, -1):
            distribution[fired_so_far] = distribution[fired_so_far] * (1.0 - fired) + (
                distribution[fired_so_far - 1] * fired
            )
        distribution[0] *= 1.0 - fired
    return math.fsum(distribution[:k])
