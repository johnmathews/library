# A map of the code, and three ways to find things

**Date:** 2026-08-12. **Units:** W24, W25 (batch I of the library-defect-generators run).

Both units are about the same failure: documentation that exists but cannot be
found, or that promises something it does not deliver.

## 1. W24 — the module map

`docs/README.md` tells a new reader to open `architecture.md` first, and
advertises it as covering "module layout". It did not. A cold reader following
the documented path hit a promise the document did not keep — so this fixes a
false claim, in the one file most likely to be read first.

§1.6 is one table of the 8 packages, one of the 14 top-level modules at ≥300
lines, and a closing sentence about the single-purpose helpers below that. The
descriptions are each module's own docstring first line, not paraphrases.

### 1.1 The floor is 400, and the map documents ~300

The plan proposed `MODULE_MAP_LINE_FLOOR = 300` and the handover flagged the
risk: `config.py` is 334 lines, close enough to churn. Measuring the actual
distribution made the problem sharper than that — the sizes below 372 are packed
tight:

```
512 -> 372   140-line gap
372 -> 371     1
371 -> 360    11
360 -> 334    26
334 -> 299    35
```

`email_label.py` is at **299**. A 300 floor puts it into the mandatory set the
moment anyone adds a single line, redding CI for a change that means nothing.
350 is no better — it lands between 334 and 360.

There is exactly one wide gap: 512 → 372. So the gate's floor is **400**, in the
middle of it, where crossing means a module genuinely grew.

The map still documents everything ≥300, because more documentation helps a
reader and the floor is a **minimum, not a maximum**. A listed module is never a
violation for being small — only for naming a path that does not exist. That
separation is what lets the floor be chosen for stability and the map for
usefulness, instead of forcing one number to do both jobs.

### 1.2 Two directions

A map rots two ways, so the rule checks both: naming a path that no longer
exists (a rename went through without the doc), and omitting a package or an
over-floor module (the codebase grew past the map).

## 2. W25 — findability

**`smart-groups.md` was not in the docs index.** It was reachable only through a
footnote in `roadmap.md`, despite declaring itself authoritative over another
document. Added, plus a rule that every gated doc appears in the index and every
index link resolves.

**The journal had no index** — 134 entries reachable only by guessing filenames.
`scripts/build_journal_index.py` generates `journal/README.md` from each entry's
H1, grouped by month, newest first. `--check` has the same contract as
`ruff format --check` and runs in `make lint` and the `docs-stamps` CI job: add
an entry, regenerate, commit both.

**Journal *cadence* is deliberately not gated.** "No entry in N days" is a
calendar gate wearing a different hat — it reds on legitimately quiet weeks and
teaches writing an entry to clear a check. It stays a convention.

The generator raises rather than skips on a malformed entry. A skipped entry
would produce an index that looks complete and is not, which is this repo's
signature defect. `--check` on an empty or malformed journal exits **2**, not 1:
"cannot check" must never share an exit code with "nothing wrong".

Writing it turned up one real bug in my own rule: the first slug pattern rejected
`260611-v0.1.0-build-complete.md`, because I had not allowed dots. A version
number is a perfectly good descriptive name; the pattern was wrong, not the file.

**CHANGELOG backfill.** `README.md` advertised `CHANGELOG.md` as "the full list"
while seven shipped features were missing from it: Smart Groups, business
matters, document comments, the two-screen Ask, PDF unlock at ingest, the email
skip audit, and `git_sha` on `/healthz`. All seven are now in `[Unreleased]`.

Per the owner's decision recorded in the plan, there is **no comparative
CHANGELOG gate** — it would force a CHANGELOG line into every feature PR.
Instead `README.md` now says "notable changes", which is what the file honestly
is. Narrowing the claim is a real fix: the defect was the mismatch, and either
side could move.

## 3. Verification

Four mutations, each shown to red its guard:

- deleting the `series.py` row → `map-missing-module`
- renaming a mapped module away → `map-names-missing-path`
- un-indexing `smart-groups.md` → `doc-not-indexed`
- hand-editing `journal/README.md` → `--check` exits 1

`scripts/check_docs.py` still reports **exactly 15** violations, so the CI
ratchet baseline is untouched and W27 still has to take it to 0.
