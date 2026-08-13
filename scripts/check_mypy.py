"""Two-directional ratchet over the mypy quarantine in `pyproject.toml`.

`uv run mypy` is green by construction: the `[[tool.mypy.overrides]]` blocks
with `disable_error_code` suppress the errors four modules still carry. That
makes the ordinary type-check job blind in one direction — as `pyproject.toml`
says of itself, "if a module is cleaned up the override lingers silently
instead of failing to force the gain to be locked in".

This script measures what those overrides actually suppress. It regenerates the
mypy configuration *from* `pyproject.toml` with the quarantine lifted, runs
mypy against it, counts errors per (module, error-code), and compares the
result to a checked-in baseline. Both directions fail, and so does an override
that has stopped suppressing anything — the dead-entry case that motivated the
unit, since such an entry disables a whole error class for a module while
appearing to cost nothing.

The generated config is derived rather than restated: a settings change in
`pyproject.toml` reaches the measurement run with no edit here. The one thing
deliberately kept is the `ignore_missing_imports` block — untyped third-party
packages are someone else's packaging, not errors this repo is quarantining.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILENAME = "mypy-baseline.json"

# `src/library/ask/engine.py:841: error: ... has no attribute "name"  [union-attr]`
_ERROR_LINE = re.compile(r"^(?P<path>\S+?\.py):\d+: error: .*\[(?P<code>[a-z-]+)\]\s*$")


def _module_of(path: str) -> str:
    """`src/library/ask/engine.py` -> `library.ask.engine`.

    The dotted form is what `module = ...` in a `[[tool.mypy.overrides]]` block
    uses, and the counts are keyed by it so the two can be compared directly.
    """
    parts = Path(path).with_suffix("").parts
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def parse_counts(output: str) -> dict[str, dict[str, int]]:
    """Count mypy errors per module and error code.

    Only `error:` lines are counted. A `note:` is commentary attached to an
    error already counted, and mypy's trailing summary line would otherwise
    parse as an error in a file named `Found`.
    """
    counts: dict[str, dict[str, int]] = {}
    for line in output.splitlines():
        match = _ERROR_LINE.match(line.strip())
        if match is None:
            continue
        module = _module_of(match.group("path"))
        counts.setdefault(module, {})
        code = match.group("code")
        counts[module][code] = counts[module].get(code, 0) + 1
    return counts


def quarantined_codes(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    """The `(module, codes)` pairs the quarantine currently suppresses.

    Read from `pyproject.toml` rather than from the baseline so the baseline can
    be checked *against* the real overrides — a quarantine entry with no
    baseline number would otherwise be an unmeasured suppression.
    """
    overrides = pyproject.get("tool", {}).get("mypy", {}).get("overrides", [])
    quarantine: dict[str, list[str]] = {}
    for override in overrides:
        codes = override.get("disable_error_code")
        if not codes:
            continue
        modules = override["module"]
        for module in [modules] if isinstance(modules, str) else modules:
            quarantine.setdefault(module, []).extend(codes)
    return quarantine


def baseline_gaps(pyproject: dict[str, Any], baseline: dict[str, dict[str, int]]) -> list[str]:
    """Where the quarantine and the baseline disagree about what is covered.

    Checked in both directions. A quarantine entry with no number is an
    unmeasured suppression; a number for a code nothing quarantines makes
    `ratchet_verdicts` report that the module "disables" a code pyproject never
    mentions, which sends the reader to the wrong file.
    """
    quarantine = quarantined_codes(pyproject)
    gaps: list[str] = []
    for module, codes in sorted(quarantine.items()):
        for code in sorted(codes):
            if code not in baseline.get(module, {}):
                gaps.append(
                    f"FAIL: {module} disables [{code}] but {BASELINE_FILENAME} has no "
                    "count for it. Add the measured number so the ratchet guards it."
                )
    for module, codes in sorted(baseline.items()):
        for code in sorted(codes):
            if code not in quarantine.get(module, []):
                gaps.append(
                    f"FAIL: {BASELINE_FILENAME} has a count for {module} [{code}] but "
                    "pyproject.toml no longer disables it. Delete the stale entry."
                )
    return gaps


def ratchet_verdicts(
    counts: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> tuple[int, list[str]]:
    """Compare measured suppression against the baseline. Pure, so the tests
    need neither a mypy run nor a checkout.

    Iterates the *baseline*, not the counts: an error in a module nothing
    suppresses already reds the ordinary mypy run, and counting it here would
    couple the two gates and double-report the same problem.
    """
    messages: list[str] = []
    for module, codes in sorted(baseline.items()):
        for code, expected in sorted(codes.items()):
            actual = counts.get(module, {}).get(code, 0)
            if actual == 0:
                messages.append(
                    f"FAIL: {module} disables [{code}] but it no longer suppresses "
                    f"anything (baseline {expected}). Delete the code from the "
                    "override in pyproject.toml — leaving it there silently "
                    "disables that check for the whole module."
                )
            elif actual > expected:
                messages.append(
                    f"FAIL: {module} [{code}] rose to {actual} from the baseline of "
                    f"{expected}. Fix the new error rather than raising the baseline."
                )
            elif actual < expected:
                messages.append(
                    f"FAIL: {module} [{code}] is {actual}, BELOW the baseline of "
                    f"{expected}. Types improved — lower the number in "
                    f"{BASELINE_FILENAME} to lock the gain in."
                )
    return (1 if messages else 0), messages


def _ini_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def no_quarantine_config(pyproject: dict[str, Any]) -> str:
    """Render `[tool.mypy]` as a mypy INI config with the quarantine lifted.

    Lifting it *is* the measurement: if `disable_error_code` survived into this
    config every count would read zero and the gate would pass while suppressing
    everything it exists to watch.
    """
    mypy = pyproject.get("tool", {}).get("mypy", {})
    lines = ["[mypy]"]
    for key, value in mypy.items():
        if key == "overrides":
            continue
        lines.append(f"{key} = {_ini_value(value)}")

    for override in mypy.get("overrides", []):
        if "disable_error_code" in override:
            continue
        modules = override["module"]
        section = modules if isinstance(modules, str) else ",".join(modules)
        lines.append("")
        lines.append(f"[mypy-{section}]")
        for key, value in override.items():
            if key == "module":
                continue
            lines.append(f"{key} = {_ini_value(value)}")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    with path.open() as handle:
        loaded: dict[str, dict[str, int]] = json.load(handle)
    return loaded


def require_real_run(returncode: int, stderr: str) -> None:
    """Refuse to draw conclusions from a mypy invocation that did not check.

    0 (clean) and 1 (errors found) are both real measurements — with the
    quarantine lifted, 1 is the expected one. Anything else means mypy never
    type-checked: a bad config, a missing interpreter, an internal error. Its
    stdout is then empty, every count reads zero, and `ratchet_verdicts` would
    report that every override is dead and should be deleted. A gate that turns
    its own breakage into confident advice is worse than one that fails, so this
    stops the run instead.
    """
    if returncode not in (0, 1):
        raise RuntimeError(
            f"mypy exited with exit code {returncode} without type checking; "
            f"refusing to measure. stderr:\n{stderr.strip()}"
        )


def run_mypy(config: str) -> str:
    """Run mypy against a generated config and return its stdout.

    The config goes to a temp file rather than mutating `pyproject.toml`, so a
    cancelled run cannot leave the repo's real settings lifted.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as handle:
        handle.write(config)
        config_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "mypy", "--config-file", str(config_path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        require_real_run(completed.returncode, completed.stderr)
        return completed.stdout
    finally:
        config_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--show-counts",
        action="store_true",
        help="print the measured per-module counts even when the gate passes",
    )
    args = parser.parse_args(argv)

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    baseline = load_baseline(REPO_ROOT / BASELINE_FILENAME)

    gaps = baseline_gaps(pyproject, baseline)
    if gaps:
        for gap in gaps:
            print(gap, file=sys.stderr)
        return 1

    counts = parse_counts(run_mypy(no_quarantine_config(pyproject)))
    code, messages = ratchet_verdicts(counts, baseline)

    if args.show_counts or messages:
        for module, codes in sorted(counts.items()):
            rendered = ", ".join(f"{c}={n}" for c, n in sorted(codes.items()))
            print(f"{module}: {rendered}", file=sys.stderr)

    for message in messages:
        print(message, file=sys.stderr)
    if code == 0:
        total = sum(n for codes in baseline.values() for n in codes.values())
        print(f"ok: quarantine suppresses exactly {total} error(s), matching the baseline")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
