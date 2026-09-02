# A disclosure rule that spans a comparison

**Date:** 2026-09-02
**Branch:** `et/ux-issues-20260902`
**Issue:** [#155](https://github.com/johnmathews/library/issues/155) (first half)

## 1. What went

Ask's coverage-disclosure rule governs **one** tool result. It says: if a
result's `excluded` is non-empty, say so, with the reason and the count. It says
nothing about what happens when the model makes two aggregate calls and compares
them — which is exactly how a comparative question gets answered now that
`compare_to_series` is gone.

So this is expressible today, and every rule in the prompt is satisfied by it:

- 2025: `total=1200`, `excluded={}`
- 2026: `total=960`, `excluded={no_amount: 5}`

Each side discloses correctly in isolation. "Spending fell 20%" is the
composition of the two, and the composition is governed by nothing. The fall is
a fact about extraction, not about spending.

The prompt gains a **separate** bullet: before stating any comparison,
difference, percentage or trend, check whether the results have different
`excluded` reasons or counts, and if they do, name the difference and say which
side is affected. Separate rather than folded into the existing bullet, because
folded in, both a reader and the model read the whole thing as being about a
single result — which is the failure it exists to correct.

`Coverage` is untouched. It still partitions one matched set
(`included + sum(excluded) == matched`), which is what makes a single answer
testable. A coverage block that genuinely *spans* a comparison is a property of
the comparative tool, and that tool is still the open half of #155.

## 2. The scenario, and what it can and cannot tell you

`comparative-uneven-coverage` seeds one sender across two years: 2024 complete
at 4 × 300.00, 2025 at 3 × 320.00 with three further bills carrying no amount.
The readable totals show a 20% fall in a year whose bills went **up** — the
dropped bills at 2025's own rate more than close the gap.

That arithmetic is the whole point of the fixture, and none of it is implied by
the documents merely existing, so it is pinned by a test rather than left to a
comment. Raising one 2025 amount to 800.00 was confirmed to red it.

Two limits, stated because a green run here means less than it looks:

- `cli._coverage_from_turn_messages` merges every coverage block a turn
  produced, per reason, taking the **maximum**. That is what lets the existing
  regex scorer see the asymmetry with no scorer change — and equally what stops
  it distinguishing "disclosed the asymmetry between the periods" from
  "disclosed the exclusion at all". A model that names the three bills and still
  calls the fall a trend passes.
- The scorer is a screen, not a judge. It reds on silence, which is the failure
  #155 describes. It does not grade the caveat.

## 3. What is NOT verified, and why that is in the stamp

**The eval has never been run.** `library eval-disclosure` needs Claude
subscription credentials and a configured database; this work was done in a
worktree with neither. So:

- **Verified:** the prompt has no cross-call rule (read end to end, and
  `grep -i 'compar\|trend\|across calls'` over `engine.py` returns only
  unrelated comments). The new bullet exists and is pinned by a test confirmed
  to red without it. The scenario's arithmetic is pinned by a test confirmed to
  red when mutated.
- **Not verified:** that the model actually produced a misleading comparison
  before this change, or that it stops after it. Both need a live run.

The finding this closes was graded [SUSPECTED] for that reason and stays there.
`docs/ask.md` §1.2 and its stamp both say so outright rather than letting a
shipped rule imply a measured one.

## 4. One assumption the work falsified

The first draft of the new test carried a rationale that turned out to be
wrong. It claimed the neighbouring
`test_disclosure_rule_names_the_coverage_reporting_tools` would catch a
misordering, because that test slices from the first rule line containing
"coverage" and a new coverage-mentioning bullet placed earlier would capture the
slice.

Moving the bullet above the per-result one and running the suite falsified it:
the sibling test stayed **green**, and only the new test reded. This bullet's
first *line* does not contain the word "coverage" — it reaches `excluded` on its
second line — so the sibling never sees it.

The rationale was corrected rather than deleted, because the hazard is real for
a differently-worded bullet; what changed is that the new test now asserts the
bullet index explicitly instead of relying on a neighbour that cannot see it.
Cheap to check, and it would have shipped as a confident false comment.

## 5. Files

- `src/library/ask/engine.py` — the new rule, placed after the per-result one.
- `src/library/ask/disclosure_scenarios.py` — the scenario; and its module
  docstring, which still claimed `not_summable_kind` and `duplicate_payment`
  need "a shape `SeedDoc` does not yet express". `docs/ask.md` had already
  retracted that; the docstring had not, and now says what is actually true —
  both are seedable and simply unwritten.
- `tests/test_api_ask.py`, `tests/test_disclosure_eval.py` — the two guards.
- `docs/ask.md` — §1.2's second obligation, the sixth scenario, §1.10 item 11
  narrowed to the missing *tool*, and a stamp that separates executed from
  unexecuted.
