"""Tier 2 — characterisation of PDF routing decisions over the real corpus.

Snapshots what ``_route_pdf`` *decides*, from ``analyze_pdf``'s output: page
count, how many pages are image-backed, whether the document reads as a scan,
and therefore which branch it takes. That decision is the thing worth pinning —
it is ours, and until now nothing exercised it against real scanner and real
government-exporter output, only synthetic fixtures.

**Why this stops at the decision and does not run the engines.** ``analyze_pdf``
is pure pypdfium2 parsing: no subprocess, no model, no language pack, so it gives
the same answer on a laptop and in CI. The engine *result* does not — running
OCRmyPDF needs ``tesseract-ocr-nld``, which CI installs and a dev machine
typically lacks, so an engine-level snapshot recorded locally would encode a
missing language pack as expected behaviour and go red in CI for the wrong
reason. Engine-level snapshots therefore have to be recorded in a CI-shaped
environment; see the note in ``docs/ingestion.md``.

Numbers are banded (``tests/golden_corpus``) so normal drift does not red an
unrelated PR, but the categorical facts — scan-like or not, which branch — are
exact, because a change there is a behavioural change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.config import get_settings
from library.ocr.analysis import analyze_pdf
from tests.golden_corpus import (
    corpus_documents,
    require_corpus,
    routing_snapshot_path,
    text_bucket,
)

# Snapshots live in the private corpus repo: their KEYS are the real filenames,
# which name the owner's insurer, bank, tax advisor and a medical screening
# programme. See tests/golden_corpus.py.


def _load() -> dict[str, dict[str, object]]:
    path = routing_snapshot_path()
    return json.loads(path.read_text()) if path.exists() else {}


def routing_decision(path: Path) -> dict[str, object]:
    """Reduce one PDF to the routing facts ``_route_pdf`` branches on.

    Mirrors the predicate in ``_route_pdf`` rather than re-deriving it, so a
    change to that predicate shows up here as a changed ``branch``.
    """
    analysis = analyze_pdf(path)
    floor = get_settings().text_layer_min_chars_per_page
    has_text_layer = analysis.pages > 0 and analysis.chars_per_page >= floor
    return {
        "pages": analysis.pages,
        "image_backed_pages": analysis.image_backed_pages,
        "scan_like": analysis.scan_like,
        "has_text_layer": has_text_layer,
        "text_bucket": text_bucket(len(analysis.text)),
        # The branch _route_pdf takes: the text layer is authoritative only for
        # a document that both has one and does not read as a scan.
        "branch": "text-layer" if (has_text_layer and not analysis.scan_like) else "ocr",
    }


def test_corpus_is_present() -> None:
    """Errors rather than skips when LIBRARY_GOLDEN_CORPUS=1.

    The guard that keeps this tier from becoming an invisible no-op: a broken
    corpus checkout in CI must fail, not skip and read as green.
    """
    documents = require_corpus()
    assert len(documents) >= 15, f"expected the full corpus, found {len(documents)}"


@pytest.mark.parametrize(
    "document_path",
    corpus_documents() or [pytest.param(None, marks=pytest.mark.skip(reason="no corpus"))],
    ids=lambda path: path.stem[:60] if path is not None else "no-corpus",
)
def test_routing_decision_matches_snapshot(document_path: Path | None) -> None:
    """Each real document still routes the way it did when recorded."""
    if document_path is None:  # pragma: no cover - placeholder when no corpus
        pytest.skip("no corpus")
    require_corpus()
    snapshots = _load()
    key = document_path.name
    assert key in snapshots, (
        f"no recorded routing snapshot for {key}.\n"
        f"Either the corpus repo does not carry the baselines yet — it is fetched "
        f"at its default-branch HEAD, so baseline updates must land there BEFORE "
        f"the library change is pushed — or this document is new and needs "
        f"recording: `python scripts/record_golden_routing.py --accept`."
    )

    actual = routing_decision(document_path)

    assert actual == snapshots[key], (
        f"routing changed for {key}:\n  recorded: {snapshots[key]}\n  actual:   {actual}"
    )


def test_the_corpus_exercises_both_branches() -> None:
    """A corpus that all routes one way characterises nothing.

    Guards the corpus rather than the code: if every document took the same
    branch, every snapshot above would pass while testing one path.
    """
    require_corpus()
    branches = {snap["branch"] for snap in _load().values()}
    assert branches == {"text-layer", "ocr"}, (
        f"the corpus exercises only {branches}; it needs at least one "
        "born-digital document and one scan to characterise routing"
    )


def test_scan_like_is_not_merely_a_text_length_proxy() -> None:
    """At least one document must have plenty of text and still read as a scan.

    This is the case the threshold exists for and the one a naive
    "enough characters means born-digital" rule gets wrong: a scanner app that
    embeds its own OCR produces a fat text layer over image-backed pages, and
    that text must be redone rather than trusted. Recorded from the real corpus,
    where such documents exist — so if a refactor collapses `scan_like` into a
    character-count test, this fails.
    """
    require_corpus()
    fat_scans = [
        name for name, snap in _load().items() if snap["scan_like"] and snap["has_text_layer"]
    ]
    assert fat_scans, (
        "no document in the corpus is both scan-like and text-layer-bearing; "
        "the redo-OCR path is then uncharacterised"
    )
