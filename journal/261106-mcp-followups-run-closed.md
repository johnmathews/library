# MCP follow-ups run closed

> Purpose
>
> Closes the MCP follow-ups run (engineering-team run of 2026-11-05/06). It
> lists what shipped and where, the decisions taken, what was proven live,
> two hardening fixes the closing review found, and what is deliberately
> left. The unit entries of 2026-11-05/06 hold the detail.

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-wrapup`

## 1. What shipped

| Unit | What | PR | Live |
| --- | --- | --- | --- |
| W1 | CI time limits, apt retries, e2e skipped on docs-only changes | #211, #212 | CI only |
| W2 | MCP tool text for files, ingest and edits | #210 | yes |
| W3 | `mcp_rejected` log line for refused `/mcp` requests | #210 | yes |
| W4 | Money facet filters count the money rows that carry the facet | #213 | yes |
| W5 | Migration 0041: the counted row carries its twins' labels | #217 | yes |
| W6 | Ask's `query_documents` refuses unknown filter values | #215 | yes |
| W7 | `ANALYZE` after the amount backfill and the import | #214 | yes |
| W8 | Apt retries switch from the Azure mirror to archive.ubuntu.com | #218 | CI only |

The unit entries are `261105-mcp-client-guidance`,
`261106-ci-time-limits-and-docs-only-e2e`, `261106-money-line-facets`,
`261106-money-planner-statistics`, `261106-ask-filter-vocabulary`,
`261106-charts-twin-labels` and `261106-ci-apt-mirror-fallback`.

## 2. Decisions

- **D1:** twin labels are merged in the view. The alternative, choosing a
  twin within each chart's filter, was rejected because it would make
  stacked series overcount.
- **D2:** document search keeps document-level facet matching. Only money
  totals became line-aware.
- **D3:** totals and charts read the same merged labels. This was asked
  mid-unit, after a read-only production count showed no current total
  would move.

The handover had already settled three things, and this run did not
reopen them: the 30-day sliding refresh window, no `ask_library` tool, and
one shared archive for the household.

## 3. Proven live (confirmed)

- **Docs-only PRs skip e2e.** The docs-only PR (#212) logged
  `e2e=skipped expect.e2e=false`, and its gate passed. The code PR (#211)
  logged `e2e=success expect.e2e=true`.
- **The retry works.** On its first live run it killed a slow apt attempt
  at 300 s, and the next attempt finished in 59 s.
- **The first-request 400 is a protocol probe.** A hosted client's first
  request was logged as `mcp_rejected status=400 method=server/discover
  protocol_version=2026-07-28`: the MCP 2026-07-28 discovery probe, which
  the installed SDK refuses before the client falls back.
- **Migration 0041 behaves in production.** It applied, and the view marks
  5 payments as conflicting, matching a pre-deploy count. The one saved
  chart computes in 0.33 s.

## 4. Found along the way

- **The same-runner retry was not enough.** After W1, three shards in two
  runs failed all three apt attempts on the same runner, and one held a
  deploy back. W8 followed: retries now switch mirrors. That switch has not
  yet been seen firing live.
- **Two performance traps in the money views, both caught by timing probes
  rather than tests:**
  - a correlated `EXISTS` over `spend_facts`, which made W4's first draft
    about 5× slower;
  - a `jsonb_each` whose 100-row estimate tipped money statements into
    JIT, adding about 320 ms each in W5's first draft.

  Both were fixed before merge.
- **The deploy ran the migration twice (confirmed).** On the 0041 deploy,
  compose's recreate started a hash-prefixed `library-migrate` copy beside
  the real one. One applied 0041; the other exited 255 ("expected to match
  one row … 0 found"). The database was correct because the DDL is
  transactional, but `deploy.sh` reported FAILED. A non-transactional
  migration would have run twice for real.
- **Two sessions deployed at once.** Another session deployed the same
  `main` commits minutes before this one. That was harmless here, but two
  overlapping `deploy.sh` runs would race.

## 5. Closing review fixes (this branch)

An independent review of the run's merged code found two Low issues in
`McpRejectedLog`. Both are fixed here, and each test was seen red first.

1. **Deeply nested JSON.** A body like `[[[…` raised `RecursionError` from
   the `finally` that writes the line, after the response had gone out. The
   parsers now treat it as unreadable, and any fault while writing the line
   is logged at debug and dropped.
2. **Control characters.** ESC and NUL in `method` or `protocol_version`
   passed through `str.split` and reached the log raw. Anything outside
   printable ASCII now becomes `_`.

A documentation audit of the run found nothing stale. It added migration
0041 and the new modules to the `Covers:` lines and to the architecture
module map.

## 6. What is deliberately not done

- **Fixing the double migration in `deploy.sh`.** The fix would be one
  explicit `docker compose run --rm migrate`, then `up --no-deps`. That
  script belongs to the deploy work another session is changing (#216), so
  the finding is recorded here and in the repo's working notes. It should
  be fixed before any non-transactional migration ships.
- **An MCP SDK upgrade** to serve 2026-07-28 natively (mcp 2.x, fastmcp
  4.x). That needs its own run.
- **MCP elicitation for edits.** The transport and the hosted clients do
  not support it yet.
- **One shared twin-ranking builder** for the view and `sum_amount`.
- **The home-server `nfs_client` group mismatch** (handover item 8). It is
  in another repo and was not picked.
