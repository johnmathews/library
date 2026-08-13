# Forty errors from one getattr, and a ratchet that finally fails downward

**Date:** 2026-08-13. Follow-up to [260812-mypy-ratchet.md](260812-mypy-ratchet.md),
which introduced the mypy quarantine and wrote down the two things wrong with it.
Both are now closed, and the first turned out to be far smaller than its own
comment claimed.

## 1. The largest quarantine entry was a one-line bug

`library.ask.engine` carried 40 of the 98 real errors — every one of them
`union-attr`, every one on `response.content[i]`. The override's exit note read:

> Exit: narrow once at the top of each tool loop into a typed local, rather than
> 40 asserts.

That describes a refactor. The actual cause is one comparison:

```python
for block in response.content:
    if getattr(block, "type", None) != "tool_use":
        continue
    used.append(block.name)          # ← 10 errors, one per union member
```

mypy narrows a discriminated union on `block.type == "tool_use"`. It cannot
narrow through `getattr`, so every access after the guard still saw the full
12-member union — `.name`, `.input` and `.id` producing ten errors each.

Replacing it with `block.type != "tool_use"` removed all 40. Measured before
and after, with the quarantine lifted:

| | before | after |
| --- | ---: | ---: |
| `library.ask.engine` | 41 | 1 |
| whole tree | 70 | 30 |

**The `getattr` was also the weaker code on its own terms.** Every member of the
SDK's content union carries `type`, so the default could never fire; it bought
nothing and cost the narrowing. The comment now in its place says so, because
the next person to reach for `getattr` on a union is the person this cost.

The remaining error was `engine.py:417` (`attr-defined`): `query_documents`
returns `dict[str, object]`, so `result["rows"]` is not iterable to mypy. Fixed
by declaring the shape the function already returns — a `QueryResult` TypedDict
in `structured_query.py`, whose three branches all match it — rather than
casting at the call site. The whole `library.ask.engine` override is gone.

One wrinkle worth recording: mypy rejects returning a TypedDict where
`dict[str, Any]` is declared (a caller could insert a key the TypedDict
forbids), so `_dispatch_tool` widens with `dict(...)`. That is a real constraint,
not a workaround — and it is one line with a comment rather than a `cast`.

## 2. The one-directional ratchet had already rotted

The second known weakness, quoted from the config it was written in:

> unlike the docs ratchet, this one only fails in one direction. mypy has no
> baseline mechanism, so if a module is cleaned up the override lingers silently
> instead of failing to force the gain to be locked in.

It had not stayed theoretical. Measuring the tree against the numbers those
comments carried:

| Module / code | comment claimed | actual |
| --- | ---: | ---: |
| `series_insight` union-attr | 13 | 11 |
| `series` arg-type | 7 | 7 |
| `email_ingest` arg-type | 2 | 3 |
| `ask.engine` arg-type | 4 | 0 |

Three of four were wrong in three different directions — one had improved, one
had regressed, one had been fixed entirely. **Nothing was comparing the numbers
to anything**, which is the same finding as the documentation sweep, one file
over.

`scripts/check_mypy.py` closes it. It regenerates the mypy config *from*
`pyproject.toml` with the `disable_error_code` overrides dropped, runs mypy
against that, counts errors per (module, code), and compares to
`mypy-baseline.json`. Three ways to fail:

- **a rise** — a new error of a quarantined class;
- **a fall** — types improved and the gain was not locked in;
- **zero** — the override no longer suppresses anything, which is the case that
  motivated the unit: a dead entry disables an error class for a whole module
  while appearing to cost nothing.

### 2.1 Deriving the config rather than restating it

The measurement needs mypy's real settings minus the quarantine. Copying those
settings into the script would create exactly the drift this unit exists to
kill, so the script reads `[tool.mypy]` from `pyproject.toml` and renders it as
an INI config, dropping overrides that carry `disable_error_code` and keeping
the `ignore_missing_imports` block — untyped third-party packages are someone
else's packaging, not a suppression of ours. A settings change in
`pyproject.toml` reaches the measurement run with no edit here.

The quarantine's *shape* is derived the same way: `baseline_gaps()` compares
`pyproject.toml`'s overrides against the baseline's keys **in both directions**,
so neither an override without a number nor a number without an override can
exist. The second direction was added after the first end-to-end failure demo
produced a message claiming a module "disables `[no-any-return]`" when
`pyproject.toml` said no such thing — a correct failure with a message that sent
the reader to the wrong file.

### 2.2 Shown failing, three ways

Per the standing rule that a gate never shown to fail is decoration, each branch
was reded against the real tree, not only in unit tests:

```
baseline 6, actual 7  -> FAIL: library.series [arg-type] rose to 7 from the baseline of 6.
baseline 9, actual 7  -> FAIL: library.series [arg-type] is 7, BELOW the baseline of 9.
code with 0 errors    -> FAIL: library.series disables [no-any-return] but it no longer
                                suppresses anything.
restored              -> ok: quarantine suppresses exactly 29 error(s), matching the baseline
```

### 2.3 Refusing to measure a run that did not happen

The script's own silent-failure mode: if mypy never type-checks, its stdout is
empty, every count reads zero, and `ratchet_verdicts` reports — confidently and
in detail — that all three overrides are dead and should be deleted. A gate that
converts its own breakage into advice is worse than one that fails, so
`require_real_run` accepts exit codes 0 and 1 (clean, and errors-found, which is
the expected one here) and raises on anything else.

Shown working against the two failures that reach it — a `files` path mypy
cannot read, and a malformed INI — both of which exit 2 with empty stdout.

**What it does not cover, checked rather than assumed:** an *unrecognized*
option does not exit 2. mypy warns on stderr and carries on, so a typo'd setting
would be silently ignored. That is mypy-wide behaviour and applies equally to
the primary `uv run mypy` step, so it is not a hole this script opened — but the
guard should not be described as covering "a bad config" without the
qualification. The first version of this entry did; the probe corrected it.

**One measurement error worth recording, because it nearly became the
baseline.** The first run reported `import-not-found` in 50-odd modules and a
different count in every quarantined one. Cause: the probe ran under system
`python3`, so `sys.executable -m mypy` resolved outside the project venv and no
dependency was importable. Numbers taken from a run that cannot import the code
it is checking are not small errors — they are a different measurement
altogether, and they looked plausible enough to commit. The script runs mypy via
`sys.executable`, which is correct under `uv run`; the CI step invokes it that
way.

## 3. What is left, and who now guards it

29 errors across three modules: `series_insight` (11 `union-attr`), `series`
(10, mostly `arg-type`), `email_ingest` (8). The first two share one root cause
the old comments already named — `SeriesSummary` fields typed `| None` that are
non-None by the time the formatter runs — and the exit is a genuine type split,
not a one-liner, so it stays quarantined deliberately. `email_ingest`'s is
deferred for a stated reason: the uid guard would change what the live email
poller does on a path with no test.

The difference is that the numbers are now enforced rather than described. The
per-module comments no longer carry counts at all — they name the cause and the
exit, and point at `mypy-baseline.json` for the arithmetic. Whoever does the
`SeriesSummary` split will find the build red until they lower the baseline,
which is the entire point.

## 4. A drift found in passing

`docs/runbooks/deploy.md` §1.2 said branch protection was "**not yet** pointed at
`ci-gate`", and told the reader to trust the run's result over the badge. The
`main` ruleset has required `ci-gate` since 2026-07-28 — active, no bypass
actors, deletion and force-push blocked. The doc was written before the ruleset
existed and nothing re-checked it. Corrected, and the `typecheck` job's second
step documented there in the same pass; the stamp says the verification was
partial and scoped to §1.2 rather than claiming a fresh end-to-end deploy.
