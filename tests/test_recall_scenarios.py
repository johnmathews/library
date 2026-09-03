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
from library.search import VECTOR_CANDIDATE_FANOUT
from tests.conftest import fetch_all

#: Below this the haystack stops discriminating: with ten retrieval slots and a
#: corpus of thirty, "retrieved" and "exists" converge and recall@10 is near 1.0
#: for everything. Raised to 180 over two rebuilds: first after the 2026-08-27
#: baseline came out at mean 0.917 — above the 0.90 ceiling docs/ask.md holds this
#: corpus to — because five of six cases expected a single document against only
#: three or four distractors and so could not lose recall at k=10 at all, and
#: again when the clusters grew to roughly forty. Chosen as a floor, not a target:
#: the corpus is now 252 documents and growing it further is fine.
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

#: The fewest chunks a document declared as a crowder may have. One chunk is
#: not crowding — it is an ordinary competitor. Three is the point at which a
#: document occupies meaningfully more of the candidate window than the
#: single-chunk majority of both this corpus and the real archive (whose
#: median is 2).
CROWDER_MIN_CHUNKS = 3

#: How much of each end of an answer sentence must also be unique to one
#: chunk. Fifty characters is a clause or so — long enough to be distinctive,
#: short enough to sit well inside the 200-character overlap window that would
#: duplicate it.
_NEEDLE_FRAGMENT_CHARS = 50


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


def test_declared_crowders_are_actually_long() -> None:
    """ "Long purely to crowd" has to be enforced, or it is just a comment.

    A crowder that gets shortened — or a short document that someone declares as
    a crowder — competes for one candidate slot instead of several and the case
    silently gets easier. Nothing else would notice: the blind floor would fall,
    which reads like the case got *harder*, and the guard below would still pass.
    """
    declared = [doc for doc in CORPUS if doc.crowds]
    # Without this the guard below iterates an empty list if every `crowds=` is
    # deleted, and passes — while every case's blind floor RISES (crowders leave
    # the pool), which reads as the corpus getting harder rather than blinder.
    # Nothing else in the suite would go red. Exactly the shape of guard this
    # corpus keeps growing by accident.
    assert declared, "no document declares `crowds`; the crowder guard is vacuous"
    for doc in declared:
        assert doc.chunks >= CROWDER_MIN_CHUNKS, (
            f"{doc.marker} is declared a crowder for {list(doc.crowds)} but yields "
            f"only {doc.chunks} chunk(s); a crowder needs at least "
            f"{CROWDER_MIN_CHUNKS} to occupy more than one candidate slot"
        )


def test_the_corpus_outgrows_the_ann_prefetch_window() -> None:
    """Under the window, the vector leg is exact and the eval scores the wrong thing.

    `semantic_search` prefetches `pool * VECTOR_CANDIDATE_FANOUT` CHUNKS, where
    `pool = max(top_k * 5, 50)`, and only then collapses to one row per document.
    If the whole corpus fits inside that window the `LIMIT` never binds: every
    chunk in existence is fetched and the vector leg becomes an exact global
    argmax — not the approximate retriever that ships.

    That was the state until this corpus grew: 201 chunks against a 300-chunk
    window at the breadth case's k=12. The deployed archive (1300 chunks) binds
    and the nightly's clean stack did not, which is a structural difference
    between the two environments that nothing recorded and that shows up in the
    numbers as a haystack effect.

    The constants are imported rather than copied, so a change to either one
    reds this instead of silently invalidating it.
    """
    # Mirrors `search.py`'s `pool = max(top_k * 5, 50)`, floor included. Dropping
    # the floor understates the window for any case with k < 10, which is the
    # UNSAFE direction: the guard would pass while the real prefetch still failed
    # to bind. Every case is k=10 or k=12 today, so this is latent, not live.
    window = max(max(case.k * 5, 50) for case in CASES) * VECTOR_CANDIDATE_FANOUT
    total_chunks = sum(doc.chunks for doc in CORPUS)
    assert total_chunks > window, (
        f"the corpus is {total_chunks} chunks against a prefetch window of {window}, "
        "so the ANN prefetch never binds and the eval scores an exact retriever "
        "rather than the shipped one — add crowders"
    )


def test_a_buried_answer_lands_in_exactly_one_chunk() -> None:
    """The premise of a passage case, which the chunker can silently break.

    `chunk_text` carries a 200-character overlap tail into the next chunk, so a
    sentence placed within ~200 characters of a boundary appears in TWO chunks.
    That breaks the case twice over: the answer no longer "lives in a specific
    passage", and — because ranking is max-over-chunks — the answer gets two
    draws instead of one, inflating the score for a reason that has nothing to do
    with retrieval quality.

    Nothing else would catch it. The declared chunk count would still be right,
    the blind floor would still pass, and the case would still look well formed.
    """
    by_marker = {doc.marker: doc for doc in CORPUS}
    needled = [case for case in CASES if case.answer_needle]
    assert needled, "no case declares `answer_needle`; this guard is vacuous"
    for case in needled:
        for marker in case.expected_markers:
            body = by_marker[marker].body
            settings = get_settings()
            chunks = chunk_text(
                body,
                max_chars=settings.embedding_chunk_chars,
                overlap=settings.embedding_chunk_overlap,
            )
            # Whole sentence, and each end of it. Checking only the whole
            # sentence leaves a hole that mutation testing found: with the needle
            # just past a boundary, the overlap carries its OPENING WORDS back
            # into the previous chunk. The full sentence then appears once — so a
            # whole-sentence check passes — while both chunks carry part of the
            # answer, which is the duplicated signal this guard exists to stop.
            for label, fragment in (
                ("sentence", case.answer_needle),
                ("opening", case.answer_needle[:_NEEDLE_FRAGMENT_CHARS]),
                ("closing", case.answer_needle[-_NEEDLE_FRAGMENT_CHARS:]),
            ):
                hits = [index for index, chunk in enumerate(chunks) if fragment in chunk]
                assert len(hits) == 1, (
                    f"{case.name}: the answer's {label} appears in {len(hits)} chunks "
                    f"of {marker} (chunks {hits}), not exactly one. The overlap is "
                    f"{settings.embedding_chunk_overlap} characters — move the "
                    "sentence away from the boundary rather than adjusting this guard."
                )


def test_a_buried_answer_appears_in_no_other_document() -> None:
    """Otherwise a miss can score as a hit.

    If any document outside the expected set states the answer, retrieving it
    instead of an expected one is a correct answer that the eval records as a
    failure — or, worse, the case passes for the wrong reason and stops measuring
    what its name claims.
    """
    needled = [case for case in CASES if case.answer_needle]
    assert needled, "no case declares `answer_needle`; this guard is vacuous"
    for case in needled:
        expected = set(case.expected_markers)
        leaks = [
            doc.marker
            for doc in CORPUS
            if doc.marker not in expected and case.answer_needle in doc.body
        ]
        assert not leaks, (
            f"{case.name}: the answer sentence also appears in {leaks}, so a "
            "document the case does not expect can answer the question"
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
    pre-rebuild all-single-chunk shapes, so the model reproduces the old numbers
    on the corpus the old formula was correct for.

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
