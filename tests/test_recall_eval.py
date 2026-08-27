"""Unit tests for the recall scorer — no DB, no embedder, no credentials."""

import pytest

from library.ask.recall_eval import score_recall


def test_all_expected_retrieved_passes() -> None:
    verdict = score_recall("a", [1, 2], [1, 2, 3], k=10)
    assert verdict.passed is True
    assert verdict.recall == 1.0
    assert verdict.missed == ()


def test_partial_retrieval_reports_which_were_missed() -> None:
    verdict = score_recall("b", [1, 2], [1, 9], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.5
    assert verdict.found == (1,)
    assert verdict.missed == (2,)


def test_k_truncates_before_scoring() -> None:
    """Documents ranked below k do not count as retrieved, even though the
    caller handed them to us — k is the measurement, not a display limit."""
    verdict = score_recall("c", [1, 5], [9, 8, 7, 5, 1], k=3)
    assert verdict.retrieved == (9, 8, 7)
    assert verdict.recall == 0.0
    assert verdict.missed == (1, 5)


def test_empty_expected_set_fails_rather_than_vacuously_passing() -> None:
    """A case with nothing expected has nothing to measure. It must FAIL.

    This is the exact defect `disclosure_eval.score` had to grow a guard for:
    a scenario whose check loop has nothing to iterate reports success having
    exercised nothing, which is worse than no eval at all.
    """
    verdict = score_recall("d", [], [1, 2], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.0


def test_duplicate_expected_ids_are_counted_once() -> None:
    """Otherwise a case that lists a document twice can never reach recall 1.0."""
    verdict = score_recall("e", [1, 1, 2], [1, 2], k=10)
    assert verdict.expected == (1, 2)
    assert verdict.recall == 1.0
    assert verdict.passed is True


def test_nothing_retrieved_is_zero_not_a_crash() -> None:
    verdict = score_recall("f", [1], [], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.0
    assert verdict.missed == (1,)


@pytest.mark.parametrize("k", [0, -1])
def test_non_positive_k_is_rejected(k: int) -> None:
    """k <= 0 makes recall meaningless and would silently score every case 0.0.
    Fail loudly instead — a caller passing k=0 has a bug, not a measurement."""
    with pytest.raises(ValueError):
        score_recall("g", [1], [1], k=k)
