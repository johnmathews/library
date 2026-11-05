"""Structural checks on .github/workflows/ci.yml.

`tests/test_ci_gate.py` tests what `scripts/ci_gate.sh` does with the results it
is given. This file tests what the workflow actually gives it, and the path
filters that decide which jobs run. Neither can be seen in a unit test of the
script, and a mistake in either is silent: a job listed in `needs:` but not in
the gate's arguments is waited for and then ignored, and a filter that puts a
path in the wrong bucket skips (or runs) e2e with a green gate either way.

Path matching uses `PurePosixPath.full_match` (Python 3.13). dorny/paths-filter
uses picomatch; for the pattern shapes in this file (literal names and a
trailing `/**`) the two agree.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

# `"name=${{ needs.<job>.result }}"` arguments in ci-gate's run block.
_RESULT_ARG = re.compile(r'"([\w-]+)=\$\{\{\s*needs\.([\w-]+)\.result\s*\}\}"')


def _workflow() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text())
    return loaded


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = _workflow()["jobs"]
    return jobs


def _gate_run() -> str:
    steps = _jobs()["ci-gate"]["steps"]
    runs = [step["run"] for step in steps if "ci_gate.sh" in step.get("run", "")]
    assert len(runs) == 1, f"expected one ci_gate.sh step in ci-gate, found {len(runs)}"
    run: str = runs[0]
    return run


def _filters() -> dict[str, list[str]]:
    """The paths-filter `filters:` block — a YAML document inside a string."""
    steps = _jobs()["changes"]["steps"]
    blocks = [
        step["with"]["filters"]
        for step in steps
        if str(step.get("uses", "")).startswith("dorny/paths-filter@")
    ]
    assert len(blocks) == 1, f"expected one paths-filter step in changes, found {len(blocks)}"
    filters: dict[str, list[str]] = yaml.safe_load(blocks[0])
    return filters


def _squash(expression: str) -> str:
    return " ".join(expression.split())


def test_gate_judges_every_job_it_waits_for() -> None:
    """ci-gate's `needs:` and its `name=${{ needs.name.result }}` arguments agree.

    A job only in `needs:` is waited for and then ignored; a job only in the
    arguments renders as `name=` (an empty context), which the script rejects —
    so the first direction is the silent one, but both are checked.
    """
    needs = set(_jobs()["ci-gate"]["needs"])
    pairs = _RESULT_ARG.findall(_gate_run())

    mismatched = [(name, job) for name, job in pairs if name != job]
    assert not mismatched, f"argument name differs from the job it reads: {mismatched}"
    assert {job for _, job in pairs} == needs, (
        f"needs-only: {sorted(needs - {job for _, job in pairs})}; "
        f"arguments-only: {sorted({job for _, job in pairs} - needs)}"
    )


def test_e2e_runs_exactly_when_the_gate_requires_it() -> None:
    """One output decides "needs e2e"; the job's `if:` and the gate both read it.

    If the job read anything else, the two could disagree: e2e skipped while
    the gate was told it was optional, which is the accidental-skip hole.
    """
    jobs = _jobs()

    assert _squash(str(jobs["e2e"]["if"])) == "needs.changes.outputs.e2e == 'true'"
    assert '"expect.e2e=${{ needs.changes.outputs.e2e }}"' in _gate_run()


def test_main_always_needs_e2e() -> None:
    """`promote` needs e2e, and the image bakes docs/*.md, so main cannot skip it."""
    jobs = _jobs()
    output = _squash(str(jobs["changes"]["outputs"]["e2e"]))

    assert "github.ref == 'refs/heads/main'" in output, output
    for area in ("backend", "frontend", "ci"):
        assert f"steps.filter.outputs.{area} == 'true'" in output, (area, output)
    assert "docs" not in output, f"a docs-only change must not need e2e: {output}"
    assert "e2e" in jobs["promote"]["needs"]


def test_backend_suite_runs_on_docs_changes() -> None:
    """Tests read docs/ (check_docs.py's plan, module map and index)."""
    condition = _squash(str(_jobs()["backend"]["if"]))

    assert "needs.changes.outputs.docs == 'true'" in condition, condition


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/mcp.md", {"docs"}),
        ("docs/archive/x.md", {"docs"}),
        ("journal/261105-x.md", set()),
        ("README.md", set()),
        ("frontend/README.md", {"frontend"}),
        ("frontend/e2e/fixtures/note-fixture.md", {"frontend"}),
        (".github/workflows/ci.yml", {"ci"}),
        (".github/dependabot.yml", {"ci"}),
        ("src/library/app.py", {"backend"}),
        ("scripts/ci_gate.sh", {"backend"}),
        # Read at runtime from inside the image (Dockerfile COPY); compose-smoke
        # checks both are there, so a change to either must run it.
        ("models/ocr/latin_PP-OCRv5_rec_mobile.onnx", {"backend"}),
        ("recall-baseline.json", {"backend"}),
    ],
)
def test_path_classification(path: str, expected: set[str]) -> None:
    matched = {
        name
        for name, patterns in _filters().items()
        if any(PurePosixPath(path).full_match(pattern) for pattern in patterns)
    }

    assert matched == expected, f"{path}: filters {sorted(matched)}, expected {sorted(expected)}"


def test_every_job_has_a_time_limit() -> None:
    """GitHub's default is 360 minutes; an apt stall once held a shard for 27."""
    unlimited = [name for name, job in _jobs().items() if "timeout-minutes" not in job]

    assert not unlimited, f"jobs without timeout-minutes: {unlimited}"
