"""Tests for scripts/ci_gate.sh (the CI aggregator gate).

`scripts/` is not an importable package and this one is shell, so invoke it by
file path via subprocess — the same "test a CI script by file path" pattern as
tests/test_coverage_summary.py.

The gate's contract:

* `changes` must be exactly `success`. It is unconditional in the workflow, and
  every other job `needs:` it — GitHub reports a job whose dependency failed as
  `skipped`, so a broken `changes` skips all five others. Tolerating a
  non-success `changes` would launder a wholly untested run into a green
  required check (`test_broken_changes_is_not_laundered_by_skips`).
* Every other job may be `success` or `skipped` (path-skipping is legitimate,
  and a skipped *required* check blocks a merge forever) — unless an
  `expect.<job>=true` argument says this change needed it, in which case it
  must be exactly `success`. That is what tells a deliberate skip (docs-only
  PR, `changes.outputs.e2e=false`) from an accidental one (an `if:` typo that
  stops e2e on a code PR), which look identical in `needs.<job>.result`.
"""

import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_gate.sh"

# Every job ci-gate needs besides `changes`. tests/test_ci_workflow.py pins the
# workflow's own list to its argument list; this one only has to be realistic.
_OTHERS = ("backend", "frontend", "e2e", "compose-smoke", "build", "docs-stamps", "typecheck")


def _argv(changes: str = "success", *extra: str, **overrides: str) -> list[str]:
    """Build `name=result` pairs, defaulting every non-overridden job to success.

    `extra` is appended verbatim — the `expect.<job>=...` arguments.
    """
    pairs = [f"changes={changes}"]
    pairs += [f"{name}={overrides.get(name.replace('-', '_'), 'success')}" for name in _OTHERS]
    return [*pairs, *extra]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), *argv],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("case", "argv", "expected_exit"),
    [
        ("everything green", _argv(), 0),
        ("legitimate path skip", _argv(backend="skipped"), 0),
        (
            "docs-only PR",
            _argv(**{name.replace("-", "_"): "skipped" for name in _OTHERS}),
            0,
        ),
        (
            "the laundering case: changes failed, so all five skipped",
            _argv("failure", **{name.replace("-", "_"): "skipped" for name in _OTHERS}),
            1,
        ),
        (
            "changes skipped (structurally impossible → red)",
            _argv("skipped", **{name.replace("-", "_"): "skipped" for name in _OTHERS}),
            1,
        ),
        ("real failure", _argv(backend="failure"), 1),
        ("cancellation", _argv(e2e="cancelled"), 1),
    ],
)
def test_gate_exit_codes(case: str, argv: list[str], expected_exit: int) -> None:
    result = _run(argv)
    assert result.returncode == expected_exit, (
        f"{case}: argv={argv} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_broken_changes_is_not_laundered_by_skips() -> None:
    """The whole point: a failed `changes` skips all five others and must go red.

    This exact argv is what today's green-but-untested run looks like.
    """
    argv = _argv("failure", **{name.replace("-", "_"): "skipped" for name in _OTHERS})

    result = _run(argv)

    assert result.returncode == 1, f"laundered an untested run as green:\n{result.stdout}"
    output = result.stdout + result.stderr
    assert "::error::" in output, f"no run annotation emitted:\n{output}"
    assert "changes" in output, f"annotation does not name the culprit:\n{output}"


def test_failure_annotation_names_the_failing_job() -> None:
    result = _run(_argv(backend="failure"))

    output = result.stdout + result.stderr
    assert "::error::" in output
    assert "backend" in output
    assert "failure" in output


def test_no_arguments_is_an_error() -> None:
    """An empty argv would otherwise vacuously pass — the same hole in miniature."""
    result = _run([])

    assert result.returncode != 0
    assert "::error::" in result.stdout + result.stderr


def test_missing_changes_pair_is_an_error() -> None:
    """`changes` must be reported; its absence cannot read as a pass."""
    result = _run([f"{name}=success" for name in _OTHERS])

    assert result.returncode == 1
    assert "changes" in result.stdout + result.stderr


# --- expect.<job>: a skip the change did not allow -----------------------------


@pytest.mark.parametrize(
    ("case", "argv", "expected_exit"),
    [
        (
            "code PR, e2e accidentally skipped",
            _argv("success", "expect.e2e=true", e2e="skipped"),
            1,
        ),
        (
            "docs-only PR, e2e deliberately skipped",
            _argv("success", "expect.e2e=false", e2e="skipped"),
            0,
        ),
        ("code PR, e2e ran and passed", _argv("success", "expect.e2e=true"), 0),
        ("expect false, e2e ran anyway (main)", _argv("success", "expect.e2e=false"), 0),
        ("expect true, e2e failed", _argv("success", "expect.e2e=true", e2e="failure"), 1),
        ("expect true, e2e cancelled", _argv("success", "expect.e2e=true", e2e="cancelled"), 1),
        ("expect false, e2e failed", _argv("success", "expect.e2e=false", e2e="failure"), 1),
        (
            "expect true but no e2e result supplied",
            ["changes=success", "backend=success", "expect.e2e=true"],
            1,
        ),
        (
            "expect false but no e2e result supplied (typo'd job name)",
            _argv("success", "expect.e2e2=false"),
            1,
        ),
        ("expect value not a boolean", _argv("success", "expect.e2e=maybe"), 1),
        ("expect value empty (changes output missing)", _argv("success", "expect.e2e="), 1),
        ("expect names no job", _argv("success", "expect.=true"), 1),
    ],
)
def test_gate_expect(case: str, argv: list[str], expected_exit: int) -> None:
    result = _run(argv)
    assert result.returncode == expected_exit, (
        f"{case}: argv={argv} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_required_but_skipped_names_the_job_and_the_output() -> None:
    """The red a reviewer sees must say which job and why it was required."""
    result = _run(_argv("success", "expect.e2e=true", e2e="skipped"))

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert (
        "::error::job 'e2e' was required to run for this change "
        "(changes.outputs.e2e=true) but result: skipped"
    ) in output, output


@pytest.mark.parametrize("value", ["maybe", "", "True", "1"])
def test_bad_expect_value_is_named(value: str) -> None:
    """Only the literal strings GitHub renders a boolean as are accepted."""
    result = _run(_argv("success", f"expect.e2e={value}"))

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "::error::" in output
    assert "expect.e2e" in output, output
