#!/usr/bin/env python3
"""Populate or verify the vendored RapidOCR weights in ``models/ocr/``.

The three pinned ONNX models are committed to this repository (see
``library.ocr.weights`` for why). This script is the only thing that puts them
there, and the only thing that says whether what is there is right.

Two modes:

``--check``
    Verify every pinned model is present with the expected SHA256. Exits 1
    naming what is wrong. This is what ``compose-smoke`` runs *inside* the
    running containers — the weights being committed says nothing about them
    being in the image, and a file read at run time from inside the image needs
    a Dockerfile ``COPY`` to actually ship. Cheap enough to run anywhere:
    ~13 MB of hashing.

default
    Download whatever is missing or mismatched from the pinned upstream URLs.
    Run this after a rapidocr bump changes what the pins resolve to, then
    commit the result. Needs network; the whole point of vendoring is that
    nothing else does.

Usage:
    python -m scripts.fetch_ocr_models            # download what is missing
    python -m scripts.fetch_ocr_models --check    # verify, exit 1 if not ok
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from library.ocr import weights
from library.ocr.weights import PinnedModel

# 1 MiB: large enough that hashing 13 MB is a couple of dozen reads, small
# enough not to matter on a container with a 512 MB limit.
_CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def problem(model: PinnedModel) -> str | None:
    """What is wrong with this model's file on disk, or None if nothing is.

    A wrong checksum is reported as its own case rather than folded into
    "missing": a truncated download and a model that was silently repointed by
    a rapidocr release need different fixes, and the message should say which.
    """
    if not model.path.is_file():
        return f"missing: {model.path}"
    actual = file_sha256(model.path)
    if actual != model.sha256:
        return (
            f"checksum mismatch: {model.path}\n    expected {model.sha256}\n    actual   {actual}"
        )
    return None


def check() -> int:
    problems = [(model, detail) for model in weights.pinned_models() if (detail := problem(model))]
    if problems:
        print(f"error: vendored OCR weights are not usable ({weights.MODEL_DIR}):", file=sys.stderr)
        for model, detail in problems:
            print(f"  - {model.task.value}: {detail}", file=sys.stderr)
        print(
            "\nPhoto OCR (JPEG/PNG/HEIC, and any scan that falls through the "
            "Tesseract confidence gate) cannot run without these. Repopulate "
            "with `python -m scripts.fetch_ocr_models` and commit the result.",
            file=sys.stderr,
        )
        return 1
    for model in weights.pinned_models():
        print(f"ok: {model.task.value} {model.path.name}")
    return 0


def fetch() -> int:
    """Download every pinned model that is absent or does not match its checksum.

    Delegates to rapidocr's own downloader so the fetch and the run-time lookup
    agree on where a file goes and what it must hash to — the same call the
    engine used to make on first construction, only now it happens once, on a
    developer's machine, with the result committed.
    """
    from rapidocr.utils.download_file import DownloadFile, DownloadFileInput
    from rapidocr.utils.log import logger

    weights.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for model in weights.pinned_models():
        detail = problem(model)
        if detail is None:
            print(f"ok: {model.task.value} {model.path.name}")
            continue
        print(f"fetching {model.task.value}: {detail.splitlines()[0]}")
        # A mismatched file would make rapidocr re-download anyway, but unlink
        # it first so a failed fetch leaves no plausible-looking wrong weights
        # behind for `--check` to pass on later.
        model.path.unlink(missing_ok=True)
        DownloadFile.run(
            DownloadFileInput(
                file_url=model.url,
                sha256=model.sha256,
                save_path=model.path,
                logger=logger,
            )
        )
    return check()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the vendored weights instead of downloading; exit 1 if any is wrong",
    )
    args = parser.parse_args(argv)
    return check() if args.check else fetch()


if __name__ == "__main__":
    raise SystemExit(main())
