#!/usr/bin/env python3
"""Record the PDF routing snapshots for the golden corpus.

Deterministic everywhere: ``analyze_pdf`` is pure pypdfium2 parsing, so this
needs no OCR binaries, no language packs, no API key and no network. That is why
routing is what gets snapshotted — see ``tests/test_golden_corpus_routing.py``.

Prints the diff and exits non-zero when anything moved, unless ``--accept`` is
passed. That ordering is deliberate: a snapshot file regenerated without reading
the diff records the regression instead of catching it.

Usage:
    python scripts/record_golden_routing.py                 # show what would change
    python scripts/record_golden_routing.py --accept        # write them
    LIBRARY_GOLDEN_CORPUS_DIR=/path/to/corpus python scripts/record_golden_routing.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from tests.golden_corpus import (  # noqa: E402
    corpus_dir,
    corpus_documents,
    routing_snapshot_path,
)
from tests.test_golden_corpus_routing import routing_decision  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--accept",
        action="store_true",
        help="write the new snapshots (default: report the diff and exit 1)",
    )
    args = parser.parse_args(argv)

    documents = corpus_documents()
    if not documents:
        print(f"error: no corpus documents under {corpus_dir()}", file=sys.stderr)
        return 2

    out = routing_snapshot_path()
    previous = json.loads(out.read_text()) if out.exists() else {}
    current = {path.name: routing_decision(path) for path in documents}

    changes = [
        (name, previous.get(name), current[name])
        for name in current
        if previous.get(name) != current[name]
    ]
    removed = sorted(set(previous) - set(current))

    if not changes and not removed:
        print(f"unchanged: {len(current)} document(s) match the snapshots")
        return 0

    for name, before, after in changes:
        print(f"\n{name}\n  before: {before}\n  after:  {after}")
    for name in removed:
        print(f"\n{name}\n  removed from the corpus")

    if not args.accept:
        print(
            f"\n{len(changes)} changed, {len(removed)} removed. Review the above, "
            "then re-run with --accept.",
            file=sys.stderr,
        )
        return 1

    out.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    print(
        f"\nwrote {out} ({len(current)} documents)\n"
        "This lands in the PRIVATE corpus repo: its keys are the real filenames."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
