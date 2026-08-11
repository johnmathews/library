"""Tests for scripts/check_docs.py (the documentation freshness gate).

`scripts/` is not an importable package, so load the module by file path.

One test per hole, not one happy path. The failure this gate exists to prevent is
a *presence-only* stamp check — one that greps for the field labels and stops,
so `Last verified: 2019-01-01` passes forever and so does `Last verified: banana`.
A staleness gate that cannot detect staleness is the "check that cannot fail"
this project keeps finding, so each rule gets a test that reds it, plus the one
that pins the design: **an old stamp in an untouched repo stays green**, which is
what a calendar-based gate gets wrong.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_docs.py"
_spec = importlib.util.spec_from_file_location("check_docs", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_docs = importlib.util.module_from_spec(_spec)
# Registered before exec: @dataclass resolves its own module via
# sys.modules[cls.__module__], which is None for a module loaded by path alone.
# The sibling script tests do not need this only because their scripts have no
# dataclasses.
sys.modules["check_docs"] = check_docs
_spec.loader.exec_module(check_docs)

TODAY = date(2026, 7, 29)


def doc(
    *,
    status: str = "active",
    updated: str = "2026-07-01",
    verified: str | None = "2026-07-20 — method: read the module and ran the suite",
    covers: str | None = None,
    body: str = "\nSome prose.\n",
) -> str:
    """A document with a stamp, shaped exactly like the real ones."""
    lines = ["# A Doc", "", f"**Status:** {status}. **Last updated:** {updated} (things)."]
    if verified is not None:
        lines.append(f"**Last verified:** {verified}")
    if covers is not None:
        lines.append(f"**Covers:** {covers}")
    return "\n".join(lines) + body


def rules(violations: list) -> set[str]:
    return {v.rule for v in violations}


def check(text: str, *, last_commit: str | None = "2026-07-10", covered: tuple = ()) -> list:
    return check_docs.check_document(
        "docs/thing.md",
        text,
        last_commit=date.fromisoformat(last_commit) if last_commit else None,
        covered_changes=covered,
        today=TODAY,
    )


class TestTheDesign:
    def test_an_old_stamp_in_an_untouched_repo_is_clean(self) -> None:
        """The test that pins the design, and that a calendar gate fails.

        Verified in 2019 and nothing has been committed since: the document
        cannot have drifted from code change, so it is NOT due. A fixed window
        would red it, teach re-stamping on a schedule, and thereby manufacture
        the false verification this convention exists to prevent.
        """
        text = doc(
            updated="2019-01-01",
            verified="2019-01-02 — method: read it end to end",
        )
        assert check(text, last_commit="2019-01-02") == []

    def test_a_fresh_stamp_in_a_busy_repo_is_clean(self) -> None:
        """The other half: recency alone is not what is being measured."""
        assert (
            check(doc(verified="2026-07-28 — method: ran the suite"), last_commit="2026-07-28")
            == []
        )


class TestSeededViolations:
    def test_no_status_line_fails(self) -> None:
        assert rules(check("# A Doc\n\nJust prose.\n")) == {"no-stamp"}

    def test_active_without_last_verified_fails(self) -> None:
        assert "missing-verified" in rules(check(doc(verified=None)))

    def test_unparseable_date_fails(self) -> None:
        """`banana` must be red, never skipped — absence of a parse is not a pass."""
        assert "unparseable-date" in rules(check(doc(verified="banana — method: x")))

    def test_impossible_date_fails(self) -> None:
        """A well-shaped but non-existent date must not slip through."""
        assert "unparseable-date" in rules(check(doc(verified="2026-13-45 — method: x")))

    def test_missing_method_fails(self) -> None:
        assert "missing-method" in rules(check(doc(verified="2026-07-20")))

    def test_empty_method_fails(self) -> None:
        assert "missing-method" in rules(check(doc(verified="2026-07-20 — method:")))

    def test_future_date_fails(self) -> None:
        assert "future-date" in rules(check(doc(verified="2027-01-01 — method: x")))

    def test_doc_edited_after_verification_fails(self) -> None:
        """The comparative rule: edited without being re-verified."""
        violations = check(doc(verified="2026-07-20 — method: x"), last_commit="2026-07-25")
        assert "stale-doc-edit" in rules(violations)
        assert "2026-07-25" in next(v.message for v in violations if v.rule == "stale-doc-edit")

    def test_untracked_file_fails(self) -> None:
        assert "untracked" in rules(check(doc(), last_commit=None))


class TestCoveredCode:
    def test_covered_code_changed_since_verification_fails(self) -> None:
        """The signal that matters most: the code moved, the prose did not.

        A doc can be untouched — so `stale-doc-edit` stays quiet — while the
        module it describes has been rewritten underneath it.
        """
        violations = check(
            doc(covers="src/library/jobs.py"),
            last_commit="2026-07-10",
            covered=("src/library/jobs.py",),
        )
        assert "stale-covered-code" in rules(violations)
        assert "src/library/jobs.py" in next(
            v.message for v in violations if v.rule == "stale-covered-code"
        )

    def test_covers_is_optional(self) -> None:
        """Omitting it is allowed; it costs the precise signal, not a failure."""
        assert check(doc(covers=None)) == []

    def test_covers_parses_a_comma_list_with_backticks(self) -> None:
        stamp = check_docs.parse_stamp(doc(covers="`src/a.py`, src/b/**"))
        assert stamp is not None
        assert stamp.covers == ("src/a.py", "src/b/**")


class TestNotYet:
    def test_not_yet_with_a_reason_is_allowed_while_fresh(self) -> None:
        text = doc(updated="2026-07-20", verified="not yet — written alongside the feature")
        assert check(text, last_commit="2026-07-20") == []

    def test_not_yet_without_a_reason_fails(self) -> None:
        assert "not-yet-no-reason" in rules(check(doc(verified="not yet")))

    def test_not_yet_expires(self) -> None:
        """An exemption must expire by itself, or it is permanent."""
        text = doc(updated="2026-01-01", verified="not yet — will verify later")
        assert "not-yet-expired" in rules(check(text, last_commit="2026-01-01"))


class TestNonActiveStatus:
    def test_superseded_docs_need_no_verification(self) -> None:
        """Archiving a doc is precisely a statement that it stopped being current."""
        text = doc(status="superseded by [x](x.md)", verified=None)
        assert check(text, last_commit="2020-01-01") == []


class TestParsing:
    def test_a_status_far_down_the_file_is_prose_not_a_stamp(self) -> None:
        """Only the head is scanned, so a mid-document mention cannot pass as one."""
        text = "# A Doc\n" + "\nfiller\n" * 30 + "\n**Status:** active\n"
        assert rules(check(text)) == {"no-stamp"}

    def test_a_hyphen_separator_is_accepted_like_an_em_dash(self) -> None:
        stamp = check_docs.parse_stamp(doc(verified="2026-07-20 - method: ran it"))
        assert stamp is not None
        assert stamp.verified_date == date(2026, 7, 20)
        assert stamp.method == "ran it"

    def test_the_long_last_updated_line_does_not_swallow_the_date(self) -> None:
        """api.md's Last updated line is 1,966 characters of running changelog.

        The parser must take the date and stop at the parenthetical, which is why
        `Last verified` goes on its own line rather than being appended to this.
        """
        long_tail = "(" + "x" * 1900 + ")"
        text = (
            "# A Doc\n\n"
            f"**Status:** active. **Last updated:** 2026-07-17 {long_tail}\n"
            "**Last verified:** 2026-07-20 — method: read it\n\nProse.\n"
        )
        stamp = check_docs.parse_stamp(text)
        assert stamp is not None
        assert stamp.last_updated == date(2026, 7, 17)
        assert stamp.verified_date == date(2026, 7, 20)


class TestTheGatedSet:
    def test_point_in_time_material_is_excluded_by_path(self) -> None:
        """By path, not by judgement: ADRs and benchmarks are meant to age."""
        gated = {str(p) for p in check_docs.gated_documents()}
        assert gated, "the gated set must not be empty"
        for excluded in check_docs.EXCLUDED_DIRS:
            assert not any(f"/{excluded}/" in path for path in gated), excluded

    def test_the_gated_set_is_the_sixteen_living_docs(self) -> None:
        """Pins the set so a new top-level doc joins the gate automatically."""
        gated = check_docs.gated_documents()
        assert len(gated) >= 16, f"expected at least the 16 living docs, found {len(gated)}"


class TestRepoState:
    def test_repo_docs_report_the_expected_violations(self) -> None:
        """The gate reds today's tree, and this records exactly why.

        Deliberately not asserting zero: W27 does the verify-and-stamp sweep. This
        pins the starting position so that sweep's progress is measurable, and it
        is the check that proves the gate is not vacuous on real input.

        Flips to expecting zero in W27.
        """
        violations: list = []
        for path in check_docs.gated_documents():
            relative = str(path.relative_to(check_docs.REPO_ROOT))
            violations.extend(
                check_docs.check_document(
                    relative,
                    path.read_text(encoding="utf-8"),
                    last_commit=check_docs.git_last_commit_date(relative),
                    today=TODAY,
                )
            )
        assert violations, "the gate found nothing on a tree known to be unstamped"
        counts: dict[str, int] = {}
        for violation in violations:
            counts[violation.rule] = counts.get(violation.rule, 0) + 1
        # Every living doc is either unstamped or stamped-without-verification.
        assert set(counts) <= {"no-stamp", "missing-verified", "stale-doc-edit"}, counts


class TestShallowDetection:
    """The guard that stops the whole gate going blind.

    Tested through the pure `interpret_shallow`, NOT by asserting the ambient
    repo's depth. The first version of this asserted
    `is_shallow_clone() is False`, which is a property of the *checkout* rather
    than of this code: it passed locally on a full clone and failed in CI's
    backend job, which legitimately uses the default `fetch-depth: 1`. Only the
    docs-stamps job needs full history.
    """

    def test_git_says_shallow(self) -> None:
        assert check_docs.interpret_shallow("true", False) is True

    def test_git_says_not_shallow(self) -> None:
        assert check_docs.interpret_shallow("false", False) is False

    def test_the_shallow_marker_alone_is_enough(self) -> None:
        """`rev-parse --is-shallow-repository` needs git >= 2.15.

        On older git it prints nothing, and treating "no answer" as "not shallow"
        would fail OPEN on the one guard whose whole job is to stop the gate
        passing everything silently.
        """
        assert check_docs.interpret_shallow("", True) is True

    def test_no_answer_and_no_marker_is_not_shallow(self) -> None:
        assert check_docs.interpret_shallow("", False) is False

    def test_whitespace_is_tolerated(self) -> None:
        assert check_docs.interpret_shallow(" true\n", False) is True

    def test_the_real_detector_returns_a_bool(self) -> None:
        """The wiring runs; its ANSWER depends on the checkout, so is not asserted."""
        assert isinstance(check_docs.is_shallow_clone(), bool)


def test_main_exits_two_on_an_unreadable_path(tmp_path: Path) -> None:
    """Cannot-check must never share an exit code with nothing-wrong."""
    assert check_docs.main([str(tmp_path / "absent.md")]) == 2


class TestRatchet:
    """--max-violations must fail in BOTH directions.

    Driven through the pure `ratchet_verdict`, NOT through `main`. `main` runs the
    shallow-clone guard first and returns 2 in a shallow checkout — correct
    behaviour, and the reason the first version of these tests passed locally and
    failed in CI's backend job, which uses the default `fetch-depth: 1`. That is
    the same environment-dependence this file already got wrong once.
    """

    def test_at_the_baseline_passes(self) -> None:
        code, message = check_docs.ratchet_verdict(15, 15)
        assert code == 0
        assert "exactly the baseline" in message

    def test_above_the_baseline_fails(self) -> None:
        code, message = check_docs.ratchet_verdict(16, 15)
        assert code == 1
        assert "got worse" in message

    def test_below_the_baseline_fails_so_gains_get_locked_in(self) -> None:
        """Slack in the baseline is where the next regression hides."""
        code, message = check_docs.ratchet_verdict(14, 15)
        assert code == 1
        assert "lower the baseline" in message

    def test_zero_baseline_is_the_post_sweep_state(self) -> None:
        """What W27 drives it to: no violations, no tolerance."""
        assert check_docs.ratchet_verdict(0, 0)[0] == 0
        assert check_docs.ratchet_verdict(1, 0)[0] == 1

    def test_main_applies_the_ratchet_when_history_is_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One end-to-end pass, with the environment-dependent guard stubbed."""
        monkeypatch.setattr(check_docs, "is_shallow_clone", lambda: False)
        doc_path = tmp_path / "unstamped.md"
        doc_path.write_text("# No stamp\n\nProse.\n")
        # Exactly one violation: `no-stamp` returns early, so `untracked` is
        # never also reported for the same document — one actionable message per
        # doc rather than a pile.
        assert check_docs.main([str(doc_path), "--max-violations", "1"]) == 0
        assert check_docs.main([str(doc_path), "--max-violations", "2"]) == 1

    def test_main_refuses_before_ratcheting_in_a_shallow_clone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cannot-check outranks the ratchet: exit 2, never a tolerated 0."""
        monkeypatch.setattr(check_docs, "is_shallow_clone", lambda: True)
        doc_path = tmp_path / "unstamped.md"
        doc_path.write_text("# No stamp\n\nProse.\n")
        assert check_docs.main([str(doc_path), "--max-violations", "99"]) == 2
