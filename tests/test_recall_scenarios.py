"""Structural invariants of the recall corpus.

These do not measure retrieval — they check that the corpus can still
measure it. Every one of them corresponds to a way the corpus could be
edited into uselessness without any test going red.
"""

import pytest

from library.ask.recall_scenarios import CASES, CORPUS, FIXTURE_SUFFIX, MAX_BODY_CHARS
from library.config import get_settings
from tests.conftest import fetch_all

pytestmark = pytest.mark.integration

#: Below this the haystack stops discriminating: with ten retrieval slots and a
#: corpus of thirty, "retrieved" and "exists" converge and recall@10 is near 1.0
#: for everything. Raised from 45 to 80 after the first real baseline came out at
#: mean 0.917 — above the 0.90 ceiling docs/ask.md holds this corpus to — because
#: five of six cases expected a single document against only three or four
#: distractors and so could not lose recall at k=10 at all. Chosen as a floor,
#: not a target: growing the corpus is fine.
MIN_CORPUS_SIZE = 180

#: The score a retriever that ranked at RANDOM would get on a case. It is the
#: floor of that case's range, so the closer it sits to 1.0 the less the case can
#: say. At 0.77 (a pool of 13 against a cut of 10) the corpus measured nothing;
#: this ceiling keeps every case's pool several times its cut.
MAX_BLIND_RECALL = 0.35


def test_markers_are_unique() -> None:
    markers = [doc.marker for doc in CORPUS]
    assert len(markers) == len(set(markers))


def test_every_case_expects_documents_that_exist() -> None:
    markers = {doc.marker for doc in CORPUS}
    for case in CASES:
        assert case.expected_markers, f"{case.name} expects nothing — see score_recall"
        unknown = set(case.expected_markers) - markers
        assert not unknown, f"{case.name} expects unknown markers: {sorted(unknown)}"


def test_corpus_is_large_enough_to_discriminate() -> None:
    assert len(CORPUS) >= MIN_CORPUS_SIZE


def test_every_body_yields_exactly_one_chunk() -> None:
    """Document-level recall is only unambiguous if a document is one chunk."""
    limit = get_settings().embedding_chunk_chars
    assert limit >= MAX_BODY_CHARS
    for doc in CORPUS:
        assert len(doc.body) <= MAX_BODY_CHARS, doc.marker


def test_every_sender_is_marked_as_a_fixture() -> None:
    for doc in CORPUS:
        assert doc.sender_name.endswith(FIXTURE_SUFFIX), doc.marker


def test_every_kind_slug_is_seeded_in_the_database(api_database_url: str) -> None:
    """A typo'd slug makes `_seed_corpus`'s `scalar_one()` raise mid-run."""
    rows = fetch_all(api_database_url, "SELECT slug FROM kinds")
    seeded = {row[0] for row in rows}
    used = {doc.kind_slug for doc in CORPUS}
    assert used <= seeded, f"unseeded kind slugs: {sorted(used - seeded)}"


def test_breadth_case_is_unreachable_at_the_shipped_top_k() -> None:
    """The #7 case must be constructed so today's depth cannot satisfy it."""
    breadth = next(c for c in CASES if c.name == "breadth-many-mentions")
    assert len(breadth.expected_markers) > get_settings().retrieve_top_k
    assert breadth.k >= len(breadth.expected_markers)


def test_only_the_control_case_expects_a_single_document() -> None:
    """A single expected document at k=10 has almost no resolution.

    It scores 1.0 unless ten documents outrank it, which no cluster in this
    corpus is meant to achieve. Multi-target cases lose recall gradually, which
    is what makes a delta readable. `control-unique-term` is exempt by design:
    it is the canary, and it is supposed to sit at 1.00.
    """
    for case in CASES:
        if case.name == "control-unique-term":
            assert len(case.expected_markers) == 1
            continue
        assert len(case.expected_markers) >= 2, (
            f"{case.name} expects one document at k={case.k}; it cannot lose recall"
        )


def test_every_case_competes_against_more_documents_than_its_cut() -> None:
    """Each case needs more plausible candidates than its rank cut.

    "Larger than k" is not enough, which the 2026-08-27 measurements showed the
    expensive way: at a pool of 13 against a cut of 10, a retriever ranking at
    RANDOM already scores 0.77, so the corpus scored 0.9028 and still could not
    discriminate. The pool must be several times the cut before the difference
    between good and bad retrieval shows up in the number.

    "Plausible candidate" is approximated as sharing a sender or a title with an
    expected document, which is exactly how this corpus places its near-misses.
    """
    by_marker = {doc.marker: doc for doc in CORPUS}
    for case in CASES:
        if case.name == "control-unique-term":
            continue
        expected = [by_marker[marker] for marker in case.expected_markers]
        senders = {doc.sender_name for doc in expected}
        titles = {doc.title for doc in expected}
        pool = {doc.marker for doc in CORPUS if doc.sender_name in senders or doc.title in titles}
        blind_recall = min(case.k, len(pool)) / len(pool)
        assert blind_recall <= MAX_BLIND_RECALL, (
            f"{case.name}: {len(pool)} candidates for a cut of {case.k}, so a "
            f"retriever ranking at RANDOM already scores {blind_recall:.2f}. "
            "The case has too little room to fall to measure anything."
        )
