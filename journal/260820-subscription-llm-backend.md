# Routing Ask through the Claude subscription

**Date:** 2026-08-20

## Context

Library pays per token on every LLM call. `homelab-sre/sre-agent` reaches Claude
through a Claude subscription instead, so the question was whether library could
do the same. Two candidate reference projects were named; only one was actually
doing it.

## What the reference projects actually do

**`homelab-sre/sre-agent` — yes.** Not the `anthropic` SDK with different
credentials: it uses `claude-agent-sdk`, which bundles the Claude Code CLI binary
in its wheel and shells out to it. The CLI reads the OAuth credentials
`claude setup-token` writes. Subscription access comes from *being Claude Code*.
Its journals document four hard-won details, all of which we inherited:
`ANTHROPIC_API_KEY` must be blanked in the subprocess env (the CLI ranks it above
the credentials file and sends it as `X-Api-Key`); access tokens expire ~8-hourly
with no headless self-refresh; refresh tokens are single-use; and a revoked
refresh token is terminal and needs a human.

**`/Users/john/projects/journal` — no.** Its server constructs
`anthropic.Anthropic(api_key=...)` across ~10 providers from `ANTHROPIC_API_KEY`.
Same metered API as library. The belief that it used the subscription was wrong.

## Why this could not be a wholesale port

Six of library's eight LLM call sites use `client.messages.parse(output_format=…)`
— server-side schema-validated structured output. **The Agent SDK has no
equivalent.** Extraction, the extraction judge, repair, markdown generation, email
labelling and matter classification would all have to fall back to prompting for
JSON and parsing it hopefully, discarding the schema enforcement the extraction
pipeline's correctness rests on. Two of them also send page images as content
blocks, which the SDK's prompt surface cannot take without writing files to disk
and re-enabling the built-in file tools this integration deliberately blocks.

That leaves `ask` (already a tool-use loop — the same shape sre-agent runs) and
`series_insight` (a plain text call).

## The spike, and the number that changed the plan

Before writing any code, three assumptions were checked against the live
subscription — the same discipline sre-agent's own migration used.

All three passed: non-Haiku models are reachable through the SDK (an OAuth token
sent straight to `/v1/messages` gets Haiku only); base64 image blocks survive
streaming-input mode, so Ask's attachments work; and in-process MCP tools are
registered and called.

The fourth finding was not an assumption we set out to test. For a prompt of
`"Reply with exactly: OK"`, with every built-in tool disallowed:

| Model | Real input | Context sent | Notional cost |
| --- | --- | --- | --- |
| `claude-sonnet-4-6` | 3 tok | 32,231 tok | $0.121 |
| `claude-opus-4-8` | 2 tok | 43,320 tok | $0.813 |

A second spike established it is irreducible: a custom `system_prompt` does not
replace the Claude Code preset, it costs five tokens more (32,239 vs 32,234), and
`setting_sources=[]` changes nothing.

That is a *fixed* per-call tax, which inverts the case for `series_insight`: it
runs on the cheapest model with a deliberately bounded prompt, once per ingested
document, so routing it through the SDK would spend ~32k of shared quota to avoid
a fraction of a cent — and starve Ask during an ingest burst. It ships behind a
switch that defaults to `api`, so the trade can be measured rather than assumed.

## What was built

New `src/library/llm/` package:

- `oauth.py` — refresh-before-call with a 5-minute buffer, an `asyncio.Lock`
  against single-use-token races, atomic writes, and an `invalid_grant` flag keyed
  on a hash of the token so re-authentication self-clears the alarm. Ported from
  sre-agent, minus its Prometheus gauges.
- `subscription.py` — the SDK adapter: `text_call` and `tool_loop`. All Claude
  Code built-ins blocked, `setting_sources=[]` so the host's `CLAUDE.md` cannot
  leak into library's prompts, and the `ANTHROPIC_API_KEY` blank set in exactly
  one place because a rebuild that dropped it caused sre-agent's outage.

Two independent switches (`LIBRARY_ASK_LLM_BACKEND`,
`LIBRARY_SERIES_INSIGHT_LLM_BACKEND`), both defaulting to `api` so a deploy is a
no-op until credentials are provisioned. Per-surface rather than global because
the trade genuinely differs per call site.

`run_ask` was restructured into setup → backend branch → shared tail, with the
existing API loop moved verbatim into `_run_api_turn`.

### Three decisions worth recording

1. **Transcripts stay in Anthropic block form on both backends.** Ask's
   preview-then-confirm write gate re-reads stored `tool_use` blocks to decide
   what a later turn may edit, so the SDK path reconstructs the same vocabulary
   from its message stream. Without this, flipping the switch would silently
   disable the write gate on follow-up turns. There is a test that feeds a
   subscription-produced turn back through `_ids_from_history`.

2. **`cost_usd` counts cache-creation and cache-read tokens.** Under a
   subscription no dollars are billed, so the figure means "what this would have
   cost on the API". Counting only fresh input would report 3 tokens for a call
   that put 32k on the wire — making the backend look free when the real resource
   being spent is quota. A subscription turn therefore records a *higher*
   `cost_usd` than the same turn on the API, which is the honest number.

3. **Thread titles follow `ask_llm_backend`.** Splitting them off would leave a
   "subscription" deployment still needing an API key for a handful of tokens.
   The cost is one harness-taxed call per *new thread*, not per turn.

## Two bugs the stubbed tests could not have found

Every unit test replaces `query()` with a stub. That is right for testing
library's side of the contract, but it means nothing had exercised the actual
wiring. Running the real adapter against the live subscription found two defects
immediately — and both now have regression tests that were confirmed to go red
against the buggy code.

1. **`CLAUDE_CONFIG_DIR` is an override, not a hint.** `build_options` set it
   unconditionally. Setting it makes the CLI look *only* in that directory, so
   naming a directory with no credentials file turns working auth into
   "Not logged in · Please run /login" — which is exactly what happens on macOS,
   where credentials live in the Keychain. Bisected against the live API: with
   the variable, error; without it, fine; `cwd` was innocent. Now set only when
   `.credentials.json` is actually present, which is the deployment case.

2. **Raising inside the `async for` masked every real error.** Abandoning the
   SDK's async generator mid-iteration made the unwind fail with
   `RuntimeError: aclose(): asynchronous generator is already running`, which
   buried the actual cause under a traceback about generator teardown. The
   failure is now recorded and raised after the stream drains; since the result
   message is the last thing the SDK yields, deferring costs nothing.

The same run measured what a real turn costs: **134,962 input tokens** for one
two-tool Sonnet question. The 32k floor is per API call, and a tool loop makes
several — the preamble is re-sent each iteration. That figure is in the docs,
because sizing the decision on the floor would understate it by ~4×.

## Not done

Nothing was switched on. Both knobs ship defaulting to `api`, and enabling either
requires `claude setup-token` on the deploy host first — `Settings` fails fast if
a backend is `subscription` and the credentials directory is not mounted.

The licensing question is flagged in `docs/llm-backends.md` §8 and not resolved:
Anthropic's consumer subscription terms cover the Claude apps and Claude Code, not
use as a billing backend for a self-hosted service.
