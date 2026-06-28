#!/usr/bin/env python3
"""Merge backend + frontend coverage reports into one JSON summary.

CI generates this file before `docker build` and bakes it into the image at the
default `coverage-summary.json` path that the backend reads at runtime (see
`library.config.Settings.coverage_summary_path`). The admin coverage view
surfaces the numbers; absent file → the view reports "unavailable".

Both sources are optional: a missing file leaves that side's `pct` null rather
than failing, so a partial summary is still produced (exit 0). The timestamp is
passed in by the caller (`--generated-at`) so the output stays deterministic and
the build_summary() function is trivially testable.

Usage:
    python scripts/coverage_summary.py \
        --backend-json coverage.json \
        --frontend-json frontend/coverage/coverage-summary.json \
        --git-sha "$GITHUB_SHA" \
        --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --output coverage-summary.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

THRESHOLD = 85.0


def _read_json(path: Path | None) -> dict | None:
    """Return the parsed JSON at `path`, or None if it is unset/missing."""
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _backend_pct(path: Path | None) -> float | None:
    """Extract `totals.percent_covered` (1 dp) from a `coverage json` report."""
    data = _read_json(path)
    if data is None:
        return None
    return round(float(data["totals"]["percent_covered"]), 1)


def _frontend_pct(path: Path | None) -> float | None:
    """Extract `total.lines.pct` from an istanbul json-summary report."""
    data = _read_json(path)
    if data is None:
        return None
    return float(data["total"]["lines"]["pct"])


def build_summary(
    *,
    backend_json: Path | None,
    frontend_json: Path | None,
    generated_at: str | None,
    git_sha: str | None,
) -> dict:
    """Build the unified coverage summary dict (pure: no clock, no IO beyond reads)."""
    return {
        "backend": {"pct": _backend_pct(backend_json), "threshold": THRESHOLD},
        "frontend": {"pct": _frontend_pct(frontend_json), "threshold": THRESHOLD},
        "generated_at": generated_at,
        "git_sha": git_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-json", type=Path, default=None)
    parser.add_argument("--frontend-json", type=Path, default=None)
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    summary = build_summary(
        backend_json=args.backend_json,
        frontend_json=args.frontend_json,
        generated_at=args.generated_at,
        git_sha=args.git_sha,
    )
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
