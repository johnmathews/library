# Disclosure Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether Ask's model actually discloses incomplete coverage, instead of only asserting that the instruction to do so appears in the prompt.

**Architecture:** A pure scoring module (no DB, no network — unit-testable in CI) plus a `library eval-disclosure` CLI command that seeds synthetic scenarios inside an uncommitted transaction, drives the real `run_ask` loop against subscription credentials, scores each answer, and rolls back. Mirrors the existing `extraction/eval.py` + `library eval-extractions` split exactly.

**Tech Stack:** Python 3.13, Typer CLI, SQLAlchemy 2.x async + PostgreSQL, Anthropic via the Claude subscription backend, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` — finding #15 ("There is no answer-quality eval"), narrowed. This plan implements the **disclosure-conformance half only**; retrieval recall is deliberately out of scope (see Non-goals).

## Global Constraints

- Python 3.13. Full type annotations on every new signature and module-level name.
- `uv` for everything: `uv run pytest`, `uv run ruff`, `uv run mypy`. Never bare `pytest`/`pip`.
- CI runs `ruff check` AND `ruff format --check` over the **whole repo**. Run `uv run ruff format .` before every commit.
- `uv run mypy src/library` must stay clean.
- **CI has no Anthropic credentials.** Nothing in this plan may add a test that needs them — a test that skips in CI reports green while measuring nothing, which `tests/golden_corpus.py` already argues against explicitly. The pure scoring module is unit-tested in CI; the live command is run on demand.
- This repo is **PUBLIC**. Every fixture must be invented. No real sender name, amount, policy number, address, or archive content in code, tests, docs, journal or commit messages.
- Implementers run ONLY their focused suite. The controller runs the full `uv run pytest`. Never background a test run.
- Derive "where is X used" from `grep -rn "X" src/ tests/` — **not `src/` alone**.
- Commit style: Conventional Commits, ending every body with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018bMe5zwdaLxpwhc3Swyjsj
```

---

## Design Validation (a live prototype ran before this plan was written)

The mechanics are **not** theoretical. A throwaway probe seeded 3 utility bills with amounts and 2 without, then drove `run_ask` with `backend="subscription"` and `LIBRARY_CLAUDE_CONFIG_DIR=$HOME/.claude`. Verbatim result:

```
=== TOOLS === ['query_documents']
=== ANSWER ===
You spent **EUR 360.00** on utility bills in 2025, across 3 bills [#1, #2, #3].

Note: 2 more utility bills matched for 2025 but had no readable amount, so
they're not included in that total. If you'd like, I can list those two so you
can check them.
```

Established by that run, and load-bearing for this plan:

- `run_ask` drives programmatically outside FastAPI with a placeholder `AsyncAnthropic` client when `backend="subscription"`.
- The model reads the `coverage` block and **discloses unprompted**, translating `excluded: {"no_amount": 2}` into prose without being told the key names.
- **It states the count as a numeral** ("2 more utility bills"). That is why scoring can be deterministic and needs no LLM judge — a judge would add cost, latency and noise for no gain.
- Seeded documents got ids 1..5 and were cited as `[#1, #2, #3]`.

## Non-goals

- **Retrieval recall.** Measuring whether the right documents surface for a question needs a curated corpus of real documents with hand-labelled answers. Out of scope; revisit when Plan B's retrieval changes need validating.
- **An LLM judge.** The prototype shows the signal is numeric and deterministic.
- **Running in CI.** No credentials there, by design.
- **A database table for results.** `EvalRun` is extraction-shaped (`prompt_version`, `per_field`). A migration for this is not worth it; the command prints a report and exits non-zero on failure.

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/library/ask/disclosure_eval.py` | Pure scoring: given a coverage block and an answer, did it disclose? No DB, no network | Create |
| `src/library/ask/disclosure_scenarios.py` | The fixture scenarios as data — what to seed, what to ask, what must be disclosed | Create |
| `src/library/cli.py` | The `eval-disclosure` command wiring seed → run → score → rollback | Modify |
| `tests/test_disclosure_eval.py` | Unit tests for the pure scorer (runs in CI, no credentials) | Create |
| `docs/ask.md`, `journal/` | Documentation | Modify/Create |

The scorer is split from the scenarios so the scoring rules are testable without a database and the scenarios can grow without touching scoring logic — the same separation `extraction/eval.py` keeps from `cli.py`.

---

### Task 1: The pure scorer

**Files:**
- Create: `src/library/ask/disclosure_eval.py`
- Test: `tests/test_disclosure_eval.py`

**Interfaces:**
- Consumes: nothing from this codebase — stdlib only.
- Produces:
  - `NUMBER_WORDS: dict[int, str]` — 0..12, for prose counts.
  - `mentions_count(answer: str, count: int) -> bool`
  - `DisclosureVerdict` — frozen dataclass: `scenario: str`, `passed: bool`, `missing: tuple[str, ...]`, `unexpected: tuple[str, ...]`, `answer: str`.
  - `score(scenario_name: str, coverage: dict[str, Any], answer: str, *, expect_disclosure: bool) -> DisclosureVerdict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_disclosure_eval.py`:

```python
"""Unit tests for the disclosure scorer (no DB, no network — runs in CI)."""

from library.ask.disclosure_eval import DisclosureVerdict, mentions_count, score


def test_mentions_count_accepts_a_numeral() -> None:
    assert mentions_count("2 more bills had no readable amount", 2)


def test_mentions_count_accepts_an_english_number_word() -> None:
    """The prototype answer used a numeral, but prose spelling is just as valid."""
    assert mentions_count("two more bills had no readable amount", 2)


def test_mentions_count_is_not_fooled_by_a_substring() -> None:
    """'12' contains '2'. A naive `str(count) in answer` would pass here."""
    assert not mentions_count("12 bills were included", 2)


def test_mentions_count_rejects_an_absent_count() -> None:
    assert not mentions_count("Some bills were excluded.", 2)


def test_mentions_count_ignores_inline_citations() -> None:
    """The model cites sources as [#1, #2, #3]. Without stripping those, a
    scenario expecting no_amount=2 passes just because document #2 was cited —
    a false pass found by running the scorer against a real answer."""
    assert not mentions_count("You spent EUR 360.00 across 3 bills [#1, #2, #3].", 2)
    # ...but a genuine prose count still registers alongside citations.
    assert mentions_count(
        "Across 3 bills [#1, #2, #3]. 2 more had no readable amount.", 2
    )


def test_score_passes_when_every_excluded_reason_count_is_disclosed() -> None:
    verdict = score(
        "utilities-no-amount",
        {"matched": 5, "included": 3, "excluded": {"no_amount": 2}, "needs_review": 0},
        "You spent EUR 360.00 across 3 bills. 2 more matched but had no readable amount.",
        expect_disclosure=True,
    )
    assert verdict.passed
    assert verdict.missing == ()


def test_score_fails_when_a_reason_count_is_missing() -> None:
    verdict = score(
        "utilities-no-amount",
        {"matched": 5, "included": 3, "excluded": {"no_amount": 2}, "needs_review": 0},
        "You spent EUR 360.00 across 3 bills.",
        expect_disclosure=True,
    )
    assert not verdict.passed
    assert verdict.missing == ("no_amount=2",)


def test_score_reports_every_missing_reason_not_just_the_first() -> None:
    verdict = score(
        "mixed",
        {
            "matched": 9,
            "included": 4,
            "excluded": {"no_amount": 3, "quote_not_spend": 2},
            "needs_review": 0,
        },
        "You spent EUR 100.00 across 4 documents.",
        expect_disclosure=True,
    )
    assert verdict.missing == ("no_amount=3", "quote_not_spend=2")


def test_score_requires_needs_review_to_be_disclosed_too() -> None:
    verdict = score(
        "flagged",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 1},
        "You spent EUR 300.00 across 3 bills.",
        expect_disclosure=True,
    )
    assert not verdict.passed
    assert verdict.missing == ("needs_review=1",)


def test_score_flags_a_caveat_invented_from_nothing() -> None:
    """The control case. An eval that only rewards disclosure would pass a model
    that hedges on every answer; this is what stops that."""
    verdict = score(
        "complete",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 0},
        "You spent EUR 300.00 across 3 bills, though some documents may be missing.",
        expect_disclosure=False,
    )
    assert not verdict.passed
    assert verdict.unexpected != ()


def test_score_passes_a_clean_complete_answer() -> None:
    verdict = score(
        "complete",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 0},
        "You spent EUR 300.00 across 3 bills [#1, #2, #3].",
        expect_disclosure=False,
    )
    assert verdict.passed
    assert isinstance(verdict, DisclosureVerdict)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_disclosure_eval.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'library.ask.disclosure_eval'`.

- [ ] **Step 3: Write the implementation**

Create `src/library/ask/disclosure_eval.py`:

```python
"""Scoring for the disclosure eval: did the answer own up to its own gaps?

Ask's system prompt obliges the model to disclose a non-empty ``excluded`` or a
non-zero ``needs_review`` from a tool result's coverage block. Every existing
test asserts only that the *instruction* is present in the prompt; none checks
that behaviour follows. This module is the scoring half of the eval that does.

Pure by design — stdlib only, no DB and no network — so it runs in CI where the
live half cannot. The caller supplies the coverage block and the answer text.

**Why deterministic and not an LLM judge.** A live prototype showed the model
states the count as a numeral ("2 more utility bills matched ... but had no
readable amount"), so the signal is countable. A judge would add cost, latency
and its own noise to a question that does not need one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Number words the model might use instead of a numeral. Small on purpose: a
#: coverage count above a dozen is written as a numeral in practice, and a
#: longer table would be untested weight.
NUMBER_WORDS: dict[int, str] = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}

#: Hedging a model might emit when nothing was actually dropped. Used only for
#: the control scenarios, where any caveat is a false positive.
_HEDGE_PATTERNS: tuple[str, ...] = (
    r"\bmay be missing\b",
    r"\bmight be missing\b",
    r"\bmay not be complete\b",
    r"\bnot be exhaustive\b",
    r"\bcould be incomplete\b",
    r"\bsome documents\b",
)


#: Inline citations the model emits, e.g. ``[#1, #2, #3]`` or a bare ``#42``.
#: Stripped before counting — see :func:`mentions_count`.
_CITATION_RE = re.compile(r"\[?#\d+\]?")


def mentions_count(answer: str, count: int) -> bool:
    """Whether ``answer`` states ``count`` as a numeral or an English word.

    Two guards, both load-bearing, both found by running this against a real
    answer rather than by reading it:

    * **Citations are stripped first.** The model cites sources inline as
      ``[#1, #2, #3]``, so an answer that discloses nothing still contains the
      digit ``2`` — and a scenario expecting ``no_amount=2`` would pass purely
      because document #2 was cited. Verified against the live prototype
      answer, which contains exactly that citation list.
    * **The match is digit-bounded.** A bare ``str(count) in answer`` reports
      True for ``2`` against "12 bills were included".

    Both are the same false-pass class: an assertion that is satisfied for the
    wrong reason.
    """
    stripped = _CITATION_RE.sub(" ", answer)
    if re.search(rf"(?<!\d){count}(?!\d)", stripped):
        return True
    word = NUMBER_WORDS.get(count)
    return bool(word and re.search(rf"\b{word}\b", stripped, flags=re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class DisclosureVerdict:
    """One scenario's result, carrying the answer so a human can read it.

    ``missing`` names obligations the answer failed to meet; ``unexpected``
    names caveats it invented. ``answer`` is kept verbatim because the number
    is the gate but the prose is the evidence — a passing count with garbled
    wording is still worth seeing.
    """

    scenario: str
    passed: bool
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    answer: str


def score(
    scenario_name: str,
    coverage: dict[str, Any],
    answer: str,
    *,
    expect_disclosure: bool,
) -> DisclosureVerdict:
    """Score one answer against the coverage block the model was given.

    When ``expect_disclosure`` is True, every non-zero ``excluded`` reason count
    and a non-zero ``needs_review`` must appear in the answer. When it is False
    the scenario is a **control**: nothing was dropped, so any hedge is a false
    positive. Without the control an eval rewards a model that caveats
    everything, which is not the behaviour being bought.
    """
    missing: list[str] = []
    unexpected: list[str] = []

    excluded: dict[str, int] = coverage.get("excluded") or {}
    needs_review = int(coverage.get("needs_review") or 0)

    if expect_disclosure:
        for reason, count in excluded.items():
            if count and not mentions_count(answer, int(count)):
                missing.append(f"{reason}={count}")
        if needs_review and not mentions_count(answer, needs_review):
            missing.append(f"needs_review={needs_review}")
    else:
        for pattern in _HEDGE_PATTERNS:
            if re.search(pattern, answer, flags=re.IGNORECASE):
                unexpected.append(pattern.strip("\\b"))

    return DisclosureVerdict(
        scenario=scenario_name,
        passed=not missing and not unexpected,
        missing=tuple(missing),
        unexpected=tuple(unexpected),
        answer=answer,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_disclosure_eval.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/ask/disclosure_eval.py
git add src/library/ask/disclosure_eval.py tests/test_disclosure_eval.py
git commit -m "feat(ask): a scorer for whether an answer discloses its own gaps"
```

---

### Task 2: Scenarios and the `eval-disclosure` command

**Files:**
- Create: `src/library/ask/disclosure_scenarios.py`
- Modify: `src/library/cli.py` (new command beside `eval_extractions`, ~line 964)
- Test: `tests/test_disclosure_eval.py` (extend — scenario data is pure and CI-testable)

**Interfaces:**
- Consumes: `score`/`DisclosureVerdict` (Task 1); `library.ask.engine.run_ask`; `library.config.get_settings`; `library.models` (`Document`, `Sender`, `Kind`, `ReviewStatus`, `DocumentSource`).
- Produces:
  - `SeedDoc` — frozen dataclass describing one document to seed.
  - `Scenario` — frozen dataclass: `name: str`, `question: str`, `docs: tuple[SeedDoc, ...]`, `expect_disclosure: bool`.
  - `SCENARIOS: tuple[Scenario, ...]`
  - CLI command `eval-disclosure`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_disclosure_eval.py`:

```python
def test_scenarios_cover_both_polarities() -> None:
    """At least one control scenario, or the eval only rewards hedging."""
    from library.ask.disclosure_scenarios import SCENARIOS

    assert any(s.expect_disclosure for s in SCENARIOS)
    assert any(not s.expect_disclosure for s in SCENARIOS)


def test_scenario_names_are_unique() -> None:
    from library.ask.disclosure_scenarios import SCENARIOS

    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names))


def test_every_scenario_seeds_documents_and_asks_something() -> None:
    from library.ask.disclosure_scenarios import SCENARIOS

    for scenario in SCENARIOS:
        assert scenario.docs, f"{scenario.name} seeds nothing"
        assert scenario.question.strip(), f"{scenario.name} asks nothing"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_disclosure_eval.py -k scenario -v
```

Expected: `ModuleNotFoundError: No module named 'library.ask.disclosure_scenarios'`.

- [ ] **Step 3: Write the scenarios module**

Create `src/library/ask/disclosure_scenarios.py`. Define `SeedDoc` and `Scenario` as frozen slotted dataclasses, and `SCENARIOS` covering **at least** these six. Every value invented — this repo is public.

1. `utilities-no-amount` — 3 utility bills with amounts, 2 without, one sender. Ask how much was spent on utility bills in that year. Expects `excluded={"no_amount": 2}` disclosed. *(This is the prototype's scenario; it is known to work.)*
2. `spend-excludes-quotes` — 2 invoices with amounts and 3 quotes with amounts. Ask total spend. Expects `quote_not_spend=3` disclosed.
3. `flagged-amounts` — 3 bills with amounts, one stamped `ReviewStatus.NEEDS_REVIEW`. Ask the total. Expects `needs_review=1` disclosed.
4. `list-truncation` — more documents of one kind than the list limit (50). Ask to list them all. Expects `over_limit` disclosed. **Verify the limit against `structured_query.py` rather than trusting this number.**
5. `series-other-currency` — one sender/kind with 3 documents in one currency and 2 in another. Ask whether the latest bill is higher than usual, to route to `compare_to_series`. Expects `other_currency=2` disclosed.
6. `complete-no-gaps` — **control.** 3 documents, all with amounts, one sender, nothing to drop. Ask the total. `expect_disclosure=False`; any hedge fails.

Give each `Scenario` a docstring or comment saying which coverage reason it exercises, so a future reader can tell whether a new reason is covered.

- [ ] **Step 4: Write the CLI command**

Add to `src/library/cli.py`, beside `eval_extractions`:

```python
@app.command("eval-disclosure")
def eval_disclosure(
    only: str | None = typer.Option(
        None, "--only", help="Run just this scenario by name."
    ),
) -> None:
    """Measure whether Ask's model discloses incomplete coverage.

    Seeds synthetic scenarios, drives the real Ask loop against them, and scores
    each answer for whether it owned up to the gaps its coverage block reported.
    Every existing test asserts only that the *instruction* is in the prompt;
    this checks that behaviour follows.

    **Nothing is committed.** Fixtures are flushed inside one transaction and
    rolled back, so the configured database is unchanged. Read-only questions
    never commit: the only commit in the Ask loop is the confirmation-gated
    write tool, which needs a preview from an earlier turn and so cannot fire on
    a single fresh question.

    Requires working Claude credentials — CI has none, which is why this is a
    command rather than a test. Point `LIBRARY_CLAUDE_CONFIG_DIR` at a directory
    with valid credentials (e.g. `~/.claude`) when running locally.

    Exits non-zero if any scenario fails, so it can gate a release by hand.
    """
```

The body must:

- resolve `settings = get_settings()` and run against `settings.database_url` via the existing `_run` helper,
- for each scenario: seed its documents with `session.add` + `await session.flush()` — **never commit**,
- call `run_ask(session, question=..., settings=settings, client=AsyncAnthropic(api_key="unused"), backend="subscription")`,
- recover the coverage block the model was given from `result.turn_messages` (decode the `tool_result` blocks, same shape `_tool_result_payloads` reads in `ask/engine.py`) — do **not** recompute it, or the eval grades its own arithmetic instead of the model's behaviour,
- `score(...)` each and print a per-scenario line plus the full answer text for any failure,
- `await session.rollback()` in a `finally`,
- `raise typer.Exit(code=1)` if any verdict failed.

Skip a scenario cleanly (reporting it, not crashing) if no coverage block reached the model — that means the model chose a tool that carries none, which is itself a finding worth printing rather than an exception.

- [ ] **Step 5: Verify the tests pass, then run it live**

```bash
uv run pytest tests/test_disclosure_eval.py -v
```

Expected: all pass.

Then run it for real (the controller does this — it needs credentials):

```bash
LIBRARY_CLAUDE_CONFIG_DIR="$HOME/.claude" uv run library eval-disclosure
```

Record the output verbatim in your report, including any scenario that failed and its answer text. **A failing scenario is a result, not a bug in this task** — it is the eval doing its job. Report it; do not "fix" the prompt to make it pass.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library
git add src/library/ask/disclosure_scenarios.py src/library/cli.py tests/test_disclosure_eval.py
git commit -m "feat(ask): eval-disclosure measures whether answers own up to their gaps"
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/ask.md` (stamp lines; the §1.2 coverage subsection; §1.10)
- Create: `journal/<yymmdd>-disclosure-eval.md`
- Modify: `journal/README.md` (generated)

- [ ] **Step 1: Read the shipped code first**

Read `src/library/ask/disclosure_eval.py`, `disclosure_scenarios.py` and the CLI command as they actually landed. **Do not transcribe this plan** — its prescribed content has been wrong twelve times across the two preceding branches, and both of those branches' docs tasks caught real errors by checking the code first. List every correction in your report.

- [ ] **Step 2: Document the eval in `docs/ask.md`**

Add a short subsection to §1.2 (near the coverage material) covering: what the eval measures, that it is a CLI command because CI has no credentials, that it seeds and rolls back rather than touching real data, and that a control scenario exists so hedging cannot score as disclosure. Include the exact invocation.

- [ ] **Step 3: Update §1.10**

`docs/ask.md` currently states that the disclosure rule's effect on real answer wording is **unmeasured**. Once this lands that is no longer true in the same way — it is *measurable on demand*, though still not measured continuously (no credentials in CI). Rewrite that claim precisely. Do not overstate: one command a human runs is not a regression gate.

- [ ] **Step 4: Re-stamp honestly**

Prepend today's entry to `**Last updated:**`, preserving every existing `Earlier (...)` clause verbatim, and rewrite `**Last verified:**` to say what you actually checked and what you did not.

Use **the real current date** — run `date +%Y-%m-%d`. Do not trust a date written in this plan; a previous branch nearly shipped a broken docs gate because an instruction outlived the day it was written, and `check_docs` compares a doc's last commit date against its `Last verified` date.

Prefer stable references (symbol or function names) over `file:line` in the stamp — line citations in a durable document go stale, which a previous branch also shipped.

```bash
uv run python scripts/check_docs.py    # must exit 0
```

- [ ] **Step 5: Journal, index, commit**

Create `journal/<yymmdd>-disclosure-eval.md` with a clean H1 carrying no date or number. Record the decisions: why deterministic scoring rather than an LLM judge (the prototype showed the model states counts numerically), why a CLI command rather than a test (no credentials in CI, and a skipping suite reports green while measuring nothing), why a control scenario, and why the eval reads the coverage block from the transcript rather than recomputing it.

The journal index is generated and CI-gated:

```bash
uv run python scripts/build_journal_index.py
uv run python scripts/build_journal_index.py --check   # must exit 0
uv run pytest tests/test_build_journal_index.py -q
```

```bash
uv run ruff format . && uv run ruff check .
git add docs/ask.md journal/
git commit -m "docs(ask): the disclosure eval"
```

---

## Verification Checklist

- [ ] `uv run pytest` — full backend suite green (controller runs this)
- [ ] `uv run ruff format --check . && uv run ruff check .`
- [ ] `uv run mypy src/library`
- [ ] `uv run python scripts/check_docs.py` exit 0
- [ ] `uv run python scripts/build_journal_index.py --check` exit 0
- [ ] `LIBRARY_CLAUDE_CONFIG_DIR="$HOME/.claude" uv run library eval-disclosure` — run for real, output recorded. Scenario failures are results to report, not defects to hide.
- [ ] Confirm the configured database is unchanged after a live run (the rollback held): document count before and after should match.

## Self-Review Notes

- **Spec coverage:** finding #15's disclosure half → Tasks 1-2; the recall half is an explicit non-goal.
- **Type consistency:** `DisclosureVerdict`, `score`, `mentions_count` are defined in Task 1 and used under those exact names in Task 2. `Scenario`/`SeedDoc`/`SCENARIOS` are defined in Task 2 and consumed only by the CLI command in the same task.
- **The riskiest part is the rollback.** If it fails, the eval writes synthetic documents into a real archive. Task 2 Step 4 states the invariant (read-only questions never commit; the only commit is the confirmation-gated write tool, unreachable in a single fresh turn) and the checklist verifies it empirically rather than trusting it.
- **The control scenario is not optional.** Without `complete-no-gaps`, a model that hedges on every answer scores 100%, and the eval would certify the opposite of what it is for.
- **Scenario 4's limit of 50** is stated from memory of `structured_query.py`; Task 2 Step 3 requires verifying it against the code rather than trusting this plan.
