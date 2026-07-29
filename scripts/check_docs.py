#!/usr/bin/env python3
"""Documentation freshness gate: every living doc carries a verified stamp.

Structured as a **pure core plus a thin git shell**, so every rule is unit
testable without building a scratch repository: :func:`check_document` takes the
document text and the git facts as *arguments* and returns violations. Only
:func:`git_last_commit_date` and :func:`git_changed_since` touch the repo.

The stamp lives in the first :data:`STAMP_SCAN_LINES` lines and looks like::

    **Status:** active. **Last updated:** 2026-07-17 (what changed).
    **Last verified:** 2026-07-29 — method: read the module and ran the suite.
    **Covers:** src/library/ocr/**, src/library/jobs.py

``Last verified`` is on its **own line** deliberately. `docs/api.md`'s
``Last updated`` line is 1,966 characters — a running changelog — and appending
to it would bury the one field a reader needs to judge whether the document can
be trusted.

Why this is not calendar-based
------------------------------
A fixed window ("re-verify every 90 days") reds accurate docs in a dormant repo
and passes rotten ones in a busy one. Worse, it teaches re-stamping on a
schedule: the cheapest way to clear a due list is to bump the date without
re-checking anything, which manufactures exactly the false verification the
convention exists to prevent. So staleness is driven off **change**:

* the document's own last commit being newer than its ``Last verified`` date
  means it was edited without being re-verified;
* any path in ``Covers:`` having changed since that date means the code moved on
  and nobody re-checked the prose. This is the more valuable signal, and it is
  the reason ``Covers:`` exists — it cannot be derived, so it is declared.

Exit codes: 0 clean, 1 violations found, 2 cannot check (a shallow clone, an
unreadable path). "Cannot check" must never share an exit code with "nothing
wrong".

Usage:
    python scripts/check_docs.py               # the default gated set
    python scripts/check_docs.py docs/api.md   # specific files
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

#: Only the head of a file is scanned: a stamp is a header, and a `**Status:**`
#: appearing halfway down a document is prose, not a stamp.
STAMP_SCAN_LINES: int = 15

#: Living documents, gated. Point-in-time material is excluded **by path**, not
#: by judgement: an ADR, a benchmark, a dated design spec and a journal entry are
#: records of a moment and are supposed to age.
GATED_GLOBS: tuple[str, ...] = ("docs/*.md", "docs/runbooks/*.md")
EXCLUDED_DIRS: frozenset[str] = frozenset({"archive", "adr", "rfc", "benchmarks", "superpowers"})

#: A stamp may say `not yet — <reason>` before its first real verification, but
#: that cannot be permanent or it becomes a legal value forever. Bounded against
#: `Last updated`, so an exemption expires by itself.
NOT_YET_GRACE_DAYS: int = 60

_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(?P<value>[^.\n]*)", re.IGNORECASE)
_VERIFIED_RE = re.compile(r"\*\*Last verified:\*\*\s*(?P<value>[^\n]*)", re.IGNORECASE)
_UPDATED_RE = re.compile(r"\*\*Last updated:\*\*\s*(?P<value>[^\n(]*)", re.IGNORECASE)
_COVERS_RE = re.compile(r"\*\*Covers:\*\*\s*(?P<value>[^\n]*)", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class Violation:
    """One reason a document fails the gate."""

    path: str
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}"


@dataclass(frozen=True)
class Stamp:
    """The parsed stamp header of a document."""

    status: str | None = None
    last_updated: date | None = None
    last_updated_raw: str | None = None
    verified_raw: str | None = None
    verified_date: date | None = None
    method: str | None = None
    covers: tuple[str, ...] = field(default_factory=tuple)


def _parse_iso(value: str) -> date | None:
    """Parse a bare ISO date, or None. Never raises — the caller decides."""
    match = _ISO_DATE_RE.match(value.strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None  # e.g. 2026-13-45


def parse_stamp(text: str) -> Stamp | None:
    """Parse the stamp from a document's head, or None when there is none."""
    head = "\n".join(text.splitlines()[:STAMP_SCAN_LINES])
    status_match = _STATUS_RE.search(head)
    if status_match is None:
        return None

    verified_raw = None
    verified_date = None
    method = None
    if (verified_match := _VERIFIED_RE.search(head)) is not None:
        verified_raw = verified_match.group("value").strip()
        # `<date> — method: <what was run>`; the separator may be an em dash or a
        # plain hyphen, and `method:` is matched case-insensitively.
        parts = re.split(r"\s+[—-]\s+method:\s*", verified_raw, maxsplit=1, flags=re.IGNORECASE)
        verified_date = _parse_iso(parts[0])
        method = parts[1].strip() if len(parts) == 2 else None

    updated_raw = None
    updated_date = None
    if (updated_match := _UPDATED_RE.search(head)) is not None:
        updated_raw = updated_match.group("value").strip()
        updated_date = _parse_iso(updated_raw)

    covers: tuple[str, ...] = ()
    if (covers_match := _COVERS_RE.search(head)) is not None:
        covers = tuple(
            token.strip().strip("`")
            for token in covers_match.group("value").split(",")
            if token.strip().strip("`")
        )

    return Stamp(
        status=status_match.group("value").strip().lower() or None,
        last_updated=updated_date,
        last_updated_raw=updated_raw,
        verified_raw=verified_raw,
        verified_date=verified_date,
        method=method,
        covers=covers,
    )


def check_document(
    path: str,
    text: str,
    *,
    last_commit: date | None,
    covered_changes: tuple[str, ...] = (),
    today: date,
) -> list[Violation]:
    """Every rule, as a pure function of the text and the git facts.

    ``last_commit`` is the document's own last commit date (None when untracked).
    ``covered_changes`` are paths from ``Covers:`` that changed since the stamp.
    """
    violations: list[Violation] = []
    stamp = parse_stamp(text)

    if stamp is None:
        return [Violation(path, "no-stamp", "no `**Status:**` line in the first 15 lines")]

    if last_commit is None:
        violations.append(
            Violation(path, "untracked", "not tracked by git, so staleness cannot be judged")
        )

    if stamp.status != "active":
        # Superseded/historical documents are not expected to stay verified —
        # that is the whole point of archiving them.
        return violations

    # --- Last verified -------------------------------------------------------
    if stamp.verified_raw is None:
        violations.append(
            Violation(
                path,
                "missing-verified",
                "status is `active` but there is no `**Last verified:**` line",
            )
        )
        return violations

    if stamp.verified_raw.lower().startswith("not yet"):
        reason = stamp.verified_raw[len("not yet") :].strip(" —-:")
        if not reason:
            violations.append(Violation(path, "not-yet-no-reason", "`not yet` must give a reason"))
        elif stamp.last_updated is not None:
            age = (today - stamp.last_updated).days
            if age > NOT_YET_GRACE_DAYS:
                violations.append(
                    Violation(
                        path,
                        "not-yet-expired",
                        f"`not yet` has stood for {age} days (limit {NOT_YET_GRACE_DAYS}) — "
                        "verify it or archive the document",
                    )
                )
        return violations

    if stamp.verified_date is None:
        # Unparseable is red, never skipped: `banana`, `2026-13-45` and a missing
        # date must all fail. Absence of a parse is not a pass.
        violations.append(
            Violation(
                path,
                "unparseable-date",
                f"`Last verified` is not an ISO date: {stamp.verified_raw!r}",
            )
        )
        return violations

    if stamp.verified_date > today:
        violations.append(
            Violation(
                path,
                "future-date",
                f"`Last verified` is in the future ({stamp.verified_date})",
            )
        )

    if not stamp.method:
        violations.append(
            Violation(
                path,
                "missing-method",
                "`Last verified` has no `— method: <what was run>`; the method is "
                "what makes the claim checkable",
            )
        )

    # --- Comparative staleness ----------------------------------------------
    if last_commit is not None and last_commit > stamp.verified_date:
        violations.append(
            Violation(
                path,
                "stale-doc-edit",
                f"edited {last_commit} but last verified {stamp.verified_date} — "
                "re-verify and re-stamp, or move the prose that changed",
            )
        )

    if covered_changes:
        shown = ", ".join(sorted(covered_changes)[:4])
        violations.append(
            Violation(
                path,
                "stale-covered-code",
                f"code it declares it covers changed since {stamp.verified_date} "
                f"({shown}) — re-check the prose against it",
            )
        )

    return violations


# --- The git shell -----------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    ).stdout.strip()


def is_shallow_clone() -> bool:
    """True when history is truncated, which silently breaks every date check.

    ``actions/checkout`` defaults to ``fetch-depth: 1``. Under it every file's
    ``git log -1`` returns HEAD's date, so every document looks freshly touched
    and the gate passes everything forever. Detect it and refuse to run.
    """
    return _git("rev-parse", "--is-shallow-repository") == "true"


def git_last_commit_date(path: str) -> date | None:
    """The date of the last commit touching ``path``, or None if untracked."""
    out = _git("log", "-1", "--format=%ad", "--date=short", "--", path)
    return _parse_iso(out) if out else None


def git_changed_since(since: date, patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Which of ``patterns`` have commits after ``since``."""
    changed: list[str] = []
    for pattern in patterns:
        out = _git("log", f"--since={since.isoformat()}", "--format=%H", "--", pattern)
        if out:
            changed.append(pattern)
    return tuple(changed)


def gated_documents() -> list[Path]:
    """The living documents this gate covers, in a stable order."""
    found: list[Path] = []
    for pattern in GATED_GLOBS:
        for candidate in REPO_ROOT.glob(pattern):
            if EXCLUDED_DIRS & set(candidate.parts):
                continue
            found.append(candidate)
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="documents to check")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="override today's date (tests; keeps output deterministic)",
    )
    args = parser.parse_args(argv)

    if is_shallow_clone():
        print(
            "error: this is a shallow clone, so `git log` dates are meaningless — "
            "every document would look freshly touched and the gate would pass "
            "everything. Use `fetch-depth: 0`.",
            file=sys.stderr,
        )
        return 2

    documents = args.paths or gated_documents()
    if not documents:
        print("error: no documents to check", file=sys.stderr)
        return 2

    today = args.today or date.today()
    violations: list[Violation] = []
    for document in documents:
        try:
            text = document.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {document}: {exc}", file=sys.stderr)
            return 2
        relative = str(document.relative_to(REPO_ROOT)) if document.is_absolute() else str(document)
        last_commit = git_last_commit_date(relative)
        stamp = parse_stamp(text)
        covered: tuple[str, ...] = ()
        if stamp is not None and stamp.covers and stamp.verified_date is not None:
            covered = git_changed_since(stamp.verified_date, stamp.covers)
        violations.extend(
            check_document(
                relative, text, last_commit=last_commit, covered_changes=covered, today=today
            )
        )

    if not violations:
        print(f"ok: {len(documents)} document(s) carry a current, verified stamp")
        return 0

    by_rule: dict[str, int] = {}
    for violation in sorted(violations, key=lambda v: (v.path, v.rule)):
        print(violation.render(), file=sys.stderr)
        by_rule[violation.rule] = by_rule.get(violation.rule, 0) + 1
    summary = ", ".join(f"{rule}={count}" for rule, count in sorted(by_rule.items()))
    print(
        f"\n{len(violations)} violation(s) across {len(documents)} document(s): {summary}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
