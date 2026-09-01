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
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar

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

    def test_future_date_tolerates_one_day_of_timezone_skew(self) -> None:
        """Tomorrow's date is allowed, because it is reachable without lying.

        `git log --date=short` renders a commit in its own recorded offset, so a
        commit authored at 00:42+0200 reads as the 13th on a UTC runner too, and
        `stale-doc-edit` then *requires* a stamp of the 13th. But `today` here is
        the runner's, still the 12th for those two hours. Without slack the two
        rules contradict each other and no stamp value passes both.
        """
        tomorrow = TODAY + timedelta(days=1)
        assert "future-date" not in rules(check(doc(verified=f"{tomorrow} — method: x")))

    def test_future_date_still_fails_beyond_the_skew(self) -> None:
        """The slack is one day, not a licence: no offset is two days wide."""
        far = TODAY + timedelta(days=2)
        assert "future-date" in rules(check(doc(verified=f"{far} — method: x")))

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

    def test_a_frontend_path_is_covered_like_any_other(self) -> None:
        """`Covers:` is language-agnostic, and that had never been asserted.

        Until 2026-09-01 every `Covers:` line in the repository named a path
        under `src/library/`, `migrations/` or `scripts/` — so `stale-covered-code`
        had never once run against a `frontend/` pathspec, and a frontend-only
        change could leave `spending-view.md` or `frontend.md` asserting the
        opposite of the shipped tree with CI green. This pins the rule half.
        """
        violations = check(
            doc(covers="frontend/src/views/SpendingWorkspaceView.vue"),
            last_commit="2026-07-10",
            covered=("frontend/src/views/SpendingWorkspaceView.vue",),
        )
        assert "stale-covered-code" in rules(violations)
        assert "frontend/src/views/SpendingWorkspaceView.vue" in next(
            v.message for v in violations if v.rule == "stale-covered-code"
        )

    def test_covers_is_optional(self) -> None:
        """Omitting it is allowed; it costs the precise signal, not a failure."""
        assert check(doc(covers=None)) == []

    def test_covers_parses_a_comma_list_with_backticks(self) -> None:
        stamp = check_docs.parse_stamp(doc(covers="`src/a.py`, src/b/**"))
        assert stamp is not None
        assert stamp.covers == ("src/a.py", "src/b/**")


def _repo_with_commit_at(repo: Path, when: str) -> Path:
    """A one-commit git repo whose single commit is stamped at ``when``.

    Both author and committer dates are pinned, since the two are compared by
    different rules and a drifting committer date would make the test's own
    history depend on when it ran.
    """
    repo.mkdir()

    def run(*args: str, **dates: str) -> None:
        subprocess.run(args, cwd=repo, check=True, capture_output=True, env={**os.environ, **dates})

    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (repo / "covered.py").write_text("x = 1\n")
    run("git", "add", "covered.py")
    run("git", "commit", "-q", "-m", "c", GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return repo


class TestCoveredChangeDetectionIsClockIndependent:
    """`stale-covered-code` must give the same answer at every hour of the day.

    It was written as ``git log --since=<verified_date>``. Git's ``approxidate``
    fills the fields a date string leaves unspecified from the **current clock**,
    so that argument does not mean "since the start of that date" — it means
    "since that date at whatever time it is now, locally". A commit made partway
    through the verified date is therefore reported by a morning run and hidden
    by an afternoon one, every day, indefinitely. `migration.md` sat stale on a
    green `main` that way: its covered `cli.py` changed at 16:08Z on the very
    date it was stamped.

    The replacement compares the last commit *date* per covered path, which is
    what `stale-doc-edit` already does, so both comparative rules now share one
    primitive and one meaning of "since".
    """

    def test_it_compares_last_commit_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The unit that needs no repository and no clock."""
        last: dict[str, date | None] = {
            "after.py": date(2026, 7, 30),
            "same-day.py": date(2026, 7, 29),
            "before.py": date(2026, 7, 28),
            "untracked.py": None,
        }
        monkeypatch.setattr(check_docs, "git_last_commit_date", lambda path: last[path])
        changed = check_docs.git_changed_since(date(2026, 7, 29), tuple(last))
        assert changed == ("after.py",)

    def test_a_same_day_change_is_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Date-granularity, stated deliberately rather than left to emerge.

        A stamp is a date, so a code commit on that same date cannot be ordered
        against it — and the normal workflow is to verify a doc and commit it
        *alongside* the code change, which `>=` would flag every single time.
        `stale-doc-edit` already resolves this the same way. The cost is a
        bounded blind spot of one day, and it is the reason the rule is not the
        whole guarantee: the `method:` string is.
        """
        monkeypatch.setattr(check_docs, "git_last_commit_date", lambda path: date(2026, 7, 29))
        assert check_docs.git_changed_since(date(2026, 7, 29), ("x.py",)) == ()

    def test_the_hour_of_the_commit_cannot_change_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property, against a real repository rather than a stub.

        Two commits on the same day, one just after midnight and one just before
        it. Under date comparison they are indistinguishable, so the rule must
        return the same verdict for both. Under `--since` it returned different
        verdicts for all but ~30 minutes of each day.
        """
        stamped = date(2026, 7, 29)
        verdicts = set()
        for hour in ("00:30:00", "23:30:00"):
            repo = _repo_with_commit_at(tmp_path / f"repo-{hour[:2]}", f"{stamped}T{hour}+00:00")
            monkeypatch.setattr(check_docs, "REPO_ROOT", repo)
            verdicts.add(check_docs.git_changed_since(stamped, ("covered.py",)))

        assert len(verdicts) == 1, f"verdict depends on the commit's hour: {verdicts}"


class TestPathspecsResolveForEveryLanguage:
    """`git_changed_since` must resolve a nested `frontend/` pathspec.

    The sibling test above pins the *rule*: given a changed path, the violation
    fires. That is not the same claim as this one, and only this one would have
    caught the gap it was written for — every `Covers:` line in the repository
    named `src/library/`, `migrations/` or `scripts/`, so the pathspec half had
    only ever been exercised against top-level Python directories. A doc
    covering `frontend/src/views/` is worthless if the lookup behind it silently
    matches nothing, and a lookup that matches nothing is indistinguishable from
    a doc that is up to date.
    """

    def test_a_nested_frontend_pathspec_matches_a_real_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()

        def run(*args: str, **dates: str) -> None:
            subprocess.run(
                args, cwd=repo, check=True, capture_output=True, env={**os.environ, **dates}
            )

        when = "2026-07-30T12:00:00+00:00"
        run("git", "init", "-q", ".")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "T")
        nested = repo / "frontend" / "src" / "views"
        nested.mkdir(parents=True)
        (nested / "SomeView.vue").write_text("<template />\n")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", "c", GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        monkeypatch.setattr(check_docs, "REPO_ROOT", repo)

        # The exact file, the directory prefix, and a glob — the three shapes a
        # `Covers:` line actually uses.
        for pattern in (
            "frontend/src/views/SomeView.vue",
            "frontend/src/views/",
            "frontend/src/**",
        ):
            assert check_docs.git_changed_since(date(2026, 7, 29), (pattern,)) == (pattern,), (
                f"pathspec {pattern!r} matched nothing"
            )

    def test_at_least_one_gated_doc_covers_a_frontend_path(self) -> None:
        """The blind spot is closed and must stay closed.

        Deleting a `Covers:` line to quiet a red build would silently restore
        the state where a frontend-only change cannot make any document stale.
        That should cost a red test, not nothing.
        """
        covering_frontend = [
            path
            for path in check_docs.gated_documents()
            if (stamp := check_docs.parse_stamp(path.read_text())) is not None
            and any(pattern.startswith("frontend/") for pattern in stamp.covers)
        ]
        assert covering_frontend, (
            "no gated document names a frontend/ path in Covers: — "
            "stale-covered-code cannot fire for any frontend-only change"
        )


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
    def test_every_gated_doc_carries_a_verified_stamp(self) -> None:
        """Every living document is stamped, verified, and says how.

        The verify-and-stamp sweep cleared the backlog this used to pin, so the
        assertion is now the shape the sweep established: a parseable stamp,
        status `active`, a real (non-future) ISO date, and a non-empty `method`.

        **Deliberately no git in this test.** The comparative rules
        (`stale-doc-edit`, `stale-covered-code`) need real history, and the
        `backend` job checks out at `fetch-depth: 1` — under which every file
        reports HEAD's date, so asserting on them here would red this job on the
        first unrelated merge while `docs-stamps` (`fetch-depth: 0`) stayed
        green. Those rules are covered by the pure unit tests above and enforced
        for real by the `docs-stamps` job; this test owns the part that is a
        property of the *text*.
        """
        gated = check_docs.gated_documents()
        assert gated, "no gated documents found — the glob is broken"

        today = date.today()
        problems: list[str] = []
        for path in gated:
            relative = str(path.relative_to(check_docs.REPO_ROOT))
            stamp = check_docs.parse_stamp(path.read_text(encoding="utf-8"))
            if stamp is None:
                problems.append(f"{relative}: no stamp")
                continue
            if stamp.status != "active":
                # A living doc that is not `active` would silently skip every
                # rule below, so the sweep's guarantee has to name it.
                problems.append(f"{relative}: status is {stamp.status!r}, not 'active'")
                continue
            if stamp.verified_date is None:
                problems.append(f"{relative}: `Last verified` is not an ISO date")
            elif (stamp.verified_date - today).days > check_docs.FUTURE_DATE_GRACE_DAYS:
                # Same slack as the rule itself: this job runs in UTC, and a doc
                # stamped just after midnight in +0200 is legitimately "tomorrow"
                # here. Without it this test reds for two hours a day.
                problems.append(f"{relative}: `Last verified` is in the future")
            if not stamp.method:
                problems.append(f"{relative}: no `— method:` on the stamp")
        assert not problems, problems


PLAN_FRONTMATTER = """---
plan: library-greenfield-build
units:
  - id: W1
    title: Repo scaffold
  - id: W2
    title: Database schema
  - id: W17
    title: Deployment hardening
---

# Improvement plan
"""


class TestWorkUnitCitations:
    """`Wn` in a gated doc must resolve against the archived plan (W14)."""

    def test_units_parse_from_the_frontmatter(self) -> None:
        assert check_docs.parse_plan_units(PLAN_FRONTMATTER) == frozenset({1, 2, 17})

    def test_a_known_citation_is_clean(self) -> None:
        violations = check_docs.check_work_unit_citations(
            "docs/api.md", "the upload path (W2) does this", frozenset({1, 2, 17})
        )
        assert violations == []

    def test_a_seeded_w99_fails(self) -> None:
        violations = check_docs.check_work_unit_citations(
            "docs/api.md", "as decided in (W99)", frozenset({1, 2, 17})
        )
        assert rules(violations) == {"unknown-work-unit"}
        assert "W99" in violations[0].message

    def test_w3c_is_not_a_citation(self) -> None:
        """`\\b` on both sides: the standards body is not work unit 3."""
        violations = check_docs.check_work_unit_citations(
            "docs/frontend.md", "follows the W3C spec", frozenset({1, 2, 17})
        )
        assert violations == []

    def test_each_unknown_unit_is_reported_once(self) -> None:
        violations = check_docs.check_work_unit_citations(
            "docs/api.md", "(W99) and again (W99) and (W98)", frozenset({1, 2, 17})
        )
        assert len(violations) == 2

    def test_an_empty_unit_list_is_a_violation_not_a_pass(self) -> None:
        """The rule must not go quietly vacuous if the plan moves.

        Checking membership against an empty set would pass every citation,
        which is the "check that cannot fail" this whole module is built
        against.
        """
        violations = check_docs.check_work_unit_citations("docs/api.md", "(W99)", frozenset())
        assert rules(violations) == {"plan-unreadable"}

    def test_the_real_plan_declares_the_seventeen_units(self) -> None:
        plan = check_docs.REPO_ROOT / check_docs.WORK_UNIT_PLAN
        assert check_docs.parse_plan_units(plan.read_text(encoding="utf-8")) == frozenset(
            range(1, 18)
        )

    # `test_every_citation_in_the_repo_resolves` used to live here: it walked
    # `gated_documents()` applying `check_work_unit_citations` to each, and
    # asserted no offenders. That is precisely what `check_docs.py` itself does
    # over the same document set, and `docs-stamps` runs it unconditionally on
    # every push and PR — so the test was a second copy of a live gate, and the
    # one it duplicated is the one that actually blocks a merge.
    #
    # Its name also promised far more than it delivered: "every citation in the
    # repo" was really "every citation in the 21 stamped documents". A bogus
    # `(W99)` in a journal entry passed it, and still does — journal entries are
    # deliberately outside the citation gate, and the test's name was the only
    # thing suggesting otherwise. Deleting it removed a duplicate; it did not
    # remove coverage. See journal/260901-ci-gate-hardening.md.


CONFIG_SOURCE = """
_PRICED_MODEL_FIELDS: tuple[str, ...] = (
    "extraction_model",
    "ask_model",
)


class Settings(BaseSettings):
    extraction_model: str = "claude-haiku-4-5"
    ask_model: str = "claude-opus-4-8"
    unpriced_model: str = "claude-something-else"
"""


class TestModelIdentity:
    """A model id quoted next to its settings field must match config (W13)."""

    def test_defaults_parse_and_exclude_unpriced_fields(self) -> None:
        assert check_docs.parse_model_defaults(CONFIG_SOURCE) == {
            "extraction_model": "claude-haiku-4-5",
            "ask_model": "claude-opus-4-8",
        }

    DEFAULTS: ClassVar[dict[str, str]] = {
        "ask_model": "claude-opus-4-8",
        "extraction_model": "claude-haiku-4-5",
    }

    @pytest.mark.parametrize(
        "text",
        [
            "model (`ask_model` = `claude-opus-4-8`). Each has",
            "`ask_model` (`claude-opus-4-8`) is multimodal",
            "call (`ask_model`, default `claude-opus-4-8` — the",
            "`ask_model`: `claude-opus-4-8`",
        ],
        ids=["equals", "parenthetical", "default", "colon"],
    )
    def test_the_correct_id_passes_in_every_phrasing(self, text: str) -> None:
        assert check_docs.check_model_identity("docs/api.md", text, self.DEFAULTS) == []

    def test_the_stale_claim_this_rule_was_written_for_fails(self) -> None:
        """The real defect: api.md said sonnet while config said opus."""
        violations = check_docs.check_model_identity(
            "docs/api.md",
            "model (`ask_model` = `claude-sonnet-4-6`). Each has",
            self.DEFAULTS,
        )
        assert rules(violations) == {"stale-model-id"}
        assert "claude-opus-4-8" in violations[0].message

    def test_a_relationship_phrasing_does_not_fire(self) -> None:
        """Narrow by design — see the docstring on `check_model_identity`.

        This sentence names a field and a different model, but asserts nothing
        about the default. A rule that reds it gets switched off.
        """
        violations = check_docs.check_model_identity(
            "docs/ingestion.md",
            "escalates from `extraction_model` to `claude-sonnet-4-6` on low confidence",
            self.DEFAULTS,
        )
        assert violations == []

    def test_the_real_config_parses(self) -> None:
        config = check_docs.REPO_ROOT / check_docs.CONFIG_SOURCE
        defaults = check_docs.parse_model_defaults(config.read_text(encoding="utf-8"))
        assert defaults["ask_model"] == "claude-opus-4-8"
        assert defaults["matter_classifier_model"] == "claude-sonnet-4-6"
        assert len(defaults) == 8

    def test_every_model_claim_in_the_repo_is_current(self) -> None:
        """The W13 acceptance criterion, as a standing gate."""
        defaults = check_docs.parse_model_defaults(
            (check_docs.REPO_ROOT / check_docs.CONFIG_SOURCE).read_text(encoding="utf-8")
        )
        offenders: list = []
        for path in check_docs.gated_documents():
            relative = str(path.relative_to(check_docs.REPO_ROOT))
            offenders.extend(
                check_docs.check_model_identity(
                    relative, path.read_text(encoding="utf-8"), defaults
                )
            )
        assert offenders == [], [v.render() for v in offenders]


MAP_DOC = """# Architecture

**Status:** active. **Last updated:** 2026-08-12 (x).
**Last verified:** 2026-08-12 — method: read it

## 1.5 Something else

Prose.

## 1.6 Module map

| Package | What |
| --- | --- |
| `src/library/api/` | routers |

| Module | What |
| --- | --- |
| `src/library/models.py` | models |

## 1.7 After

Not part of the map: `src/library/ghost.py`.
"""


def inventory(**modules: int) -> object:
    return check_docs.SourceInventory(
        packages=frozenset({"src/library/api/"}), modules=dict(modules)
    )


class TestModuleMap:
    """The map must name real paths and must not omit the big ones (W24)."""

    def test_a_complete_map_is_clean(self) -> None:
        violations = check_docs.check_module_map(
            "docs/architecture.md", MAP_DOC, inventory(**{"src/library/models.py": 1156})
        )
        assert violations == []

    def test_a_module_over_the_floor_but_absent_fails(self) -> None:
        violations = check_docs.check_module_map(
            "docs/architecture.md",
            MAP_DOC,
            inventory(**{"src/library/models.py": 1156, "src/library/huge.py": 900}),
        )
        assert rules(violations) == {"map-missing-module"}
        assert "huge.py" in violations[0].message

    def test_a_module_under_the_floor_may_be_absent(self) -> None:
        """The floor is a minimum to document, not a ceiling."""
        violations = check_docs.check_module_map(
            "docs/architecture.md",
            MAP_DOC,
            inventory(**{"src/library/models.py": 1156, "src/library/small.py": 120}),
        )
        assert violations == []

    def test_a_listed_module_under_the_floor_is_not_a_violation(self) -> None:
        """Listing more than required must never red the gate."""
        doc_text = MAP_DOC.replace(
            "| `src/library/models.py` | models |",
            "| `src/library/models.py` | models |\n| `src/library/tiny.py` | helper |",
        )
        violations = check_docs.check_module_map(
            "docs/architecture.md",
            doc_text,
            inventory(**{"src/library/models.py": 1156, "src/library/tiny.py": 40}),
        )
        assert violations == []

    def test_a_map_entry_for_a_nonexistent_path_fails(self) -> None:
        doc_text = MAP_DOC.replace(
            "| `src/library/models.py` | models |",
            "| `src/library/models.py` | models |\n| `src/library/gone.py` | renamed away |",
        )
        violations = check_docs.check_module_map(
            "docs/architecture.md", doc_text, inventory(**{"src/library/models.py": 1156})
        )
        assert rules(violations) == {"map-names-missing-path"}
        assert "gone.py" in violations[0].message

    def test_a_missing_package_fails(self) -> None:
        doc_text = MAP_DOC.replace("| `src/library/api/` | routers |", "")
        violations = check_docs.check_module_map(
            "docs/architecture.md", doc_text, inventory(**{"src/library/models.py": 1156})
        )
        assert rules(violations) == {"map-missing-package"}

    def test_a_missing_section_fails(self) -> None:
        violations = check_docs.check_module_map(
            "docs/architecture.md",
            "# Architecture\n\nNo map here.\n",
            inventory(**{"src/library/models.py": 1156}),
        )
        assert rules(violations) == {"no-module-map"}

    def test_the_section_ends_at_the_next_heading(self) -> None:
        """`ghost.py` sits under 1.7 and must not count as a map entry."""
        section = check_docs.extract_section(MAP_DOC, "## 1.6 Module map")
        assert section is not None
        assert "models.py" in section
        assert "ghost.py" not in section

    def test_an_empty_inventory_is_a_violation_not_a_pass(self) -> None:
        violations = check_docs.check_module_map(
            "docs/architecture.md", MAP_DOC, check_docs.SourceInventory()
        )
        assert rules(violations) == {"source-tree-unreadable"}

    def test_the_rule_only_applies_to_its_own_document(self) -> None:
        assert check_docs.check_module_map("docs/api.md", "# API\n", inventory()) == []

    def test_the_real_tree_matches_the_real_map(self) -> None:
        """The W24 acceptance criterion, as a standing gate."""
        found = check_docs.scan_source_tree(check_docs.REPO_ROOT)
        text = (check_docs.REPO_ROOT / check_docs.MODULE_MAP_DOC).read_text(encoding="utf-8")
        violations = check_docs.check_module_map(check_docs.MODULE_MAP_DOC, text, found)
        assert violations == [], [v.render() for v in violations]

    def test_deleting_the_models_row_reds_the_gate(self) -> None:
        """Proves the gate is wired to the real tree, not just to fixtures.

        `src/library/models.py` is the anchor, not `src/library/series.py`
        (retired here after the legacy series stack's deletion left that
        example module itself deleted, which silently defanged this test
        instead of reddening it). `models.py` is chosen because: it sits at
        ~1166 lines, roughly 3x `MODULE_MAP_LINE_FLOOR` (400), so it cannot
        casually drop below the floor and stop reddening the gate; it is
        already `MAP_DOC`'s example, the synthetic fixture the sibling tests
        in this class use, so this real-tree test and its unit-level
        siblings pin the same anchor; and it is about the least deletable
        file in the repo, which is exactly the property this test needs from
        its example after `series.py` demonstrated the failure mode of
        picking one that wasn't.
        """
        found = check_docs.scan_source_tree(check_docs.REPO_ROOT)
        text = (check_docs.REPO_ROOT / check_docs.MODULE_MAP_DOC).read_text(encoding="utf-8")
        without = "\n".join(
            line for line in text.splitlines() if "`src/library/models.py`" not in line
        )
        assert without != text, "the anchor row is gone — pick a still-listed example module"
        violations = check_docs.check_module_map(check_docs.MODULE_MAP_DOC, without, found)
        assert "map-missing-module" in rules(violations)


INDEX = """# Documentation

| [`architecture.md`](architecture.md) | design |
| [`runbooks/deploy.md`](runbooks/deploy.md) | deploys |
"""


class TestDocsIndex:
    """Every gated doc must be reachable from the index (W25)."""

    def test_a_complete_index_is_clean(self) -> None:
        gated = ("docs/architecture.md", "docs/runbooks/deploy.md", "docs/README.md")
        assert check_docs.check_docs_index(INDEX, gated) == []

    def test_an_unlisted_doc_fails(self) -> None:
        gated = ("docs/architecture.md", "docs/smart-groups.md")
        violations = check_docs.check_docs_index(INDEX, gated)
        assert rules(violations) == {"doc-not-indexed"}
        assert "smart-groups.md" in violations[0].message

    def test_the_index_does_not_have_to_list_itself(self) -> None:
        assert check_docs.check_docs_index(INDEX, ("docs/README.md",)) == []

    def test_a_broken_link_fails(self) -> None:
        violations = check_docs.check_index_targets(INDEX, lambda t: t != "architecture.md")
        assert rules(violations) == {"index-link-broken"}
        assert "architecture.md" in violations[0].message

    def test_external_and_anchor_links_are_not_file_claims(self) -> None:
        text = "[a](https://example.com/x.md) [b](#section) [c](other.md)"
        violations = check_docs.check_index_targets(text, lambda t: False)
        assert len(violations) == 1
        assert "other.md" in violations[0].message

    def test_the_real_index_lists_every_gated_doc(self) -> None:
        """The W25 acceptance criterion, as a standing gate."""
        index = check_docs.REPO_ROOT / check_docs.DOCS_INDEX
        gated = tuple(
            str(p.relative_to(check_docs.REPO_ROOT)) for p in check_docs.gated_documents()
        )
        text = index.read_text(encoding="utf-8")
        offenders = check_docs.check_docs_index(text, gated) + check_docs.check_index_targets(
            text, lambda t: (index.parent / t).exists()
        )
        assert offenders == [], [v.render() for v in offenders]


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
