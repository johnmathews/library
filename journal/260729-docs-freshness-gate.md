# A staleness gate that can actually detect staleness

W12, the foundation the rest of the docs lane extends. Also the unit where I stopped to check
the plan's numbers first, having been burned four times by not doing that.

## 1. Premises verified before writing anything

The plan claimed the gate would exit 1 "naming exactly the 6 stale and 8 unstamped files". Run
against the tree, the real figures are **8 stale and 7 unstamped** out of a gated set of
**16** — the set size matching the plan exactly.

The divergence is not an error, and the distinction matters more than the numbers: the plan was
written at `64e6acd`, and my own PRs since have edited `ask.md`, `deployment.md`, `roadmap.md`,
`ingestion.md`, `frontend.md`, `jobs-and-notifications.md` and `runbooks/deploy.md`. **I made
those docs staler.** A process that cannot tell "the plan was wrong" from "the plan got old"
will either ignore real errors or churn on harmless drift.

It also improved the acceptance criterion. A fixed count is the wrong assertion because the
count is a moving target *by design* — so `test_repo_docs_report_the_expected_violations`
asserts the *shape* (every gated doc is unstamped or stamped-without-verification) rather than a
number, and flips to expecting zero in W27.

## 2. Not calendar-based, and the test that pins it

The obvious design is "re-verify every 90 days". It is wrong in both directions: it reds
accurate docs in a dormant repo and passes rotten ones in a busy one. Worse, it *teaches
re-stamping on a schedule* — the cheapest way to clear a due list is to bump the date without
re-checking anything, which manufactures precisely the false verification the convention exists
to prevent.

So staleness is driven off change, with two signals:

- **`stale-doc-edit`** — the doc's own last commit is newer than its `Last verified`: someone
  edited it without re-verifying.
- **`stale-covered-code`** — a path in the optional `**Covers:**` field has commits since that
  date: the code moved and nobody re-checked the prose. This is the more valuable signal, and
  `Covers:` has to be *declared* because it cannot be derived.

The test that pins the whole design is the one a calendar gate fails:
`test_an_old_stamp_in_an_untouched_repo_is_clean`. Verified in 2019, nothing committed since,
**green** — because it cannot have drifted.

## 3. The hole this kind of gate usually has

The obvious implementation greps for the three field labels and stops. At which point
`Last verified: 2019-01-01` passes forever, and so does `Last verified: banana`. A staleness
gate that cannot detect staleness is the "check that cannot fail" this project keeps turning up
— and the skill's own reference documents a real instance that survived months in another repo,
asserting three literal labels appeared in the first 15 lines and doing no date arithmetic at
all, while the project's testing doc claimed it "flags ones gone stale past a window".

So: unparseable is red, never skipped. `banana` fails, `2026-13-45` fails (well-shaped,
non-existent), a missing date fails, an empty `method:` fails, a future date fails. Each has its
own test.

`not yet — <reason>` is permitted before first verification, because writing a doc alongside a
feature is legitimate — but it is bounded against `Last updated` at 60 days, so the exemption
expires by itself rather than becoming a legal value forever.

## 4. The shallow-clone guard is the one that matters most

`actions/checkout` defaults to `fetch-depth: 1`. Under it, **every file's `git log -1` returns
HEAD's date**, so every document looks freshly touched and the comparative rule passes
everything, silently, forever. The gate would be decoration and nothing would say so.

**Grading: confirmed against a real shallow clone**, not reasoned about:

```
$ git clone --depth 1 … && git log -1 --format=%ad --date=short -- docs/api.md
2026-07-29        # and roadmap.md, and mcp.md — all the same date
$ python3 scripts/check_docs.py
error: this is a shallow clone, so `git log` dates are meaningless …
exit=2
```

Exit 2, distinct from 1: "cannot check" must never share an exit code with "nothing wrong". The
CI job carries `fetch-depth: 0`, no path filter and no `needs: changes` — the check is over repo
*state*, and a doc goes stale because of a commit that touches no docs at all.

## 5. Deliberately not required yet

`docs-stamps` is `continue-on-error: true` and is **not** in `ci-gate`'s `needs`. The gate reds
today's tree by design, and W27 is the sweep that clears it; requiring a check that cannot pass
would block every merge including the PR that fixes it. W27 adds the wiring once it exits 0 —
the same "prove it reports, then require it" order the CI-gate laws set out.

Also out of scope, and worth naming so its absence is deliberate: a stamp **size** budget.
`docs/api.md`'s `Last updated` line is 1,966 characters of running changelog. That is exactly
the "narrative masquerading as verified current state" the convention warns about, but unwinding
it is its own decision, not a rider on building the gate. It is the reason `Last verified` goes
on its **own line**: appended, the one field a reader needs would be buried in that wall.

## 6. Result

26 new tests, 1453 passing overall, coverage 95%. The gate exits 1 on today's tree naming all 15
violations, exits 2 on a shallow clone, and stays green on an old-but-undisturbed document.
