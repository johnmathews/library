#!/usr/bin/env python3
"""Generate `journal/README.md` — an index of the development journal.

The journal is the project's memory — well over a hundred dated entries
recording why things were done the way they were. Without an index it is
reachable only by guessing at filenames, so in practice nobody reads anything
but the newest few. (No count here on purpose: it would be one more number to
drift. The generated index carries the real one.)

Generated, not hand-written, and gated the same way `ruff format` is: `--check`
regenerates in memory and exits 1 if the file on disk differs. That contract is
the point — an index that can be edited by hand drifts from the entries it
indexes, and a stale index is worse than none, because it looks authoritative.

**Cadence is deliberately not gated.** "No entry in N days" would be a calendar
gate wearing a different hat: it reds on legitimately quiet weeks and teaches
writing an entry to clear a check. Writing one per PR is a convention, enforced
by review.

Usage:
    python scripts/build_journal_index.py            # write journal/README.md
    python scripts/build_journal_index.py --check    # exit 1 if out of date
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
JOURNAL_DIR: Path = REPO_ROOT / "journal"
INDEX_PATH: Path = JOURNAL_DIR / "README.md"

#: `260610-project-inception.md` — the naming convention for every entry. Dots
#: are allowed in the slug: `260611-v0.1.0-build-complete.md` is a real entry and
#: a version number is a perfectly good descriptive name.
_ENTRY_NAME_RE = re.compile(r"^(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})-(?P<slug>[\w.-]+)\.md$")

_H1_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


@dataclass(frozen=True)
class Entry:
    """One journal entry, as the index needs it."""

    filename: str
    day: date
    title: str


class JournalError(Exception):
    """An entry the index cannot describe. Never silently skipped.

    Skipping a malformed entry would produce an index that looks complete while
    quietly omitting things — the failure mode this repo keeps rediscovering.
    """


def parse_entry(filename: str, text: str) -> Entry:
    """One entry's index row, from its filename and body. Pure."""
    match = _ENTRY_NAME_RE.match(filename)
    if match is None:
        raise JournalError(f"{filename}: does not follow the yymmdd-descriptive-name.md convention")
    try:
        day = date(2000 + int(match["yy"]), int(match["mm"]), int(match["dd"]))
    except ValueError as exc:
        raise JournalError(f"{filename}: not a real date ({exc})") from exc

    title_match = _H1_RE.search(text)
    if title_match is None:
        raise JournalError(f"{filename}: no H1 heading to take a title from")
    return Entry(filename=filename, day=day, title=title_match["title"])


def render_index(entries: list[Entry]) -> str:
    """The full `journal/README.md`, grouped by month, newest first. Pure."""
    ordered = sorted(entries, key=lambda e: (e.day, e.filename), reverse=True)
    lines = [
        "# Development journal",
        "",
        "> Purpose",
        ">",
        "> Dated entries recording decisions, progress and context — why things are",
        "> the way they are. Newest first.",
        "",
        f"{len(ordered)} entries.",
        "",
        "**This file is generated** by `scripts/build_journal_index.py` from each",
        "entry's H1. Do not edit it by hand — add your entry and re-run the script.",
        "CI checks it is current.",
        "",
    ]
    current_month: tuple[int, int] | None = None
    for entry in ordered:
        month = (entry.day.year, entry.day.month)
        if month != current_month:
            if current_month is not None:
                lines.append("")
            lines.append(f"## {_MONTHS[entry.day.month - 1]} {entry.day.year}")
            lines.append("")
            current_month = month
        lines.append(f"- **{entry.day.isoformat()}** — [{entry.title}]({entry.filename})")
    lines.append("")
    return "\n".join(lines)


def collect_entries(journal_dir: Path) -> list[Entry]:
    """Read every entry. The only part of this script that touches the disk."""
    entries: list[Entry] = []
    for path in sorted(journal_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        entries.append(parse_entry(path.name, path.read_text(encoding="utf-8")))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if journal/README.md is not what would be written",
    )
    args = parser.parse_args(argv)

    try:
        entries = collect_entries(JOURNAL_DIR)
    except (JournalError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not entries:
        print(f"error: no journal entries found in {JOURNAL_DIR}", file=sys.stderr)
        return 2

    rendered = render_index(entries)

    if not args.check:
        INDEX_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({len(entries)} entries)")
        return 0

    current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else None
    if current == rendered:
        print(f"ok: journal index is current ({len(entries)} entries)")
        return 0
    print(
        "error: journal/README.md is out of date — run "
        "`python scripts/build_journal_index.py` and commit the result.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
