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

## Follow-up: making it a runtime setting, and turning it on

Two changes after the first pass, both requested once the numbers were on the
table: make the backend configurable from the UI rather than only by env var,
and ship `ask` defaulting to `subscription`.

**A runtime setting invalidates the startup validator.** The original design
failed fast at boot when a surface asked for OAuth without a mounted credentials
directory. That check cannot survive an editable setting: the backend can become
`subscription` long after boot, so a startup check misses the real case — and
worse, it would refuse to start a container during the §5.1 recovery, which is
exactly when starting matters most. The guard moved to write time (`set_backend`
refuses a backend that cannot authenticate; the API returns 409 with the reason)
and to `/healthz`. That is a better place for it anyway: it reaches the admin
making the change, at the moment they make it.

**New: `instance_settings` (migration 0030).** Library had two kinds of
configuration — per-user display preferences and startup env vars — and needed a
third: instance-wide *and* mutable without a restart. Deliberately key/value
with a JSON value, because a typed column per toggle costs a migration each
time. It is an **override layer**: a missing row means "use the `Settings`
value", so an empty table behaves exactly as the environment says, which is what
every existing deployment gets on upgrade.

**Resolution moved to the callers.** `resolve_backend(session, surface,
settings)` is called by the API route and the job, which then pass `backend=`
down to `run_ask` / `describe_series` / `generate_thread_title`. An earlier
revision had `run_ask` resolve it internally; that made it a second database
round-trip per turn (the route already needed the value for its 503 check) and
broke every test that called `run_ask` with `session=None`. Passing it in is
both cheaper and consistent with the other two call sites.

**One UI bug worth recording.** The refusal path called `loadLLMBackends()` to
re-read what is actually stored — and that function clears `llmError` first, so
the 409's message was wiped before it could render. The reason a refusal gives
is the entire value of the 409 (it names the command to run on the host), so
losing it would have made the failure look arbitrary. Caught by the test that
asserts the server's detail reaches the screen; fixed by setting the error
*after* the reload.

**`ask` now defaults to `subscription`.** The suite pins both surfaces back to
`api` via an autouse fixture in `conftest.py` — the subscription path shells out
to the bundled CLI, so without that pin a new test touching Ask would make real
calls against real credentials. The test asserting the shipped default therefore
reads `Settings.model_fields[...]` rather than an instance, since the fixture
would otherwise mask it.

## Taking it to production, and what that found

The first half of this entry describes code. This half describes finding out
whether it worked, which turned out to be a different activity.

### Provisioning, and two wrong instructions

The docs (and my advice) said to run `claude setup-token`, on the strength of a
note about a much older CLI. That is wrong twice over: `setup-token` mints a
long-lived token and **prints** it — it writes no credentials file and does not
log the CLI in, so `claude auth status` still reports `loggedIn: false`
afterwards. Since library reads the credentials file, following the instruction
leaves the backend unusable. The command that works is
`claude auth login --claudeai`, which is fine headless: it prints a URL and
takes the code back over `ssh -t`. **Confirmed** — both commands were run on
the deploy host and the difference observed directly.

The deploy host also had no Claude CLI at all, and the credentials the CLI
writes are owned by the invoking user (root) while the container runs as uid
999 — so `chown 999:999` is required and easy to miss.

### Three defects, each found only by running it

None of these were reachable from the test suite as written, because every test
stubs `query()`. All three now have regression tests **confirmed to fail against
the previous code**.

1. **`CLAUDE_CONFIG_DIR` is an override, not a hint.** Setting it unconditionally
   made the CLI look *only* there, so a directory with no credentials file broke
   working auth. Bisected against the live API. Now set only when the file exists.
2. **Raising inside the `async for`** abandoned the SDK's async generator, so
   every real error surfaced as `RuntimeError: aclose(): asynchronous generator
   is already running` — burying the cause. Failures are now raised after the
   stream drains.
3. **`/healthz` was blind to the runtime toggle.** It keyed on
   `settings.ask_llm_backend` (the environment default) while the live value
   comes from the database, so enabling the backend through the Settings UI —
   the intended path — left the credential alarm permanently silent. Observed in
   production: Ask answering on the subscription while `/healthz` said nothing
   about credentials. Now keyed on the credentials existing, which keeps the
   endpoint free of database access.

A fourth came from the automated security review: credentials were written with
`Path.write_text`, and since the atomic rename makes the new file's mode win,
every refresh (~8-hourly) silently widened a file holding an access token *and*
a refresh token. Under a permissive umask the test showed `0o666`.

### The unhappy path was the weak spot

Asked whether a credential-less backend fails clearly, the answer was no on three
counts: a bare 500, the SDK's own advice pointing at `/login` (meaningless in a
container), and — had it been translated naively — auth being blamed for every
failure including CLI crashes and rate limits. The common thread with the three
defects above is that **all of it lives in code that only runs when something is
already broken**, which is exactly where stubbed tests prove nothing.

### Measured cost

A real two-tool Ask turn in production: **131,966 input tokens**. The ~32k figure
from the original spike is a floor *per API call*, and a tool loop makes several
— the Claude Code preamble is re-sent each step. Sizing the decision on the floor
understates it roughly fourfold. `cost_usd` records ~$0.67 for such a turn, which
is deliberately *not* the saving: it counts harness tokens the API backend never
sends, so the honest comparison is against a much smaller API-side figure.

### An out-of-repo dependency

`/srv/apps/docker-compose.yml` is templated by the `document_library_lxc` ansible
role in `home-server`. Every host change made here — the credential mount,
`LIBRARY_CLAUDE_CONFIG_DIR` — lived only in a file ansible regenerates, so the
next run would have silently removed them and broken Ask. Ported into the role
(home-server#68), verified by rendering the template and diffing it against the
running host file. **Confirmed**: applying the role is now a no-op for library.

The same PR removed a duplicated `LIBRARY_ANTHROPIC_API_KEY` from both services'
`environment:` blocks. It shadowed `env_file: .env` from the same vault variable,
so changing the vault value would not have reached the containers.

I initially reported that duplication as a security exposure ("world-readable
key"). That was wrong and was retracted: root is the only account with a login
shell on that host, and every container that can reach the file runs as root and
could read the 0600 `.env` anyway. The file mode granted nobody anything. The
real problem was the shadowing, which is a maintenance foot-gun, not a leak.

## What is deliberately not done

1. **`CLAUDE_CODE_OAUTH_TOKEN` support.** `setup-token`'s long-lived token would
   remove the refresh machinery entirely — no 8-hourly rotation, no single-use
   race, no revoked-refresh-token outage mode. Started, then dropped: the
   file-based path already worked, and I had the variable name wrong
   (`ANTHROPIC_AUTH_TOKEN`) which is itself evidence I was building on a guess.
   Worth revisiting deliberately.
2. **Series descriptions on the subscription.** Enabled by the user through the
   UI. At ~62 documents/month this costs roughly fifteen Ask turns' worth of
   quota — tolerable. It becomes dangerous under bulk import (the paperless-ngx
   importer, or a backfill): ~1,000 documents would be ~32M tokens in a burst,
   exhausting quota and taking Ask down with it. Not gated in code; documented
   instead, which is a deliberate choice to avoid a mechanism nobody asked for.
3. **A duplicated sentence in the credential-failure message.** The login command
   appears twice — once from the health detail, once from the wrapper. Cosmetic,
   and not worth its own deploy cycle.
