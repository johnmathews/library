"""Structural invariants of the recall corpus.

These do not measure retrieval — they check that the corpus can still
measure it. Every one of them corresponds to a way the corpus could be
edited into uselessness without any test going red.
"""

import pytest

from library.ask.recall_eval import blind_recall
from library.ask.recall_scenarios import CASES, CORPUS, FIXTURE_SUFFIX, MAX_BODY_CHARS
from library.config import get_settings
from library.embedding.chunker import chunk_text
from tests.conftest import fetch_all

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

#: How far a body may be padded or trimmed without changing its chunk count.
#: Chunk boundaries fall on word breaks, so a fixture within a word or two of a
#: threshold re-chunks under a trivial edit and its declaration silently stops
#: matching. Eighty characters is roughly ten words — comfortably more than one
#: edit, comfortably less than the 1600 characters a whole chunk occupies.
_BOUNDARY_SLACK_CHARS = 80


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


def _chunks_of(body: str) -> int:
    """Chunk count from the REAL chunker, at the real settings.

    `_seed_corpus` writes `mime_type="application/pdf"` and no page rows, so
    `run_embed` takes the `ocr_text` branch and `chunker_for_mime` routes to
    `chunk_text`. Same function, same arguments, so this guard cannot drift from
    what the pipeline actually does.
    """
    settings = get_settings()
    return len(
        chunk_text(
            body,
            max_chars=settings.embedding_chunk_chars,
            overlap=settings.embedding_chunk_overlap,
        )
    )


def test_every_declared_chunk_count_matches_the_real_chunker() -> None:
    """`RecallDoc.chunks` is a claim about the pipeline. Check it against the pipeline.

    Replaces `test_every_body_yields_exactly_one_chunk`, which asserted
    `len(body) <= MAX_BODY_CHARS` — a length proxy for "one chunk". Two reasons it
    had to go, beyond the corpus no longer being single-chunk:

    1. **It passed on a body that produces ZERO chunks.** `""` and `"   "` satisfy
       `len(body) <= 1800` while `chunk_text` returns `[]`. A guard named for
       "exactly one chunk" could not fail on the case it is named for.
    2. **Length does not determine chunk count.** The packer works in whole words,
       so every boundary drifts by up to a word and the drift accumulates — the
       closed form says 5 chunks at 6610 characters and the chunker returns 4. And
       `str.split()` collapses whitespace, so this corpus's own column-padded
       figures bodies chunk as 158 characters where they measure 220.

    `MAX_BODY_CHARS` is kept, but only for what it can honestly do: pin that the
    module's mirror of `embedding_chunk_chars` has not drifted above the setting.
    """
    assert get_settings().embedding_chunk_chars >= MAX_BODY_CHARS
    for doc in CORPUS:
        assert _chunks_of(doc.body) == doc.chunks, (
            f"{doc.marker} declares {doc.chunks} chunk(s) but the chunker produces "
            f"{_chunks_of(doc.body)}"
        )


def test_no_fixture_sits_on_a_chunk_boundary() -> None:
    """A declared count that is one edit away from being wrong is not a guarantee.

    Because chunk boundaries land on word breaks, a body within a word or two of a
    threshold re-chunks under a trivial edit — fixing a typo, adding a clause — and
    the declaration silently stops matching. That would change the crowding
    pressure AND the blind floor with no test able to see it, which is the exact
    silent-miscount this corpus is most exposed to now that lengths vary.

    So require the count to be stable across ±80 characters. A fixture that fails
    this is not wrong today; it is sitting on a cliff, and the fix is to move it
    into the middle of its band rather than to widen the slack.
    """
    padding = " padding" * 10
    assert len(padding) == _BOUNDARY_SLACK_CHARS
    for doc in CORPUS:
        longer = _chunks_of(doc.body + padding)
        shorter = _chunks_of(doc.body[: max(1, len(doc.body) - _BOUNDARY_SLACK_CHARS)])
        assert longer == doc.chunks, (
            f"{doc.marker} declares {doc.chunks} chunk(s) but yields {longer} with "
            f"{_BOUNDARY_SLACK_CHARS} more characters — it is on a boundary"
        )
        assert shorter == doc.chunks, (
            f"{doc.marker} declares {doc.chunks} chunk(s) but yields {shorter} with "
            f"{_BOUNDARY_SLACK_CHARS} fewer characters — it is on a boundary"
        )


def test_every_sender_is_marked_as_a_fixture() -> None:
    for doc in CORPUS:
        assert doc.sender_name.endswith(FIXTURE_SUFFIX), doc.marker


@pytest.mark.integration
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

    **The floor is weighted by chunk count, and that is not a refinement.** This
    check used to compute ``min(k, len(pool)) / len(pool)`` — documents drawn
    uniformly. `semantic_search` ranks a document by its NEAREST CHUNK, so a
    document with `c` chunks gets `c` draws. The two models agree exactly while
    every fixture is one chunk, and diverge in the PASSING direction the moment
    lengths vary: three 5-chunk expected documents among 37 single-chunk crowders
    have a true floor of 0.70 while the uniform formula reports 0.25 and passes.
    That is precisely the case shape issue #106 asks for, so the old formula
    would have waved through the first fixture this corpus grew.
    `library.ask.recall_eval.blind_recall` carries the model and the derivation;
    `tests/test_recall_eval.py` pins it against the uniform formula for the
    all-single-chunk corpus, so today's five numbers are unchanged.

    "Plausible candidate" is a document sharing a sender or a title with an
    expected document — exactly how this corpus places its near-misses — plus any
    document that names the case in `crowds`, for crowders that deliberately
    share neither.
    """
    by_marker = {doc.marker: doc for doc in CORPUS}
    for case in CASES:
        if case.name == "control-unique-term":
            continue
        expected_markers = set(case.expected_markers)
        expected = [by_marker[marker] for marker in case.expected_markers]
        senders = {doc.sender_name for doc in expected}
        titles = {doc.title for doc in expected}
        pool = [
            doc
            for doc in CORPUS
            if doc.sender_name in senders or doc.title in titles or case.name in doc.crowds
        ]
        floor = blind_recall(
            [doc.chunks for doc in expected],
            [doc.chunks for doc in pool if doc.marker not in expected_markers],
            k=case.k,
        )
        assert floor <= MAX_BLIND_RECALL, (
            f"{case.name}: {len(pool)} candidates for a cut of {case.k}, so a "
            f"retriever ranking at RANDOM already scores {floor:.2f}. "
            "The case has too little room to fall to measure anything. If the "
            "expected documents are longer than their crowders, that is the "
            "cause — lengthen the crowders instead."
        )
