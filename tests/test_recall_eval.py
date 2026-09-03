"""Unit tests for the recall scorer — no DB, no embedder, no credentials."""

import pytest

from library.ask.recall_eval import blind_recall, score_recall


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


class TestBlindRecall:
    """The blind floor under chunk-count weighting.

    These are the tests that make it safe to replace `k / pool_size`: the first
    proves the new model agrees with the old one everywhere the old one was
    right, and the rest pin the divergence that motivated the replacement.
    """

    @pytest.mark.parametrize(
        ("n_expected", "pool_size", "k"),
        [(3, 44, 10), (3, 40, 10), (2, 40, 10), (3, 40, 10), (12, 57, 12)],
    )
    def test_all_single_chunk_reproduces_the_uniform_formula(
        self, n_expected: int, pool_size: int, k: int
    ) -> None:
        """THE REGRESSION ANCHOR: the new model must not move the old numbers.

        These are the five case shapes as they stood BEFORE the 2026-09-03
        multi-chunk rebuild, when every fixture was one chunk and `k / pool_size`
        was therefore exactly right. That is what makes them the anchor — the
        weighted model has to reproduce the uniform formula on the corpus the
        uniform formula was correct for, or it is wrong rather than stricter.

        They are deliberately NOT read from `CASES`: the live corpus has moved on
        (breadth's pool is now 81, not 57, and a seventh case exists), and an
        anchor that tracks the thing it anchors is not an anchor.
        """
        got = blind_recall([1] * n_expected, [1] * (pool_size - n_expected), k=k)
        assert got == pytest.approx(k / pool_size, abs=1e-9)

    def test_longer_expected_documents_raise_the_floor(self) -> None:
        """The defect this model exists to expose, in the direction that hurts.

        Issue #106 asks for a case whose answer "lives in a specific passage of a
        long document". Built naively that makes the EXPECTED documents the long
        ones — and a random retriever then scores 0.70 on it, well above the 0.35
        ceiling, while the uniform formula still reports 0.25 and passes.
        """
        uniform = 10 / 40
        assert uniform == pytest.approx(0.25)
        assert blind_recall([5] * 3, [1] * 37, k=10) == pytest.approx(0.70, abs=0.01)
        assert blind_recall([8] * 3, [1] * 37, k=10) == pytest.approx(0.84, abs=0.01)

    def test_longer_crowders_lower_the_floor(self) -> None:
        """The same mechanism pointed the useful way: crowders long is what works."""
        assert blind_recall([1] * 3, [5] * 37, k=10) == pytest.approx(0.06, abs=0.01)

    def test_the_floor_is_monotone_in_expected_length(self) -> None:
        """Stated as a property, so the direction cannot silently invert."""
        floors = [blind_recall([c] * 3, [1] * 37, k=10) for c in (1, 2, 3, 5, 8)]
        assert floors == sorted(floors)

    def test_k_at_or_above_the_pool_is_a_certain_pass(self) -> None:
        """Ten slots for eight documents measures nothing at all."""
        assert blind_recall([1] * 3, [1] * 5, k=10) == 1.0

    def test_it_is_deterministic(self) -> None:
        """Quadrature, not sampling — no seed, no flake, byte-identical repeats."""
        args = ([3, 3, 5], [1, 2, 8] * 12)
        assert blind_recall(*args, k=12) == blind_recall(*args, k=12)

    def test_an_empty_expected_set_raises(self) -> None:
        """Mirrors `score_recall`'s refusal to pass a case that measures nothing."""
        with pytest.raises(ValueError, match="at least one expected"):
            blind_recall([], [1, 1], k=10)

    def test_a_non_positive_k_raises(self) -> None:
        """Mirrors `score_recall`'s own guard: k IS the measurement.

        A zero or negative cut is not a degenerate case to handle gracefully — it
        means the caller has computed a rank cut wrongly, and returning a number
        would launder that into a plausible-looking floor.
        """
        with pytest.raises(ValueError, match="k must be positive"):
            blind_recall([1, 1], [1, 1], k=0)

    def test_it_stays_accurate_as_the_pool_grows_in_chunks(self) -> None:
        """The integrator must not degrade silently as the corpus grows.

        The integrand is a polynomial in `1 - u` whose degree grows with the
        pool's TOTAL chunk count, so a fixed step count checked against one
        corpus goes quietly wrong on a larger one. Measured with the step count
        pinned at 512: exact to 1e-14 at 177 chunks (today's corpus), 1.9e-3
        relative error at 1,600, and 4.8e-2 at 3,200 — in both directions, so it
        could push a case either side of MAX_BLIND_RECALL.

        This corpus is deliberately growing in chunks, so that trajectory is
        live. The reference values below are the converged ones; they are checked
        tightly enough that reverting to a fixed 512 steps fails this test.
        """
        assert blind_recall([1] * 3, [5] * 320, k=10) == pytest.approx(0.006309556, rel=1e-6)
        assert blind_recall([1] * 3, [5] * 640, k=10) == pytest.approx(0.003139768, rel=1e-6)

    def test_a_zero_chunk_document_raises(self) -> None:
        """A body that yields no chunks is unretrievable, not a weightless entrant."""
        with pytest.raises(ValueError, match="at least one chunk"):
            blind_recall([1], [0, 1], k=10)
