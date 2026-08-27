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
#: for everything. Chosen as a floor, not a target — growing the corpus is fine.
MIN_CORPUS_SIZE = 45


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
