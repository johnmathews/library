# Ask subscription lockdown and cost accounting

> Purpose
>
> Records run `manual-20261107T190322Z`. It started as a look at a live Ask conversation's
> cost and found that the Ask agent's CLI offered the owner's claude.ai account connectors
> (Gmail, Slack, Drive and more) under `bypassPermissions`. It covers that fix, the overhead
> it removed, the per-turn cost accounting added after it, and what was left out.

**Date:** 2026-11-07
**Branch:** `worktree-eng-ask-subscription-lockdown`

## 1. What was found

All of these were observed on the production container (CLI 2.1.237, SDK 0.2.142).

1. **The CLI offered far more than library's tools (confirmed).** The CLI's `init` message
   listed 23 built-ins outside `_BLOCKED_BUILTINS` (`RemoteTrigger`, `CronCreate`, `Workflow`,
   `SendMessage`, `PushNotification`, `Skill` and others). It also listed every connected
   claude.ai connector, including Gmail `send_message` and `forward`, Slack
   `slack_send_message`, Drive `share_file`, Home Assistant `ha_manage_backup` and home-finances
   write tools. `setting_sources=[]` does not remove account connectors. Ask runs with
   `bypassPermissions` and reads ingested email, which is text a stranger can write.
2. **No stored turn ever used them (confirmed).** Every `tool_use` block stored in `ask_turns`
   names one of library's own tools. That covers 71 turns, 22 of them on the subscription. The
   block builder records every `ToolUseBlock`, so a foreign call would have been stored.
3. **The tool descriptions were the whole "harness tax" (confirmed).** A "Reply OK" call used
   102,494 input tokens on Opus and 125,580 on Haiku. With `tools=[]`,
   `strict_mcp_config=True` and `skills=[]` it used 271 and 240. The docs had said the 43k
   preamble "cannot be configured away". The August measurement varied the system prompt and
   `setting_sources`, but not the tool set.
4. **The recorded cost overstated even the API price (confirmed).** The subscription path priced
   cache reads at the full input rate. One six-turn conversation recorded about $9.90; the API
   rate card puts the same tokens at about $6.10.
5. **The arithmetic depended on Prometheus (confirmed).** `ask_turns` stored no backend, token
   split or call count, so explaining that conversation needed the `library_ask_tokens_total`
   counter. It matched the database to the token.

## 2. What shipped

| Unit | What |
| --- | --- |
| W1 | `build_options` offers only library's tools: `tools=[]`, `strict_mcp_config=True`, `skills=[]`, blocklist kept as a second layer. `tests/test_llm_subscription_cli.py` starts the real bundled CLI with no credentials and a dead API address and asserts the `init` tool list. |
| W2 | Migration 0043: `ask_turns.backend`, `fresh_input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `model_calls`; `backend` on the Ask response and thread turns. |
| W3 | Subscription cost uses the API rate card (reads 0.1x, 5-minute writes 1.25x, 1-hour writes 2x); the Ask page labels it "API-equivalent". |
| W4 | `library.ask.input_tokens_per_call` histogram, to catch the per-call cost growing again. |

The same PR carries two items held for "the next Library PR": the MCP-hold docs commit from the
`eng-mcp-hold-reason` worktree, and `.playwright-mcp/` in `.gitignore`.

## 3. Decisions

1. **One PR for all four units.** The user chose this to keep GitHub CI runs down, and to leave
   the exposure open a few more hours rather than switch Ask to the API in the meantime. Every
   CI job was run locally before the push instead: backend with coverage and the OCR/golden
   floors, typecheck, docs-stamps, lint, frontend, image build, compose-smoke, and all three e2e
   shards.
2. **The backfill fills `backend` only where it is provable.** It sets `api` before the release
   that added the subscription backend (2026-08-20 14:13 UTC), and the stored setting's value
   from its `updated_at` on, because every save moves `updated_at`. Anything else is null. In
   production, previewed read-only: 48 `api`, 22 `subscription` and 1 null.
3. **`model_calls` on the subscription is `ResultMessage.num_turns`.** A live tool loop with two
   sequential tool calls reported `num_turns = 3`, matching three model requests (confirmed).
4. **The CLI test cannot catch a leaking connector.** Without credentials there is no account to
   load connectors from. `strict_mcp_config` covers that case, pinned as an option and measured
   live (an empty MCP list).

## 4. Local CI on a Mac

The pinned TEI embedder image has no `linux/arm64` manifest, so a local `compose-smoke` fails
before it starts. A one-service override (`platform: linux/amd64`) emulates the embedder. No
service depends on it, so the smoke checks run normally.

## 5. What is deliberately not done

1. **No re-pricing of old turns.** Their token split was never stored, so they keep the
   full-rate figure; the docs say so.
2. **No alert rule.** It belongs in the home-server repo, next to the other Library alerts. The
   suggested trigger is a median `input_tokens_per_call` above ~50k.
3. **No change to Ask's model, tool-round limit or history length.** These were worth measuring
   once the lockdown had removed the overhead. They were not worth changing on top of it.
4. **No live re-measurement of a full Ask turn after the lockdown.** The two-call tool-loop probe
   used 2,034 input tokens with the lockdown options. A real Opus turn will be visible in the
   new histogram after deploy.
