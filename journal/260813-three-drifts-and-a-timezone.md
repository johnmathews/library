# Three drifts, and a staleness rule that answers differently before and after lunch

**Date:** 2026-08-13. Follow-ups to the verify-and-stamp sweep, plus one thing
the sweep could not have found.

Three defects the sweep surfaced and deliberately left out of scope, and seven
merged branches GitHub had not deleted. Small, independent, and — the reason
they were worth doing as a set — each one a case of a confident written claim
that had drifted from the code. Verifying them before acting turned out to
matter: one of the four was already half-done, and a fifth problem appeared
mid-session that nobody had reported at all.

## 1. The server that hid a tool from its own clients

`SERVER_INSTRUCTIONS` in `mcp_server.py` is the text an MCP client receives on
connect — for an LLM client it is the entire map of what the server can do. It
named five `list_*` tools for discovering filter values and omitted
`list_matters`, one of the eleven registered tools. `search_documents` has
accepted a `matter` parameter the whole time. A client following the server's
own instructions could not find it.

`docs/mcp.md` already documented both, because the sweep had corrected it. So
this is the inversion of the usual failure: the prose was right and the code was
wrong. The sweep fixed the description of the behaviour and left the behaviour
alone, which is exactly the half of the job a documentation pass is structurally
able to do.

Nothing asserted anything about that string — `grep -n instructions tests/test_mcp.py`
returned nothing — so there was no mechanism by which the omission could have
been caught. `test_instructions_mention_every_tool` now requires every tool in
the **live registry** to appear in the instructions. Deliberately not the test
file's own `EXPECTED_TOOLS` constant: that set is maintained by hand alongside
the string, so checking one against the other would let both drift together. A
newly registered tool now trips the test on its first run.

Mutation-tested, per the standing rule that a guard never shown to fail is
decoration: deleting `list_matters` from the string again reds it.

## 2. "The four tabs", clicking five

`admin-views.spec.ts` said "the four tabs" in its docstring and its test title
while its body clicked through five, which is also how many `AdminView.vue`
defines. `docs/frontend.md` already said five. Pure wording, no behaviour, and
worth recording only because of where the stale count *wasn't* fixed: the
matching strings in `CHANGELOG.md` and `260628-admin-role-and-views.md` were
left alone. Both are historical records of a time when there really were four
tabs. A changelog that gets retrofitted to present truth stops being a changelog.

## 3. The anti-flash script that caused the flash

`index.html` runs a small inline script before Vue boots whose only purpose is to
put `body.sidebar-expanded` in its final state, so the shell paints once instead
of settling. It read `localStorage['sidebar-expanded']`.

`AppSidebar.vue` writes `library:sidebar-expanded`, and reads the bare key only
as a read-once legacy fallback for preferences stored before the `library:`
convention. So for any user without a legacy value — which is to say almost
everyone — the seed guessed, and the sidebar visibly settled a frame later.

The fix mirrors the store's precedence: primary key, then legacy key, then
`matchMedia('(min-width: 1024px)')`.

Including that third level was the one real decision here. The old script's
fallback was "collapsed"; the store's fallback is the viewport check, which on
any desktop returns expanded. That is a second, independent mismatch, and a
worse one than the key-name bug: it fires for every visitor who has no stored
preference at all — a first-time user, or anyone on a new browser. The most
common way to meet the app was also the reliably-flashing one. Mirroring only
two of the three levels would have fixed the narrow reported bug and left the
wider one in place.

The guard executes the real script rather than reading it. `sidebar-seed.spec.ts`
pulls the inline script out of `index.html`, runs it under each combination of
stored keys and viewport width, and asserts the resulting body class — the thing
the user perceives — not the source text, which could be rewritten a dozen ways
without changing behaviour. Reverting the script to its previous legacy-only
lookup reds four of its five tests.

## 4. The branches, mostly already gone

Seven branches were listed as needing deletion. Five had already been cleaned
up; only the two `worktree-eng-*` ones survived. Both PRs were `MERGED` —
confirmed through PR state rather than git ancestry, since squash-merge makes
`git merge-base --is-ancestor` report false for every merged branch. `origin`
now carries `main` alone.

## 5. The finding: a staleness rule that answers differently before and after lunch

`check_docs.py` was reporting `docs/migration.md` as stale on this laptop while
`docs-stamps` was green on `main`. The first read — pre-existing, CI-green,
unrelated to this work, leave it — was wrong, and became visibly wrong about an
hour later when the same check started failing under `TZ=UTC` too.

**The explanation written into that session's commit message was also wrong**,
and is worth correcting here rather than quietly restating, because it is the
same error the sweep exists to catch: a confident mechanism inferred from two
data points and never tested. It claimed the bare date passed to `--since` is
resolved at local midnight, so the rule would differ between a `+0200` laptop
and a UTC runner and flip once at 00:00 UTC. The timezone is involved, but that
is not the mechanism, and the real one is worse.

Git's `approxidate` fills the fields a date string leaves unspecified from **the
current clock**, not from midnight. So `--since=2026-08-12` does not mean "since
the start of the 12th". It means "since the 12th at whatever time it is right
now, locally". Demonstrated in a scratch repo with two commits on the same day
and the check run at 11:27 UTC:

```
commit at 2026-08-12T05:00Z   --since=2026-08-12  ->  not reported
commit at 2026-08-12T20:00Z   --since=2026-08-12  ->  reported
```

The cutoff was 11:27 on the 12th — the wall clock, pasted onto the requested
date. The consequence is not a one-off flip at midnight UTC. It is that the rule
**oscillates daily and permanently**: `1f5e6d4` landed at 16:08Z, so a run before
16:08 local time reports the drift and a run after it does not, every day,
indefinitely. Yesterday's session happened to observe the two sides of that
oscillation an hour apart and mistook them for a single midnight transition.

The silent direction is the dangerous one. A ratchet that reports clean while
covered code has moved on is worse than no ratchet, because the green is read as
a claim that the prose was checked — and here "clean" was never a stable state
to begin with, just the afternoon half of a coin flip.

A second, genuinely separate defect sits next to it, and this half of yesterday's
analysis did survive testing. `git log --date=short` renders a commit in **its
own** recorded offset — verified across `UTC`, `America/New_York` and
`Europe/Amsterdam`, all returning the same day for a `+0200` commit — so a commit
authored at `00:42 +0200` reads as the 13th on every machine. `stale-doc-edit`
therefore requires a stamp of the 13th. But `future-date` compares against the
runner's `date.today()`, which on a UTC runner is still the 12th. For those two
hours no stamp value satisfies both rules at once. This session waited for 00:00
UTC rather than eat a red that clears on a re-run.

`migration.md`'s prose needed no change — all three edits in that commit are
behaviour-preserving refactors (a narrowing alias, a hoisted empty-checksum
guard, and the tag dedupe rewritten as an explicit loop to stop shadowing `tag`).
Rather than re-read the whole document to justify a fresh stamp, the three claims
that actually rest on the changed lines were re-verified — the MD5 one-retry
sentence against `download_original_verified`, the five `import paperless` flags
against `cli.py`, the first-occurrence-wins tag dedupe in `map_document` — and
the `method:` string says exactly that. A stamp is a claim about work performed;
"partial re-verification, scoped to X" is a true claim, and "read in full" would
not have been.

**Only the symptom was fixed** in that session — `migration.md` got a stamp, and
the rule that failed to catch it was left alone. The actual fix is to stop
passing a bare date to `--since` and instead compare the last commit date per
covered path, which `git_last_commit_date` already computes deterministically;
`future-date` needs a day of slack so the two-hour dead zone closes. That work
follows this entry.

## 6. Numbers

Backend 1557 passed, 0 skipped — not the 1519/7 recorded elsewhere, because the
golden corpus is present on this machine and those seven skips run as real
parametrised tests. Frontend 1049 passed; lint and type-check clean. `make lint`
clean, and `check_docs.py` exits 0 under both `TZ=UTC` and `+0200`. Deployed:
`/healthz` reports the merge commit.
