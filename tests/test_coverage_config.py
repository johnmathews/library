"""Guards on `[tool.coverage.run]` in pyproject.toml.

The setting under test is a performance cliff with no functional signal: the
suite passes and the coverage percentage is identical either way, so a
regression here is invisible except in CI wall clock. Measured over the full
suite, `core = "sysmon"` runs in 310s against 551s for the C tracer — the same
2115 tests, the same 11284 statements, the same 601 missing, the same 95%.

Coverage only uses `sysmon` when nothing else forces the C tracer, and
declaring any concurrency mode other than `thread` does force it. That is the
trap: re-adding `concurrency = ["greenlet", "thread"]` (the old fix for
SQLAlchemy's greenlet bridge, obsolete under `sys.monitoring`) looks harmless,
changes no test result, and silently costs four minutes on every CI run.
"""

import tomllib
from pathlib import Path
from typing import Any

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _coverage_run_config() -> dict[str, Any]:
    with _PYPROJECT.open("rb") as handle:
        config: dict[str, Any] = tomllib.load(handle)["tool"]["coverage"]["run"]
    return config


def test_coverage_uses_the_sysmon_core() -> None:
    assert _coverage_run_config().get("core") == "sysmon", (
        'pyproject must keep `core = "sysmon"` under [tool.coverage.run]; '
        "without it coverage falls back to the C tracer and the backend suite "
        "takes ~75% longer for an identical report."
    )


def test_coverage_declares_no_tracer_forcing_concurrency() -> None:
    """`thread` is free; every other mode drops coverage back to the C tracer."""
    concurrency = _coverage_run_config().get("concurrency", [])
    assert set(concurrency) <= {"thread"}, (
        f"[tool.coverage.run] concurrency={concurrency!r} forces the C tracer. "
        "SQLAlchemy's greenlet bridge no longer needs a concurrency declaration "
        "— sys.monitoring traces through it. See the comment in pyproject.toml."
    )
