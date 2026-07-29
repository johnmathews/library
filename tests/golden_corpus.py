"""Locating the real-document corpus, and the banding rules for its snapshots.

The corpus is **fetched, never committed**. It is real financial and personal
correspondence and this repository is public, so the 15 documents live in a
private sibling repo (``johnmathews/library-golden-corpus``) which CI checks out
into ``samples/`` and a developer already has there.

Honest cost of that decision: **pull requests from forks cannot run these
tests**, because they get no access to the corpus secret. That is mitigated the
same way W16 handles the e2e stack — when ``LIBRARY_GOLDEN_CORPUS=1`` is set, a
missing corpus is a **hard error, not a skip**. CI sets it, so CI cannot quietly
stop running these; a laptop without the corpus skips, which is the ergonomic
default.

That distinction is the whole point. A characterisation suite that silently
skips is worse than no suite: it reports green while measuring nothing, which is
exactly the failure W21 fixed in the RapidOCR guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Set by CI. Turns "corpus absent" from a skip into a failure.
REQUIRE_ENV: str = "LIBRARY_GOLDEN_CORPUS"

#: Overrides the corpus location. Needed in practice, not speculative: this
#: project develops in git worktrees, and ``samples/`` is gitignored, so it
#: exists only in the main checkout. Point this at it from a worktree.
DIR_ENV: str = "LIBRARY_GOLDEN_CORPUS_DIR"

#: Default corpus location: gitignored, where a developer already keeps these
#: files, and where CI's checkout step puts them — so the common cases need no
#: configuration at all.
DEFAULT_CORPUS_DIR: Path = Path(__file__).resolve().parent.parent / "samples"


def corpus_dir() -> Path:
    """The corpus directory, honouring the override."""
    override = os.environ.get(DIR_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_CORPUS_DIR


# --- Where the recorded baselines live -----------------------------------------
#
# In the PRIVATE corpus repo, alongside the documents — NOT in this public one.
#
# That is a correction, and worth stating plainly because the first version of
# this module got it wrong and claimed the baselines were "safe to commit: the
# model's structured output only, no document bytes". Text is not safer than
# bytes, it is worse: it is readable and indexable. The snapshots carry every
# document's full OCR text (~153,000 characters across the corpus) and the
# cassettes carry the model's titles, summaries, senders and amounts — including
# medical information. Even the snapshot *keys* are the real filenames, which
# name the owner's insurer, bank, tax advisor and a medical screening programme.
#
# One rule, so there is no per-field judgement call to get wrong later:
# **anything derived from the corpus lives with the corpus.** This repository
# holds the test code and nothing else.


def cassette_path() -> Path:
    """Recorded LLM responses for the Tier-1 replay (private corpus repo)."""
    return corpus_dir() / "golden_cassettes.json"


def extraction_snapshot_path() -> Path:
    """Recorded extraction characterisation per document (private corpus repo)."""
    return corpus_dir() / "golden_extraction_snapshots.json"


def routing_snapshot_path() -> Path:
    """Recorded PDF routing decisions per document (private corpus repo)."""
    return corpus_dir() / "golden_routing_snapshots.json"


def corpus_required() -> bool:
    """True when a missing corpus must fail rather than skip."""
    return os.environ.get(REQUIRE_ENV, "").strip().lower() in ("1", "true", "yes")


def corpus_documents() -> list[Path]:
    """Every PDF in the corpus, in a stable order.

    Sorted by name so test ids are deterministic across machines — the corpus
    filenames begin with an ISO date, so this is also chronological.
    """
    directory = corpus_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.pdf"))


def require_corpus() -> list[Path]:
    """The corpus, or skip/fail depending on whether it is required here.

    Deliberately not a plain ``pytest.skip`` on absence: under
    ``LIBRARY_GOLDEN_CORPUS=1`` the absence is a broken checkout step, and a
    broken checkout step that skips is indistinguishable from a green run.
    """
    documents = corpus_documents()
    if documents:
        return documents
    message = (
        f"golden corpus not found at {corpus_dir()}. It is fetched, not committed: "
        f"clone johnmathews/library-golden-corpus into samples/, or set "
        f"{DIR_ENV} to where it already is."
    )
    if corpus_required():
        raise AssertionError(f"{REQUIRE_ENV} is set but {message}")
    pytest.skip(message)


# --- Banding -----------------------------------------------------------------
#
# The evaluation proposed snapshotting exact `ocr_confidence` and exact
# `len(ocr_text)`. Both are wrong to pin: they move on any `tesseract-ocr` apt
# bump or `rapidocr` model revision, and that break surfaces as a red CI on an
# unrelated PR — which is how characterisation tests get deleted rather than
# investigated. So OCR-derived numbers are banded and the *categorical* facts
# (engine, input_mode, review_status, the rule list) stay exact.


def confidence_band(confidence: float | None) -> str:
    """Coarse confidence bucket, stable across engine revisions."""
    if confidence is None:
        return "none"
    if confidence >= 90.0:
        return "high"
    if confidence >= 70.0:
        return "good"
    if confidence >= 50.0:
        return "fair"
    return "low"


def text_bucket(length: int) -> str:
    """Order-of-magnitude bucket for extracted text length.

    Powers of ten, so a genuine regression (a page of text becoming nothing)
    moves the bucket while normal engine drift does not.
    """
    if length == 0:
        return "empty"
    if length < 100:
        return "tiny"
    if length < 1_000:
        return "small"
    if length < 10_000:
        return "medium"
    return "large"
