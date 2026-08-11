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

#: The archived greenfield plan whose `units:` frontmatter the `Wn` citations in
#: `docs/` refer to. Restored from history in W14 — before that the reference in
#: `architecture.md` pointed at a path that no longer existed, so no `Wn` token
#: in the documentation could be resolved by a reader at all.
WORK_UNIT_PLAN: str = "docs/archive/260610-greenfield-build-plan.md"

#: Where the model defaults live. Parsed **textually**, not imported: this script
#: is pure stdlib and runs in CI without the application's dependencies, and
#: importing `library.config` would drag pydantic into the docs gate.
CONFIG_SOURCE: str = "src/library/config.py"

_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(?P<value>[^.\n]*)", re.IGNORECASE)
_VERIFIED_RE = re.compile(r"\*\*Last verified:\*\*\s*(?P<value>[^\n]*)", re.IGNORECASE)
_UPDATED_RE = re.compile(r"\*\*Last updated:\*\*\s*(?P<value>[^\n(]*)", re.IGNORECASE)
_COVERS_RE = re.compile(r"\*\*Covers:\*\*\s*(?P<value>[^\n]*)", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")

#: A work-unit citation. `\b` on both sides so `W3C` is not read as `W3`.
_WORK_UNIT_RE = re.compile(r"\bW(\d{1,2})\b")

#: `  - id: W7` in the archived plan's YAML frontmatter.
_PLAN_UNIT_RE = re.compile(r"^\s*-\s*id:\s*W(\d{1,2})\s*$", re.MULTILINE)

#: `_PRICED_MODEL_FIELDS: tuple[str, ...] = (...)` and the `"field",` lines in it.
_PRICED_BLOCK_RE = re.compile(r"_PRICED_MODEL_FIELDS.*?=\s*\((?P<body>.*?)\)", re.DOTALL)
_PRICED_NAME_RE = re.compile(r'"(\w+)"')

#: `    ask_model: str = "claude-opus-4-8"` — the default for a settings field.
_MODEL_DEFAULT_RE = re.compile(
    r'^\s*(?P<field>\w+):\s*str\s*=\s*"(?P<model>claude-[\w.-]+)"', re.MULTILINE
)


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


# --- Work-unit citations (W14) ------------------------------------------------


def parse_plan_units(plan_text: str) -> frozenset[int]:
    """The unit numbers declared in the archived plan's YAML frontmatter."""
    return frozenset(int(match) for match in _PLAN_UNIT_RE.findall(plan_text))


def check_work_unit_citations(path: str, text: str, known_units: frozenset[int]) -> list[Violation]:
    """Every ``Wn`` cited in a gated doc must exist in the archived plan.

    The failure this prevents is a dangling reference: `architecture.md` pointed
    at a decision record that had been untracked, so a reader hitting "(W6)" had
    nowhere to resolve it. Restoring the plan makes the tokens meaningful; this
    rule keeps them meaningful.

    **What it cannot catch**, deliberately stated so nobody trusts it further
    than it goes: a citation of the *right-shaped but wrong* unit. Three such
    citations existed — `(W11)` in `api.md`, `(W11)` and `(W9)` in `ask.md` —
    which named units from a *different* run's numbering. All three tokens are
    present in this plan, so membership cannot distinguish them; they were found
    by reading and deleted in W14. This rule enforces membership, not intent.
    """
    if not known_units:
        # An empty unit list means the plan moved or its frontmatter changed
        # shape. Passing every citation on an empty set would be a check that
        # cannot fail — the exact defect this module exists to avoid.
        return [
            Violation(
                path,
                "plan-unreadable",
                f"no units parsed from {WORK_UNIT_PLAN}; the citation rule cannot run",
            )
        ]
    violations: list[Violation] = []
    for number in sorted({int(match) for match in _WORK_UNIT_RE.findall(text)}):
        if number not in known_units:
            violations.append(
                Violation(
                    path,
                    "unknown-work-unit",
                    f"cites `W{number}`, which is not a unit in {WORK_UNIT_PLAN} "
                    f"(it declares W1-W{max(known_units)}) — the reference does not resolve",
                )
            )
    return violations


# --- Model identity (W13) -----------------------------------------------------


def parse_model_defaults(config_text: str) -> dict[str, str]:
    """The priced settings fields mapped to their default model ids.

    Only the fields listed in ``_PRICED_MODEL_FIELDS`` are returned: those are
    the ones a reader of the docs might quote, and the ones already required to
    carry a pricing row.
    """
    block = _PRICED_BLOCK_RE.search(config_text)
    if block is None:
        return {}
    priced = set(_PRICED_NAME_RE.findall(block.group("body")))
    return {
        match.group("field"): match.group("model")
        for match in _MODEL_DEFAULT_RE.finditer(config_text)
        if match.group("field") in priced
    }


def check_model_identity(path: str, text: str, defaults: dict[str, str]) -> list[Violation]:
    """A model id quoted next to its settings field must be that field's default.

    `api.md` claimed ``ask_model`` was `claude-sonnet-4-6` while it had been
    `claude-opus-4-8` for weeks — a 67% understatement of the cost of the
    feature the page documents. Prose drifts from config silently because
    nothing links them; this is that link.

    Deliberately narrow: it fires only when the field and the model are joined
    by ``=``, ``default``, ``:`` or immediate parentheses. Prose that mentions a
    *different* model near a field — "escalates from `extraction_model` to
    `claude-sonnet-4-6`" — is describing a relationship, not asserting a
    default, and must not red the gate. A rule that cried wolf on that phrasing
    would be turned off, and then it would catch nothing at all.
    """
    violations: list[Violation] = []
    for name, expected in sorted(defaults.items()):
        pattern = re.compile(
            rf"`?\b{re.escape(name)}\b`?\s*(?:=|:|,?\s*default|\()\s*`?(claude-[\w.-]+)`?"
        )
        for found in pattern.findall(text):
            if found != expected:
                violations.append(
                    Violation(
                        path,
                        "stale-model-id",
                        f"says `{name}` is `{found}`; {CONFIG_SOURCE} sets `{expected}`",
                    )
                )
    return violations


# --- The git shell -----------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    ).stdout.strip()


def interpret_shallow(rev_parse_output: str, shallow_marker_exists: bool) -> bool:
    """Decide shallowness from git's answer plus the ``.git/shallow`` marker.

    Pure, so it is testable without cloning: the ambient repo's depth is a
    property of the *checkout*, not of this code, and a test that asserts it
    passes on a full clone and fails in any job using the default
    ``fetch-depth: 1`` — which is how the first version of this broke CI.

    The marker is checked as well as the command because
    ``rev-parse --is-shallow-repository`` needs git >= 2.15 and prints nothing
    when unsupported. Treating "no answer" as "not shallow" would fail open on
    exactly the guard that stops the whole gate going blind.
    """
    if rev_parse_output.strip() == "true":
        return True
    return shallow_marker_exists


def is_shallow_clone() -> bool:
    """True when history is truncated, which silently breaks every date check.

    ``actions/checkout`` defaults to ``fetch-depth: 1``. Under it every file's
    ``git log -1`` returns HEAD's date, so every document looks freshly touched
    and the gate passes everything forever. Detect it and refuse to run.
    """
    git_dir = _git("rev-parse", "--git-dir")
    marker = (Path(git_dir) if git_dir else REPO_ROOT / ".git") / "shallow"
    return interpret_shallow(_git("rev-parse", "--is-shallow-repository"), marker.exists())


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


def _repo_relative(path: Path) -> str:
    """``path`` relative to the repo when it is inside it, else as given.

    ``Path.relative_to`` raises for a path outside the repo, which turned an
    explicit argument pointing anywhere else — a scratch file, a tmpdir in a test
    — into a traceback rather than a usable message. The git lookups simply
    return nothing for such a path, which the `untracked` rule already reports.
    """
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def ratchet_verdict(count: int, baseline: int) -> tuple[int, str]:
    """Decide the ratchet outcome. Pure, so tests need no repo and no checkout.

    Extracted because ``main`` runs the shallow-clone guard first and returns 2
    in a shallow checkout — which is correct behaviour and makes any test that
    drives ``main`` depend on the ambient ``fetch-depth``. That is exactly the
    mistake this module already made once with ``is_shallow_clone``.

    Both directions fail. Rising is the obvious one. Falling matters just as
    much: slack left in the baseline is where the next regression hides, so a
    gain has to be locked in by lowering the number.
    """
    if count > baseline:
        return 1, (
            f"FAIL: {count} violation(s) exceeds the baseline of {baseline}. "
            "Documentation got worse — stamp the doc you touched rather than "
            "raising the baseline."
        )
    if count < baseline:
        return 1, (
            f"FAIL: {count} violation(s) is BELOW the baseline of {baseline}. "
            "Documentation improved — lower the baseline in "
            ".github/workflows/ci.yml to lock the gain in."
        )
    return 0, (
        f"ok: {count} violation(s), exactly the baseline. No regression, but these "
        "are real — W27 is the sweep that clears them."
    )


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
        "--max-violations",
        type=int,
        default=0,
        help=(
            "tolerate up to N violations (a ratchet baseline). Exceeding it fails; "
            "coming in UNDER it also fails, so the baseline cannot silently rot "
            "upward once docs improve. Default 0."
        ),
    )
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

    # Read the two cross-referenced sources once. Both are "cannot check"
    # conditions rather than violations if they are missing outright: a rule
    # that silently passes because its reference disappeared is the failure this
    # module is built against.
    try:
        plan_units = parse_plan_units((REPO_ROOT / WORK_UNIT_PLAN).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read {WORK_UNIT_PLAN}: {exc}", file=sys.stderr)
        return 2
    try:
        config_text = (REPO_ROOT / CONFIG_SOURCE).read_text(encoding="utf-8")
        model_defaults = parse_model_defaults(config_text)
    except OSError as exc:
        print(f"error: cannot read {CONFIG_SOURCE}: {exc}", file=sys.stderr)
        return 2
    if not model_defaults:
        print(
            f"error: no priced model defaults parsed from {CONFIG_SOURCE}; the "
            "model-identity rule cannot run",
            file=sys.stderr,
        )
        return 2

    violations: list[Violation] = []
    for document in documents:
        try:
            text = document.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {document}: {exc}", file=sys.stderr)
            return 2
        relative = _repo_relative(document)
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
        violations.extend(check_work_unit_citations(relative, text, plan_units))
        violations.extend(check_model_identity(relative, text, model_defaults))

    if not violations and args.max_violations == 0:
        print(f"ok: {len(documents)} document(s) carry a current, verified stamp")
        return 0

    by_rule: dict[str, int] = {}
    for violation in sorted(violations, key=lambda v: (v.path, v.rule)):
        print(violation.render(), file=sys.stderr)
        by_rule[violation.rule] = by_rule.get(violation.rule, 0) + 1
    summary = ", ".join(f"{rule}={count}" for rule, count in sorted(by_rule.items()))
    count = len(violations)
    print(
        f"\n{count} violation(s) across {len(documents)} document(s): {summary}",
        file=sys.stderr,
    )

    # Ratchet. A baseline exists because the gate legitimately reds today's tree
    # and W27 is the sweep that clears it — but `continue-on-error` would make
    # this a permanently red check, which trains everyone to ignore it. With a
    # baseline it is a real gate from its first run: docs cannot get worse.
    if args.max_violations:
        code, message = ratchet_verdict(count, args.max_violations)
        print(message, file=sys.stderr)
        return code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
