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

from collections.abc import Iterable
from dataclasses import dataclass


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
