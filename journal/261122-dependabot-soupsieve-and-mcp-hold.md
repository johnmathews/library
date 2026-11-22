# Dependabot: soupsieve 4, and the mcp hold re-checked

> Purpose
>
> Records run `manual-20261122T214003Z`, which took `main` from 9 open Dependabot alerts
> to 0. Four were fixed by upgrading soupsieve. Five mcp alerts were dismissed under the
> existing hold, after checking that the new high-rated one does not reach the server.

**Date:** 2026-11-22
**Branch:** `eng-dependabot-nov22`

## 1. What was open

All nine were Python, in `uv.lock`, and all arrived after the 2026-11-07 run
(journal `261107-dependabot-alerts`):

| Package | Alerts | Highest | Fix |
| --- | --- | --- | --- |
| soupsieve 2.9.2 | 181, 182, 183, 184 | high | 4.0.0 (182); 3.0.2 or 2.10 for the rest |
| mcp 1.28.1 | 89, 174, 175, 176, 185 | high (185) | 1.30.0, which is held |

## 2. soupsieve 2.9.2 → 4.1.3

The 2026-11-07 run capped soupsieve at 2.9 because the alerts then needed no
more. GHSA-mmhr-3c5m-f64f now needs 4.0.0, so this run crosses two majors.

- **Why it is safe.** beautifulsoup4 4.15 asks only for `soupsieve>=1.6.1`. The 3.0 and 4.0
  breaking changes are all selector semantics: strict `:has()`, custom-selector order, and
  `template`/`iframe` handling. Library never runs a selector. `html_to_markdown` only parses
  and decomposes, and markdownify does not call `select` either.
- **So the alerts were not reachable.** All four are about compiling or matching a selector.
  The upgrade is still worth having: it is lockfile-only, and it removes a trap for anyone who
  later adds `soup.select()` on email HTML, which is attacker-controlled.
- **Checked.** 8 test files covering HTML-to-Markdown, email ingest and docx: 214 passed on
  4.1.3. The full suite runs in CI.

## 3. The mcp hold, re-checked

`docs/mcp.md` §1.2 holds mcp on 1.28.1 and lists the triggers for lifting it. This run
checked the two that could have fired:

- **"An alert lands that does reach this server."** Alert 185 (GHSA-qx49-fqc8-xw99,
  high) is in `mcp.client.auth`: a malicious MCP server could steer a *client's* OAuth
  credentials to its own token endpoint. Library uses `OAuthClientProvider` only in
  `tests/test_mcp_oauth.py`, against its own server, and the advisory lists "MCP servers
  built with the SDK" as not affected. Not fired.
- **A way to raise the 4 MiB cap.** fastmcp-slim 4.2.4 (2026-11-21) still has no
  `max_request_body_size`. Not fired.

mcp 1 has had no release since 1.30.0 (2026-09-07), but the November advisories still name
1.30.0 as the 1.x fix, so "mcp 1 stops getting security fixes" has not clearly fired either.
Watch that one.

## 4. Dismissing instead of holding open

The 2026-11-07 run left the four held mcp alerts open. That meant the list never reached zero,
and alert 185 arrived rated high and needed a manual pass to classify. All five are now
dismissed as tolerable risk, each with a comment pointing to `docs/mcp.md` §1.2. A new mcp
alert shows up on its own, and the question to ask is the same: does it reach a server?
