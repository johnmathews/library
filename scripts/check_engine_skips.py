#!/usr/bin/env python3
"""Fail when a real-engine OCR test skipped instead of running.

The `slow_ocr` tests are the only coverage the OCR pipeline has over the actual
binaries and models, and both guards in `tests/test_ocr_real.py` skip rather
than fail when their engine is genuinely unavailable — correct for a laptop with
no tesseract installed, and a hole in CI, where the whole point of the job is
that those engines ran. A skip is reported as success by pytest, so a
permanently broken engine reads as a green build for as long as nobody looks at
the skip list.

This closes that: CI installs the tesseract stack and has network for the
RapidOCR model hub, so in CI neither guard has any legitimate reason to fire.
Any skip whose reason mentions a required engine is an error here.

Matching is on the skip *reason*, not on the test id, so it keeps working when
tests are renamed or moved, and it catches a new test that borrows the same
guard. `--engine` is repeatable; the default pair is what the backend job
installs.

Usage:
    python scripts/check_engine_skips.py pytest-report.xml
    python scripts/check_engine_skips.py pytest-report.xml --engine rapidocr
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

# Engines the CI backend job provisions, and therefore must actually exercise.
# tesseract/gs/unpaper come from the apt step; rapidocr downloads its weights.
DEFAULT_REQUIRED_ENGINES = ("rapidocr", "tesseract")


class ReportError(Exception):
    """The report is missing or unparseable — never silently a pass."""


def find_offending_skips(report: Path, engines: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return (test id, skip reason) for every skip naming a required engine.

    A report that does not exist, or that does not parse, raises rather than
    returning an empty list. "No skips found" and "I could not look" are
    opposite outcomes and must not share an exit code — a scanner that matches
    nothing passes loudest when it is blind.
    """
    if not report.exists():
        raise ReportError(
            f"{report} does not exist — pytest must run with "
            f"--junitxml={report.name} for this gate to mean anything"
        )
    raw = report.read_bytes()
    # A pytest JUnit report never carries a DOCTYPE, so refusing one costs
    # nothing and removes entity expansion (the "billion laughs" class) as a
    # possibility rather than reasoning about whether it is reachable. stdlib
    # ElementTree already rejects *external* entity references, which is the
    # file-disclosure half; this covers the internal-expansion half. Escaped
    # text cannot trip it: a literal `<!DOCTYPE` in the bytes can only be a
    # real declaration, since a skip reason containing it would be `&lt;`.
    if b"<!DOCTYPE" in raw:
        raise ReportError(f"{report} contains a DOCTYPE declaration; refusing to parse it")
    try:
        tree = ElementTree.ElementTree(ElementTree.fromstring(raw))
    except ElementTree.ParseError as exc:
        raise ReportError(f"{report} is not parseable JUnit XML: {exc}") from exc

    patterns = {engine: re.compile(re.escape(engine), re.IGNORECASE) for engine in engines}
    offenders: list[tuple[str, str]] = []
    for case in tree.iter("testcase"):
        for skipped in case.iter("skipped"):
            # pytest puts the reason in @message; the element text repeats it
            # with the source location prepended. Check both so a pytest change
            # to either one cannot quietly empty the haystack.
            reason = " ".join(filter(None, (skipped.get("message"), skipped.text)))
            if any(pattern.search(reason) for pattern in patterns.values()):
                test_id = f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")
                offenders.append((test_id, (skipped.get("message") or reason).strip()))
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="pytest JUnit XML report")
    parser.add_argument(
        "--engine",
        action="append",
        dest="engines",
        metavar="NAME",
        help=(
            "engine name that must not appear in a skip reason (repeatable; "
            f"default: {', '.join(DEFAULT_REQUIRED_ENGINES)})"
        ),
    )
    args = parser.parse_args(argv)
    engines = tuple(args.engines) if args.engines else DEFAULT_REQUIRED_ENGINES

    try:
        offenders = find_offending_skips(args.report, engines)
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if offenders:
        print(
            "error: real-engine OCR tests skipped in an environment that provisions "
            f"them ({', '.join(engines)}):",
            file=sys.stderr,
        )
        for test_id, reason in offenders:
            print(f"  - {test_id}: {reason}", file=sys.stderr)
        print(
            "\nThese tests are the only coverage over the real OCR binaries and "
            "models. A skip here is a broken engine, not a passing build — fix the "
            "engine, or fix the provisioning step that was meant to supply it.",
            file=sys.stderr,
        )
        return 1

    print(f"ok: no {'/'.join(engines)} test skipped for an engine-availability reason")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
