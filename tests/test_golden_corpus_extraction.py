"""Tier 1 — characterisation of extraction over the real corpus, from cassettes.

Marked ``golden_extraction``. No network, no API key, no database and no OCR
binaries — the LLM call is replayed from a recorded cassette
(``tests/golden_cassette.py``) and the OCR text comes from the snapshot rather
than being re-derived.

It **does** need the corpus, though, which is worth being precise about because
an earlier version of this file claimed otherwise. ``extract`` runs
``build_user_content`` for real — that is the point, it is what makes
``input_mode`` a characterised decision rather than a replayed constant — and for
a thin scan that means reading the original file from the content-addressed
store. With no original, ``build_user_content`` raises ``ExtractionSkipped``,
``extract`` falls back to the text path, and the recorded ``input_mode=document``
silently becomes ``text``. So the corpus is staged into a temp data dir per test.

What each document pins: ``(input_mode, escalated, kind_slug, review_status,
sorted(rules))``. All categorical, all ours, and all things a refactor can change
silently:

- ``input_mode`` — whether the thin-scan trigger sent the page image instead of
  the text. That decision is invisible in the response and has burned this
  project before.
- ``escalated`` — whether the low-confidence second call fired.
- ``review_status`` + the rule list — what the deterministic validation rules
  make of the result.

Deliberately **not** pinned: amounts, dates, sender names, titles, summaries.
Those are the model's judgement, they move with any prompt or model change, and
asserting them turns a characterisation suite into a flaky one that gets deleted.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from library.config import get_settings
from library.extraction import extractor as extraction_extractor
from library.extraction.validation import derive_review_status, validate
from library.models import Document, DocumentSource
from library.storage import path_for
from tests.golden_cassette import (
    apply_metadata_for_validation,
    load_cassettes,
    replay,
)
from tests.golden_corpus import (
    corpus_dir,
    extraction_snapshot_path,
    require_corpus,
)

pytestmark = pytest.mark.golden_extraction

# The snapshots (which include each document's full OCR text) live in the private
# corpus repo, not here — see tests/golden_corpus.py.

#: A fixed "today" so date_plausibility cannot change answer with the calendar.
#: Without this the suite would start failing on its own, months from now, for no
#: code reason — the failure mode a calendar-driven gate always has.
FIXED_TODAY: date = date(2026, 7, 28)


def load_snapshots() -> dict[str, dict[str, Any]]:
    path = extraction_snapshot_path()
    return json.loads(path.read_text()) if path.exists() else {}


def _document_for(name: str, snapshot: dict[str, Any]) -> Document:
    """A transient Document carrying what extraction and validation read.

    Built via the normal constructor, not ``__new__`` + ``setattr``: mapped
    columns are data descriptors, so an instance-dict value would be ignored on
    read (the same trap ``tests/test_extraction_validation.py`` documents).
    """
    return Document(
        sha256=snapshot["sha256"],
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename=name,
        ocr_text=snapshot["ocr_text"],
        page_count=snapshot["page_count"],
        # Load-bearing: _thin_scan_prefers_vision short-circuits to False when
        # ocr_confidence is None, so leaving it unset sends a thin scan down the
        # text path in replay while the recording used vision.
        ocr_confidence=snapshot["ocr_confidence"],
        extra={},
    )


def test_cassettes_are_present() -> None:
    """Errors rather than skips when the corpus is required.

    Same guard as the routing tier: an absent cassette file in CI means the
    recorder was never run or the artifact was lost, and either way a skip would
    report that as green.
    """
    snapshots = load_snapshots()
    cassettes = load_cassettes()
    if snapshots and cassettes:
        assert len(snapshots) >= 15, f"expected the full corpus, found {len(snapshots)}"
        return
    require_corpus()  # raises under LIBRARY_GOLDEN_CORPUS=1, else skips
    pytest.skip(
        "no recorded extraction cassettes; run "
        "`python scripts/record_golden_extractions.py` with an API key"
    )


@pytest.mark.parametrize(
    "name",
    sorted(load_snapshots())
    or [pytest.param("no-snapshots", marks=pytest.mark.skip(reason="not recorded"))],
    ids=lambda name: name[:60],
)
async def test_extraction_matches_snapshot(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay one document's extraction and compare its categorical shape."""
    snapshots = load_snapshots()
    if name not in snapshots:  # pragma: no cover - placeholder param
        pytest.skip("not recorded")
    require_corpus()  # the original must be readable; see the module docstring
    cassettes = load_cassettes()
    assert cassettes, "snapshots exist but cassettes do not; re-record"

    from library.config import Settings

    settings = Settings(_env_file=None, anthropic_api_key="test-key-not-used")
    monkeypatch.setattr(extraction_extractor, "_attempt", replay(cassettes))

    snapshot = snapshots[name]
    document = _document_for(name, snapshot)

    # Stage the original exactly as the recorder did. Without it the thin-scan
    # branch cannot read the file, extract() falls back to text, and the
    # cassette lookup misses — which is how this surfaced.
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    source = corpus_dir() / name
    stored = path_for(document.sha256)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(source.read_bytes())

    outcome = await extraction_extractor.extract(
        document,
        snapshot["ocr_text"],
        client=None,  # never used: _attempt is replaced
        settings=settings,
    )

    # Applied through the SAME helper the recorder uses, so a snapshot cannot
    # characterise the difference between two copies of this logic.
    metadata = outcome.metadata
    apply_metadata_for_validation(document, metadata, outcome.input_mode)

    findings = validate(
        document,
        kind_slug=metadata.kind_slug,
        sender_name=metadata.sender_name,
        ocr_floor=settings.extraction_validation_ocr_floor,
        today=FIXED_TODAY,
    )
    actual = {
        "input_mode": outcome.input_mode,
        "escalated": outcome.escalated,
        "kind_slug": metadata.kind_slug,
        "review_status": derive_review_status(findings).value,
        "rules": sorted({finding.rule for finding in findings}),
    }
    expected = {key: snapshot[key] for key in actual}

    assert actual == expected, (
        f"extraction characterisation changed for {name}:\n"
        f"  recorded: {expected}\n"
        f"  actual:   {actual}"
    )


def test_the_corpus_exercises_more_than_one_input_mode() -> None:
    """A corpus that all extracts one way characterises nothing.

    The thin-scan vision trigger is the branch most worth covering here, so at
    minimum the snapshots must show it firing for some documents and not others.
    """
    snapshots = load_snapshots()
    if not snapshots:
        pytest.skip("not recorded")
    modes = {snap["input_mode"] for snap in snapshots.values()}
    assert len(modes) >= 2, (
        f"every corpus document extracted with input_mode={modes}; the vision "
        "trigger is then uncharacterised"
    )
