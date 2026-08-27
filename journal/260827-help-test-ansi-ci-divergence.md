# A CLI help test that only rich's colour decision could fail

**Date:** 2026-08-27
**Branch:** `plan-b-retrieval-reach` (PR #99)

## What happened

PR #99's first CI run went red on a single test out of 1841:

    FAILED tests/test_cli.py::test_eval_recall_help_lists_ask_and_write_baseline
    AssertionError: assert '--only' in '\x1b[1m ... '

The same test, the same commit, the same lockfile, passed locally — including a
deliberate full-suite run immediately before pushing. That gap is the whole
point of this entry.

## Why it diverged

The test asserted `"--only" in result.output` against Typer's `--help`, which
rich renders. Two things combine:

1. rich styles its output when it detects a terminal **or a CI environment**,
   and renders plain text otherwise. Locally the runner is not a tty, so the
   help came back as plain characters and the substring check passed. On CI it
   came back styled.
2. Typer's `OptionHighlighter` carries two patterns, and both match the same
   token: `--only` as a long option, and `-only` as a short switch. The spans
   overlap, so rich closes and reopens a style between the two hyphens. The
   rendered bytes read `-\x1b[...m-only`, and the literal substring `--only`
   is no longer present.

So the assertion was not testing "help lists `--only`". It was testing
"help lists `--only` *and* rich chose not to style", and only the second half
varied. `--ask` and `--write-baseline` were broken the same way; the test just
never got past the first assertion to say so.

Ruled out first, in this order: terminal width (CI's console is 80 columns,
measured off the box-drawing border in the log — identical to local), Python
version (CI 3.14, local 3.13 — reproduced the render on both, both fine), and
package drift (local venv rebuilt against the lockfile — typer 0.26.7, click
8.4.1, rich 15.0.0 on both, still passed). The full suite passed locally in a
CI-matching environment, which is what pointed at the environment rather than
the code. `FORCE_COLOR=1` reproduced it locally on the first try.

## The fix

`_unstyled()` strips CSI and OSC escapes, and the assertions run against that
rather than the raw bytes. Two tests cover it: one under the ambient
environment, whatever the machine happens to do, and one with styling forced on
so the CI rendering is exercised on a developer's machine. A third test pins
`_unstyled` itself against known styled samples, so the helper cannot rot
silently.

Verified by mutation: restoring the original un-stripped assertion fails with
CI's exact message under forced styling, and passes without it.

## The second CI failure, and what it taught

The first attempt at this fix parametrised the test over "styling off" and
"styling on" and asserted that rich had honoured the requested mode. That went
red on CI too, on the *off* case:

    assert ("\x1b[" in result.output) is styled
    E   assert (... '\x1b[1m ...') is False

`NO_COLOR` suppresses **colour**, not styling — rich still emits bold and dim,
which is exactly what CI's output carried. There is no portable way to demand
escape-free output, so the "styling off" direction was dropped: the ambient
test now makes no claim about how rich rendered.

Forcing styling *on* turned out to need care as well. `FORCE_COLOR=1` alone is
not enough, because rich refuses to style a dumb terminal — with `TERM=dumb` in
the ambient environment the forced case failed locally. The test now pins
`FORCE_COLOR`, `NO_COLOR` and `TERM` together, and was re-checked under five
ambient combinations (`FORCE_COLOR=1`, `NO_COLOR=1`, `TERM=dumb`, `CI=true`,
and `TERM=dumb NO_COLOR=1`) before being pushed.

The pattern in both failures is the same one that caused the original bug: an
assertion about *rendered* output smuggles in a claim about the environment.
The fix is to assert on the de-styled text and to pin every environment input a
test does depend on — not to guess which one CI happens to set.

## Worth remembering

This was the only `--help` assertion in the suite, which is why nothing caught
the pattern earlier. Any future one should assert on de-styled text.

The general shape is the more useful lesson: a test whose subject is *rendered*
output can pass locally and fail in CI without anything about the code
differing, because the renderer reads the environment. A green local suite is
evidence about the local environment, not about the change.
