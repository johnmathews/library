# Three drifts, and a gate that only failed in one timezone

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

## 5. The finding: a gate that failed in one timezone and not the other

`check_docs.py` was reporting `docs/migration.md` as stale on this laptop while
`docs-stamps` was green on `main`. The first read — pre-existing, CI-green,
unrelated to this work, leave it — was wrong, and became visibly wrong about an
hour later when the same check started failing under `TZ=UTC` too.

The mechanism. `stale-covered-code` detects drift with
`git log --since=<verified_date> -- <path>`, and git resolves a **bare date** in
the machine's local timezone. `1f5e6d4` (the mypy ratchet) touched `cli.py` and
`importer/`, both declared in `migration.md`'s `Covers:`, at `2026-08-12T16:08Z`.
For a `+0200` laptop, `--since=2026-08-12` means `2026-08-11T22:00Z`, and the
commit is after it. For a UTC runner still on the 12th, it is not yet "since".
The rule therefore fired locally and stayed silent in CI for the better part of
a day, then began firing for everyone at 00:00 UTC.

The silent direction is the dangerous one. A ratchet that reports clean while
covered code has moved on is worse than no ratchet, because the green is read as
a claim that the prose was checked.

There is a second, sharper edge to the same root cause. `git log --date=short`
renders a commit in **its own** stored offset, so a commit authored at
`00:42 +0200` reads as the next day in CI as well. That forces the stamp to the
local date, since `stale-doc-edit` requires `verified >= commit date`. But
`future-date` compares against the runner's `date.today()`, which is UTC. Between
00:00 and 02:00 local, no stamp value satisfies both rules at once, and the only
options are to wait for 00:00 UTC or to eat a red that clears on a re-run. This
session waited.

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

**The rule is still timezone-dependent.** Only the symptom was fixed. Comparing
commit timestamps instead of handing a bare date to `--since` is the actual fix
and is not attempted here.

## 6. Numbers

Backend 1557 passed, 0 skipped — not the 1519/7 recorded elsewhere, because the
golden corpus is present on this machine and those seven skips run as real
parametrised tests. Frontend 1049 passed; lint and type-check clean. `make lint`
clean, and `check_docs.py` exits 0 under both `TZ=UTC` and `+0200`. Deployed:
`/healthz` reports the merge commit.
