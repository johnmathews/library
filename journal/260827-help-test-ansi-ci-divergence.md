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

`_unstyled()` strips CSI and OSC escapes, and the test is parametrised over
both of rich's rendering modes — `NO_COLOR=1` and `FORCE_COLOR=1` — so the CI
rendering is exercised on a developer's machine. It also asserts that the
environment was actually honoured (`("\x1b[" in output) is styled`), so the
styled case cannot quietly decay into a second copy of the plain case if rich
ever changes how it decides.

Verified by mutation: restoring the original un-stripped assertion fails the
`[styled]` case with CI's exact message while `[plain]` still passes.

## Worth remembering

This was the only `--help` assertion in the suite, which is why nothing caught
the pattern earlier. Any future one should assert on de-styled text.

The general shape is the more useful lesson: a test whose subject is *rendered*
output can pass locally and fail in CI without anything about the code
differing, because the renderer reads the environment. A green local suite is
evidence about the local environment, not about the change.
