"""Tests for scripts/coverage_summary.py (the CI coverage-merge script).

`scripts/` is not an importable package, so load the module by file path.
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "coverage_summary.py"
_spec = importlib.util.spec_from_file_location("coverage_summary", _SCRIPT)
assert _spec is not None and _spec.loader is not None
coverage_summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_summary)


def _write_backend(path: Path, pct: float) -> Path:
    path.write_text(json.dumps({"totals": {"percent_covered": pct}}), encoding="utf-8")
    return path


def _write_frontend(path: Path, pct: float) -> Path:
    path.write_text(json.dumps({"total": {"lines": {"pct": pct}}}), encoding="utf-8")
    return path


def test_build_summary_both_present(tmp_path: Path) -> None:
    backend = _write_backend(tmp_path / "coverage.json", 94.567)
    frontend = _write_frontend(tmp_path / "coverage-summary.json", 88.2)

    summary = coverage_summary.build_summary(
        backend_json=backend,
        frontend_json=frontend,
        generated_at="2026-06-28T12:00:00Z",
        git_sha="abc123",
    )

    assert summary == {
        "backend": {"pct": 94.6, "threshold": 85.0},
        "frontend": {"pct": 88.2, "threshold": 85.0},
        "generated_at": "2026-06-28T12:00:00Z",
        "git_sha": "abc123",
    }


def test_build_summary_missing_backend(tmp_path: Path) -> None:
    frontend = _write_frontend(tmp_path / "coverage-summary.json", 90.0)

    summary = coverage_summary.build_summary(
        backend_json=tmp_path / "nope.json",
        frontend_json=frontend,
        generated_at="2026-06-28T12:00:00Z",
        git_sha=None,
    )

    assert summary["backend"]["pct"] is None
    assert summary["backend"]["threshold"] == 85.0
    assert summary["frontend"]["pct"] == 90.0
    assert summary["git_sha"] is None


def test_build_summary_missing_both(tmp_path: Path) -> None:
    summary = coverage_summary.build_summary(
        backend_json=tmp_path / "a.json",
        frontend_json=tmp_path / "b.json",
        generated_at=None,
        git_sha=None,
    )

    assert summary["backend"]["pct"] is None
    assert summary["frontend"]["pct"] is None
    assert summary["backend"]["threshold"] == 85.0
    assert summary["frontend"]["threshold"] == 85.0
    assert summary["generated_at"] is None


def test_main_writes_output_and_exits_zero(tmp_path: Path) -> None:
    backend = _write_backend(tmp_path / "coverage.json", 95.0)
    frontend = _write_frontend(tmp_path / "coverage-summary.json", 87.0)
    output = tmp_path / "out" / "coverage-summary.json"
    output.parent.mkdir()

    rc = coverage_summary.main(
        [
            "--backend-json",
            str(backend),
            "--frontend-json",
            str(frontend),
            "--git-sha",
            "deadbeef",
            "--generated-at",
            "2026-06-28T00:00:00Z",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["backend"]["pct"] == 95.0
    assert written["frontend"]["pct"] == 87.0
    assert written["git_sha"] == "deadbeef"
