#!/usr/bin/env python3
"""Record extraction cassettes + snapshots for the golden corpus.

This is the **one step that needs an API key and real spend** — single-digit
dollars for the whole corpus. Everything downstream replays from what this
writes, so it runs once per prompt/model change, not per test run.

What it writes, both committed:

- ``tests/golden_cassettes.json`` — the model's structured output and token
  counts, keyed by (model, sha256(prompt content)). **No document bytes, no API
  key**, so it is safe in a public repo even though the corpus is not.
- ``tests/golden_extraction_snapshots.json`` — the categorical shape per
  document, plus the OCR text extraction ran on, so the test tier needs neither
  an API key nor OCR binaries.

Requirements:
- the corpus, in ``samples/`` or at ``LIBRARY_GOLDEN_CORPUS_DIR``
- ``ANTHROPIC_API_KEY`` (or ``LIBRARY_ANTHROPIC_API_KEY``) in the environment
- the OCR stack, since OCR text is derived here once: ``tesseract`` with the
  ``nld`` language pack, plus ``gs`` and ``unpaper``

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/record_golden_extractions.py
    ... --dry-run     # report what would be recorded, spend nothing
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from anthropic import AsyncAnthropic  # noqa: E402

from library.config import Settings, get_settings  # noqa: E402
from library.extraction import extractor as extraction_extractor  # noqa: E402
from library.extraction.validation import derive_review_status, validate  # noqa: E402
from library.models import Document, DocumentSource  # noqa: E402
from library.ocr import router as ocr_router  # noqa: E402
from library.storage import path_for  # noqa: E402
from tests.golden_cassette import (  # noqa: E402
    apply_metadata_for_validation,
    content_key,
)
from tests.golden_corpus import (  # noqa: E402
    cassette_path,
    corpus_dir,
    corpus_documents,
    extraction_snapshot_path,
)
from tests.test_golden_corpus_extraction import FIXED_TODAY  # noqa: E402


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LIBRARY_ANTHROPIC_API_KEY")


def _missing_tesseract_languages(settings: Settings) -> list[str]:
    """Configured OCR languages that this machine's tesseract cannot provide."""
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return sorted(set(settings.ocr_languages.split("+")))
    available = {line.strip() for line in proc.stdout.splitlines()[1:] if line.strip()}
    return sorted(set(settings.ocr_languages.split("+")) - available)


def preflight(settings: Settings, allow_degraded: bool) -> int:
    """Refuse to spend money recording a baseline this machine cannot produce.

    Without the configured language packs, OCRmyPDF raises and the router falls
    back to the PDF's *embedded* text layer. That still yields text, so nothing
    looks wrong — but ``ocr_confidence`` comes back None, which makes
    ``_thin_scan_prefers_vision`` short-circuit to False and stops the
    ``ocr_confidence_gate`` rule from ever firing. The recorded snapshots would
    be self-consistent and would characterise a pipeline nobody runs.

    This is a paid, once-per-prompt-change operation, so it fails closed.
    """
    missing = _missing_tesseract_languages(settings)
    if not missing:
        return 0
    message = (
        f"tesseract is missing the configured language pack(s): {', '.join(missing)} "
        f"(LIBRARY_OCR_LANGUAGES={settings.ocr_languages}).\n"
        "OCR will fall back to each PDF's embedded text layer, leaving\n"
        "ocr_confidence None — so the vision trigger and the ocr_confidence_gate\n"
        "rule can never fire and the recorded baseline will not match production.\n"
        "  macOS:  brew install tesseract-lang\n"
        "  Debian: apt-get install tesseract-ocr-nld\n"
        "Pass --allow-degraded-ocr to record anyway (not recommended)."
    )
    if allow_degraded:
        print(f"warning: {message}", file=sys.stderr)
        return 0
    print(f"error: {message}", file=sys.stderr)
    return 2


async def record_one(
    path: Path,
    settings: Settings,
    client: AsyncAnthropic,
    cassettes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """OCR one document, extract it for real, and record both sides."""
    document = Document(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        original_filename=path.name,
        extra={},
    )
    # Stage the original into the content-addressed store first. Without it,
    # build_user_content(force_file=True) cannot find the file, raises
    # ExtractionSkipped, and extract() falls back to the text path — so the
    # thin-scan VISION branch would never be recorded, which is the branch most
    # worth characterising. Two corpus documents take it (615 and 336 chars per
    # page against the 800 threshold), and both silently came back input_mode=
    # "text" until this was fixed.
    stored = path_for(document.sha256)
    stored.parent.mkdir(parents=True, exist_ok=True)
    if not stored.exists():
        stored.write_bytes(path.read_bytes())

    with tempfile.TemporaryDirectory() as tmp:
        ocr = ocr_router.run_ocr(document, path, Path(tmp), settings=settings)
    document.ocr_text = ocr.text or None
    document.page_count = ocr.pages
    # Load-bearing, and easy to omit: _thin_scan_prefers_vision returns False
    # immediately when ocr_confidence is None, and the ocr_confidence_gate
    # validation rule reads it too. Dropping it silently records a baseline in
    # which the vision trigger can never fire.
    document.ocr_confidence = ocr.confidence

    # Wrap the real _attempt so every call is captured as it happens, including
    # the escalation call, keyed exactly as the replay will look it up.
    real_attempt = extraction_extractor._attempt

    async def recording_attempt(
        inner_client: Any, model: str, content: list[dict[str, Any]]
    ) -> Any:
        metadata, usage = await real_attempt(inner_client, model, content)
        cassettes[content_key(model, content)] = {
            "metadata": json.loads(metadata.model_dump_json()),
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
        }
        return metadata, usage

    extraction_extractor._attempt = recording_attempt  # type: ignore[assignment]
    try:
        outcome = await extraction_extractor.extract(
            document, ocr.text or "", client=client, settings=settings
        )
    finally:
        extraction_extractor._attempt = real_attempt  # type: ignore[assignment]

    metadata = outcome.metadata
    apply_metadata_for_validation(document, metadata, outcome.input_mode)
    findings = validate(
        document,
        kind_slug=metadata.kind_slug,
        sender_name=metadata.sender_name,
        ocr_floor=settings.extraction_validation_ocr_floor,
        today=FIXED_TODAY,
    )
    return {
        "sha256": document.sha256,
        "ocr_text": document.ocr_text,
        "page_count": document.page_count,
        # Persisted because the replay must reconstruct the document faithfully:
        # _thin_scan_prefers_vision returns False when this is None, so omitting
        # it makes the replay take the text path while the recording took the
        # vision path — and the cassette lookup then misses.
        "ocr_confidence": document.ocr_confidence,
        "input_mode": outcome.input_mode,
        "escalated": outcome.escalated,
        "kind_slug": metadata.kind_slug,
        "review_status": derive_review_status(findings).value,
        "rules": sorted({finding.rule for finding in findings}),
        "cost_usd": round(outcome.cost_usd, 6),
    }


async def run(dry_run: bool, allow_degraded: bool) -> int:
    documents = corpus_documents()
    if not documents:
        print(f"error: no corpus documents under {corpus_dir()}", file=sys.stderr)
        return 2
    if dry_run:
        print(f"would record {len(documents)} document(s) from {corpus_dir()}:")
        for path in documents:
            print(f"  {path.name}")
        missing = _missing_tesseract_languages(Settings(_env_file=None))
        if missing:
            print(
                f"\nWARNING: tesseract is missing {', '.join(missing)} — the real run "
                "will refuse to start. See --allow-degraded-ocr.",
                file=sys.stderr,
            )
        print("\nre-run without --dry-run to record (spends a few dollars).")
        return 0

    key = _api_key()
    if not key:
        print(
            "error: set ANTHROPIC_API_KEY (or LIBRARY_ANTHROPIC_API_KEY). This is "
            "the one step that needs real API access.",
            file=sys.stderr,
        )
        return 2

    settings = Settings(_env_file=None, anthropic_api_key=key)
    if (code := preflight(settings, allow_degraded)) != 0:
        return code
    # Originals are staged into a throwaway data dir rather than a real one:
    # this script is a recorder, not an ingest path, and must not deposit
    # documents into anybody's store. Set before any path_for() call, and
    # cache-cleared because get_settings() memoises.
    staging = tempfile.TemporaryDirectory()
    os.environ["LIBRARY_DATA_DIR"] = staging.name
    get_settings.cache_clear()
    cassettes: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, Any] = {}
    total = 0.0
    async with AsyncAnthropic(api_key=key) as client:
        for index, path in enumerate(documents, start=1):
            print(f"[{index}/{len(documents)}] {path.name}", file=sys.stderr)
            snapshot = await record_one(path, settings, client, cassettes)
            snapshots[path.name] = snapshot
            total += snapshot["cost_usd"]

    staging.cleanup()
    cassettes_out = cassette_path()
    snapshots_out = extraction_snapshot_path()
    cassettes_out.write_text(json.dumps(cassettes, indent=2, sort_keys=True) + "\n")
    snapshots_out.write_text(json.dumps(snapshots, indent=2, sort_keys=True) + "\n")
    print(
        f"\nwrote {cassettes_out} ({len(cassettes)} calls)\n"
        f"      {snapshots_out} ({len(snapshots)} documents)\n"
        f"total spend: ${total:.4f}\n\n"
        "These land in the PRIVATE corpus repo, not this one: they contain each\n"
        "document's full OCR text plus the model's titles and summaries. Commit\n"
        "them there, and review the snapshot diff first — that diff is the\n"
        "review artifact."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be recorded without calling the API",
    )
    parser.add_argument(
        "--allow-degraded-ocr",
        action="store_true",
        help="record even when tesseract lacks the configured language packs",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args.dry_run, args.allow_degraded_ocr))


if __name__ == "__main__":
    raise SystemExit(main())
