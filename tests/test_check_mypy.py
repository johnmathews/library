"""Tests for scripts/check_mypy.py (the mypy quarantine ratchet).

`scripts/` is not an importable package, so load the module by file path.

The hole this gate exists to close is stated in `pyproject.toml` itself: the
mypy quarantine "only fails in one direction. mypy has no baseline mechanism,
so if a module is cleaned up the override lingers silently instead of failing
to force the gain to be locked in." So the tests that matter are the *downward*
ones — a suppressed-count that fell, and an override entry that now suppresses
nothing — not just the regression case. Each rule gets a test that reds it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_mypy.py"
_spec = importlib.util.spec_from_file_location("check_mypy", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_mypy = importlib.util.module_from_spec(_spec)
sys.modules["check_mypy"] = check_mypy
_spec.loader.exec_module(check_mypy)


# --- Parsing mypy output ----------------------------------------------------


def test_counts_errors_per_module_and_code() -> None:
    output = (
        "src/library/series.py:10: error: Argument 1 has incompatible type  [arg-type]\n"
        "src/library/series.py:20: error: Argument 2 has incompatible type  [arg-type]\n"
        "src/library/series.py:30: error: Incompatible return value  [return-value]\n"
        "Found 3 errors in 1 file (checked 94 source files)\n"
    )

    assert check_mypy.parse_counts(output) == {"library.series": {"arg-type": 2, "return-value": 1}}


def test_maps_nested_package_paths_to_dotted_modules() -> None:
    """`src/library/ask/engine.py` must key as `library.ask.engine` — the same
    string `pyproject.toml`'s `module = ...` uses, or nothing ever matches."""
    output = 'src/library/ask/engine.py:841: error: no attribute "name"  [union-attr]\n'

    assert check_mypy.parse_counts(output) == {"library.ask.engine": {"union-attr": 1}}


def test_ignores_notes_and_summary_lines() -> None:
    """Only `error:` lines count. A `note:` is commentary, and the summary line
    would otherwise be parsed as an error in a file called `Found`."""
    output = (
        "src/library/series.py:10: error: Incompatible type  [arg-type]\n"
        "src/library/series.py:10: note: Consider using a cast\n"
        "Found 1 error in 1 file (checked 94 source files)\n"
    )

    assert check_mypy.parse_counts(output) == {"library.series": {"arg-type": 1}}


# --- The ratchet verdict ----------------------------------------------------


BASELINE = {"library.series": {"arg-type": 7}}


def test_passes_when_counts_match_the_baseline_exactly() -> None:
    code, messages = check_mypy.ratchet_verdicts({"library.series": {"arg-type": 7}}, BASELINE)

    assert code == 0
    assert messages == []


def test_fails_when_a_quarantined_count_rises() -> None:
    code, messages = check_mypy.ratchet_verdicts({"library.series": {"arg-type": 8}}, BASELINE)

    assert code == 1
    assert "library.series" in messages[0]
    assert "arg-type" in messages[0]
    assert "8" in messages[0] and "7" in messages[0]


def test_fails_when_a_quarantined_count_falls() -> None:
    """The whole point of the unit. mypy is green either way — only this check
    turns an un-locked-in improvement into a failure."""
    code, messages = check_mypy.ratchet_verdicts({"library.series": {"arg-type": 5}}, BASELINE)

    assert code == 1
    assert "BELOW" in messages[0]


def test_fails_when_an_override_suppresses_nothing() -> None:
    """A code that now has zero errors is a dead override: it silently disables
    that check for the whole module and must be deleted, not re-baselined."""
    code, messages = check_mypy.ratchet_verdicts({}, BASELINE)

    assert code == 1
    assert "no longer suppresses anything" in messages[0]
    assert "library.series" in messages[0]


def test_ignores_errors_outside_the_quarantine() -> None:
    """Errors in a module nothing suppresses already red the ordinary mypy run.
    Counting them here would double-report and couple this gate to that one."""
    counts = {"library.series": {"arg-type": 7}, "library.jobs": {"assignment": 3}}

    assert check_mypy.ratchet_verdicts(counts, BASELINE) == (0, [])


def test_flags_a_quarantine_entry_with_no_baseline_number() -> None:
    """A new `disable_error_code` override added without a baseline entry is an
    unmeasured suppression — `ratchet_verdicts` never sees it, so the pairing
    has to be checked against pyproject itself."""
    gaps = check_mypy.baseline_gaps(PYPROJECT, {})

    assert any("library.series" in gap for gap in gaps)
    assert any("arg-type" in gap for gap in gaps)


def test_no_gaps_when_the_baseline_covers_every_quarantined_code() -> None:
    assert check_mypy.baseline_gaps(PYPROJECT, BASELINE) == []


def test_flags_a_baseline_entry_for_a_code_nothing_quarantines() -> None:
    """The mirror of the gap above. Left unchecked, a stale entry makes
    `ratchet_verdicts` claim the module "disables [code]" when pyproject says
    no such thing — a failure message that sends the reader to the wrong file."""
    stale = {"library.series": {"arg-type": 7, "no-any-return": 2}}

    gaps = check_mypy.baseline_gaps(PYPROJECT, stale)

    assert any("no-any-return" in gap for gap in gaps)


# --- Building the un-quarantined config -------------------------------------


PYPROJECT = {
    "tool": {
        "mypy": {
            "python_version": "3.13",
            "files": ["src"],
            "warn_unused_ignores": True,
            "enable_error_code": ["ignore-without-code"],
            "overrides": [
                {"module": ["asyncpg.*", "filetype.*"], "ignore_missing_imports": True},
                {"module": "library.series", "disable_error_code": ["arg-type"]},
            ],
        }
    }
}


def test_config_drops_the_quarantine_overrides() -> None:
    """Lifting the quarantine is the measurement. If `disable_error_code`
    survives into the generated config, every count reads zero and the gate
    passes while suppressing everything."""
    config = check_mypy.no_quarantine_config(PYPROJECT)

    assert "disable_error_code" not in config
    assert "[mypy-library.series]" not in config


def test_config_keeps_the_untyped_import_overrides() -> None:
    """Third-party packages that ship no types are not our errors. Dropping
    these too would flood the counts with someone else's packaging."""
    config = check_mypy.no_quarantine_config(PYPROJECT)

    assert "[mypy-asyncpg.*,filetype.*]" in config
    assert "ignore_missing_imports = True" in config


def test_config_carries_the_top_level_settings_through() -> None:
    """Derived from pyproject, not restated: a settings change there must reach
    the measurement run without anyone editing this script."""
    config = check_mypy.no_quarantine_config(PYPROJECT)

    assert "python_version = 3.13" in config
    assert "files = src" in config
    assert "warn_unused_ignores = True" in config
    assert "enable_error_code = ignore-without-code" in config


def test_quarantined_codes_are_read_from_pyproject() -> None:
    """The baseline's key set must be derived from the real overrides, so a
    quarantine entry can never exist without a baseline number guarding it."""
    assert check_mypy.quarantined_codes(PYPROJECT) == {"library.series": ["arg-type"]}


# --- Refusing to measure a run that did not happen ---------------------------


def test_accepts_the_exit_codes_a_real_measurement_produces() -> None:
    """0 = clean, 1 = errors found. Both are successful measurements: with the
    quarantine lifted, 1 is in fact the expected one."""
    check_mypy.require_real_run(0, "")
    check_mypy.require_real_run(1, "")


def test_rejects_a_run_that_never_type_checked_anything() -> None:
    """mypy exits 2 on a bad config or an internal error, printing nothing to
    stdout. Parsing that yields zero counts everywhere, which `ratchet_verdicts`
    would faithfully report as "every override is dead — delete them all". A
    gate that turns its own breakage into confident advice is worse than one
    that fails, so an unexpected exit code has to stop the run."""
    with pytest.raises(RuntimeError) as excinfo:
        check_mypy.require_real_run(2, "mypy.ini: Unrecognized option: nonsense")

    assert "exit code 2" in str(excinfo.value)
    assert "Unrecognized option" in str(excinfo.value)


# --- The real tree ----------------------------------------------------------


def test_repo_baseline_matches_the_real_quarantine() -> None:
    """The checked-in baseline covers exactly the modules pyproject quarantines.
    Guards the failure mode where someone adds an override and forgets the
    baseline, which `ratchet_verdicts` can only catch if it is given the pair."""
    import tomllib

    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    baseline = check_mypy.load_baseline(root / check_mypy.BASELINE_FILENAME)

    assert set(check_mypy.quarantined_codes(pyproject)) == set(baseline)
    for module, codes in check_mypy.quarantined_codes(pyproject).items():
        assert set(codes) == set(baseline[module])
