# LLM backends

**Status:** active. **Last updated:** 2026-11-07 (§3 rewritten: the CLI now offers only library's tools — `tools=[]`, `strict_mcp_config=True`, `skills=[]` — which closes access to the claude.ai account's connectors and cuts the fixed cost of a call from ~100k tokens to a few hundred; §7 says how it is tested). Earlier entries are in this file's history.
**Last verified:** 2026-11-07 — method: partial, scoped to §3 and §7's tool note. §3.1 figures are live calls from the production container, before and after the lockdown options; the after-lockdown tool list is asserted against the real bundled CLI by `tests/test_llm_subscription_cli.py`, seen red against the old options. §3.1.1 is read from `ask_turns` and the `library_ask_tokens_total` counter, which agreed to the token. Earlier verification is in this file's history.

> **Purpose** — Library can reach Claude two ways: the metered Anthropic
> Messages API, or a Claude subscription via the bundled Claude Code CLI. This
> explains which surfaces may use which, why the batch pipeline may not, and how
> to provision and operate the subscription path.

## 1. The two backends

| | `api` | `subscription` |
| --- | --- | --- |
| Package | `anthropic` | `claude-agent-sdk` |
| Transport | `POST /v1/messages` | Claude Code CLI subprocess |
| Auth | `x-api-key` from `LIBRARY_ANTHROPIC_API_KEY` | OAuth from `.credentials.json` |
| Billed as | Dollars per token | Subscription quota |
| Structured outputs | Yes (`messages.parse`) | **No** |

The second row is the whole mechanism. Subscription access is not a different
credential you can hand to the Messages API — it comes from *being the Claude
Code CLI*. `claude-agent-sdk` bundles that CLI in its wheel and shells out to
it; the CLI reads the OAuth credentials `claude auth login` writes.

An OAuth subscription token sent directly to `/v1/messages` reaches Haiku only —
Sonnet and Opus are refused. Going through the CLI is what unlocks them.

## 2. Which surfaces can use it

**One** — `ask`, the only surface library has that is *not* built on structured
outputs:

| Surface | Environment default | Shipped default |
| --- | --- | --- |
| `ask` tool loop + thread titles | `LIBRARY_ASK_LLM_BACKEND` | `subscription` |

There were two until 2026-08-31: `series_insight` (`LIBRARY_SERIES_INSIGHT_LLM_BACKEND`,
defaulting to `api`) generated the one-line description shown above a legacy
series chart, and it went with the rest of the series stack. **The mechanism did
not go with it.** `BACKEND_SURFACES` in `src/library/llm/backends.py` is still a
map, the settings view still iterates it, and a second row is still the whole
cost of making another surface switchable — the table is one row long because
only one surface qualifies today, not because the layer collapsed into a
single-purpose switch.

That value is a **default**, not the control. An admin changes the live value in
**Settings → LLM backend** (§4), which takes effect on the next request without
a restart. Nothing may read `settings.ask_llm_backend` to decide a live request
— call `library.llm.backends.resolve_backend` instead.

The other nine LLM call sites — extraction, the extraction judge, extraction
repair, markdown generation, email labelling, facet labelling, matter
classification, the amount backfill and chart-rule drafting — all use
`client.messages.parse(output_format=...)`, which validates the response against
a Pydantic schema server-side. **The Agent SDK has no equivalent.** Porting them
would mean asking for JSON in the prompt and parsing it hopefully, discarding
the schema enforcement the extraction pipeline's correctness rests on. Two of
them also send page images as content blocks, which the SDK's prompt surface
cannot take without writing files to disk and re-enabling the built-in file
tools this integration deliberately blocks.

## 3. The harness tax, and how the tool lockdown removes it

The CLI puts a description of every tool it offers into the context of every
call. Left at its defaults it offers a lot: its own built-ins and, because the
credentials are a claude.ai login, **every connector on that claude.ai account**
(Gmail, Slack, Drive, Calendar and so on). `build_options` therefore offers
nothing but library's own tools (§7). With that lockdown the CLI adds almost
nothing to a call.

### 3.1 Measured

A prompt of literally `"Reply with exactly: OK"`, run on the production
container on 2026-11-07 (CLI 2.1.237, SDK 0.2.142):

| Model | Before the lockdown | With the lockdown |
| --- | --- | --- |
| `claude-opus-4-8` | **102,494 tok** | 271 tok |
| `claude-sonnet-4-6` | not measured | 211 tok |
| `claude-haiku-4-5` | **125,580 tok** | 240 tok |

"Before" offered 23 built-ins outside the old blocklist plus about 130 tools
from the account's connectors. The figure grows with whatever the account has
connected: the same measurement read 43,320 on Opus in August 2026, before
more connectors were added. A custom string `system_prompt` replaces Claude
Code's own prompt, so with no tools offered the call is close to the bare
prompt.

### 3.1.1 What it cost on a real turn

The tax was paid per *API call*, and one `tool_loop` makes several. A six-turn
Ask conversation on 2026-11-07, before the lockdown, made 12 model calls and
sent 1.93M input tokens, of which about 1.2M was tool descriptions. The
remaining cost of a turn is library's own system prompt and tool schemas
(about 4k tokens), the conversation history (§7) and the tool results.

### 3.2 What follows from it

- **The lockdown is a security control first.** Ask runs with
  `bypassPermissions`, so any tool the CLI offers can be called without a
  prompt. Ingested email is text a stranger can write, and an Ask turn reads
  it. If the account's connectors were offered, a document could steer the agent
  into sending mail or sharing a file from the owner's accounts. An audit of
  every stored subscription turn up to 2026-11-07 found no call to a tool
  outside library's own.
- **`ask` on the subscription now costs about what it would on the API**, in
  tokens. The subscription's own rolling window is still shared with everything
  else on those credentials.
- **A per-document job still should not be switchable.** `series_insight`, the
  second switchable surface until it was deleted, defaulted to `api`. A
  high-frequency job on the subscription draws on the same window as `ask`, so
  an ingest burst could starve the interactive surface, and §2 explains why the
  structured-output call sites cannot move at all. **A high-frequency,
  small-prompt call site should not be made switchable.**

### 3.3 Cost accounting under a subscription

`cost_usd` keeps being computed from `MODEL_PRICING_USD_PER_MTOK` on both
backends. Under a subscription no such dollars are billed, so the number means
*"what this would have cost on the API"* — useful for measuring the saving.

It deliberately counts cache-creation and cache-read tokens as input, so the
harness overhead shows up rather than reading as free. A subscription-answered
ask turn will therefore record a *higher* `cost_usd` than the same turn on the
API. That is the honest number: the resource being spent is quota, and quota is
proportional to context.

## 4. Provisioning

`ask` ships defaulting to `subscription`, so a deploy needs these credentials
in place to answer questions. The app still *starts* without them — see §6 for
why that is deliberate — but Ask will fail until they exist, and Settings will
refuse to switch a surface onto a backend that cannot authenticate.

1. **Authenticate on the host** — on the LXC, not your laptop:

   ```bash
   claude auth login --claudeai
   ```

   It prints a URL; open it anywhere, sign in, and paste the code back. This
   works fine over SSH — it needs a TTY (`ssh -t`), not a local browser — and
   writes `.credentials.json` into the config directory. Confirm with
   `claude auth status`, which should report `loggedIn: true`.

   **Do not use `claude setup-token` for this.** It does something different:
   it mints a long-lived token and *prints* it for use as
   `CLAUDE_CODE_OAUTH_TOKEN`, writes no credentials file, and does not log the
   CLI in — `claude auth status` still says `loggedIn: false` afterwards.
   Library reads the credentials file, so `setup-token` alone leaves the
   subscription backend unusable. (An earlier version of this document
   recommended it, on the strength of a note about a much older CLI. That was
   wrong; it was corrected after the command was run on this host and left
   nothing behind.)

   > On macOS the CLI stores credentials in the Keychain instead of that file,
   > so a Mac host has nothing to mount. This path is for the Linux deploy host.
   >
   > Library sets `CLAUDE_CONFIG_DIR` for the CLI subprocess **only when
   > `.credentials.json` actually exists** in the configured directory. That
   > variable is an override, not a hint: setting it makes the CLI look only
   > there, so naming an empty directory turns working auth into "Not logged in
   > · Please run /login". Setting it unconditionally broke local development
   > against the Keychain, which is why the check exists.

2. **Check the mount is writable by the container user, and owner-only.** The
   compose file mounts `${CLAUDE_CONFIG_DIR:-~/.claude}` at `/app/.claude`
   **read-write** on both `api` and `worker`. Write access is required — see §5.
   The image runs as the unprivileged `app` user (uid 999), so the host
   directory must be owned by it:

   ```bash
   install -d -m 700 -o 999 -g 999 /srv/apps/library/claude
   CLAUDE_CONFIG_DIR=/srv/apps/library/claude claude auth login --claudeai
   chown 999:999 /srv/apps/library/claude/.credentials.json
   ```

   The `chown` is not optional: the CLI writes as the invoking user (root),
   and the container cannot read — let alone refresh — a root-owned file.

   Mode `700` on the directory, not just the file: the file holds a live access
   token *and* a refresh token, and the refresh token is the one that cannot be
   rotated away without a human. Library writes the file itself with `0600` and
   preserves that across every refresh (§5); the directory is yours to get
   right.

3. **Nothing else is needed to *enable* it** — `ask` ships defaulting to
   `subscription`. To change a surface later, use Settings → LLM backend (§4.1)
   rather than editing `.env`; a stored override wins over the environment.

4. **Verify** — `/healthz` reports credential status whenever a surface uses
   OAuth:

   ```bash
   curl -s http://localhost:8000/healthz | jq '{status, claude_credentials, claude_credentials_detail}'
   ```

   `claude_credentials: "healthy"` means the next call will authenticate. Then
   ask a real question, because health checks the credentials, not the path.

### 4.1 Changing a backend later (Settings → LLM backend)

The live value is an admin-editable instance setting, stored in
`instance_settings` and resolved per request — so a change applies to the next
question with no restart and no redeploy.

**Settings → LLM backend** shows, for each switchable surface, the backend in
force, whether it overrides the deployed default, and what that default is. It
also shows whether an API key is configured and the subscription credential
status, so you can tell *before* switching whether the target backend would
work. Any signed-in user can read the tab — it explains why Ask behaves as it
does; only an admin sees editable controls.

- Changing a surface: pick a backend from its dropdown. If the chosen backend
  cannot authenticate the API returns `409` and the tab shows the reason (e.g.
  run `claude auth login` on the host), leaving the stored value untouched.
- **Reset to deployed default** deletes the override row, so the surface follows
  `LIBRARY_*_LLM_BACKEND` again.

The environment variables remain the *defaults* — what a surface uses when no
override is stored, which is every deployment until an admin changes something.
Editing `.env` does not override a stored value; clear the override instead.

## 5. Token refresh, and the failure that needs a human

OAuth access tokens expire roughly every 8 hours, and the CLI does not refresh
them itself in a headless container. `library.llm.oauth` refreshes before each
call: it reads `expiresAt`, and within a five-minute buffer exchanges the
refresh token at `POST /v1/oauth/token`.

The webserver also runs that same check on a timer
(`LIBRARY_CLAUDE_CREDENTIALS_CHECK_INTERVAL_S`, default 900 s; `0` turns it off),
whether or not anyone asks a question. Before the timer existed, a refresh
token that died while Ask sat unused stayed invisible: health reads "expired
access token + refresh token present" as healthy until something spends the
token, and on 2026-10-22 that something was the user's own question, after 11
idle days. On the timer, a rejection surfaces within one access-token lifetime,
and the token stays in use.

Three properties matter operationally:

- **Refresh tokens are single-use.** The replacement must be persisted or the
  credentials die. Refreshes are serialised behind a lock and written
  atomically, and this is why the mount cannot be `:ro`.
- **The write is owner-only (`0600`), explicitly.** The atomic rename makes the
  *new* file's mode win, so writing it with default permissions would relax the
  credentials file on every refresh — roughly 8-hourly — undoing the `0600` the
  Claude CLI sets when it first writes them.
- **Refresh is best-effort.** Any failure is logged and swallowed so the call
  still proceeds and surfaces its own auth error, rather than refresh becoming a
  second way for ask to fail.
- **A rejected refresh token is not self-healing.** `400 invalid_grant` means it
  has been revoked and no amount of retrying will help.

### 5.1 Runbook: log in again

Symptoms: the Pushover alert "Library Ask: Claude login needs renewing" or
"Library Ask: questions failing on Claude login" (§5.3); `/healthz` reports
`claude_credentials: "unhealthy"`, for example with the detail
`refresh token rejected by Anthropic (invalid_grant)`; Ask answers 503.

Recovery, on the deploy host, not your laptop. The CLI login is interactive, so
SSH needs a TTY:

```bash
ssh -t root@<deploy host>
CLAUDE_CONFIG_DIR=<mounted dir> claude auth login --claudeai
chown 999:999 <mounted dir>/.credentials.json
```

`<mounted dir>` is the host side of the `/app/.claude` bind mount
(`/srv/apps/library/claude` on the current deploy). **Not `/app/.claude`:** that path
exists only inside the container. Set `LIBRARY_CLAUDE_HOST_CONFIG_DIR` to the host
path and the alert, `/healthz` and the 503 all print these commands with it filled in.
The repo's compose file passes it from the same `CLAUDE_CONFIG_DIR` variable it
mounts. The login prints a URL:
open it anywhere, sign in, and paste the code back. Confirm with
`curl -s http://localhost:8010/healthz | jq .claude_credentials` on the host,
which should read `"healthy"`; the gauge follows within one scrape and the
alert resolves.

No restart needed — the next call re-reads the file.

Health keys the rejection on a hash of the token itself, so re-authenticating
clears the alarm immediately rather than at the next refresh cycle.

Transient failures (5xx, network) are deliberately *not* flagged — they recover
on the next call, and flagging them would train you to ignore the signal.

### 5.2 What a credential failure looks like

`POST /api/ask` answers **503** with the reason and the fix, e.g.:

```
Ask is unavailable: the Claude subscription backend could not authenticate:
refresh token rejected by Anthropic (invalid_grant) — log in on the host:
`CLAUDE_CONFIG_DIR=/srv/apps/library/claude claude auth login --claudeai`, then
`chown 999:999 /srv/apps/library/claude/.credentials.json`
(see docs/llm-backends.md §5.1). Original error: …
```

The commands name the **host** directory, from `LIBRARY_CLAUDE_HOST_CONFIG_DIR`
(§5.1). Without that setting they say `<host dir>` and name the mount instead,
because the container cannot see the host side of its own bind mount. They never
print `/app/.claude` as a host path. Until 2026-10-27 they did (#195): pasted on
the host, `CLAUDE_CONFIG_DIR=/app/.claude claude auth login` created a new
directory library never reads. The bare `claude auth login` that `/healthz` used to
suggest logged in the operator's own `~/.claude` instead. Every non-healthy
`/healthz` detail now ends with the same commands.

Three things this deliberately avoids:

- **A bare 500.** The SDK raises its own error *mid-iteration*, before any
  result message is seen, so an untranslated exception escaped to FastAPI as
  "Internal Server Error" with the reason only in the container log.
- **The SDK's own advice.** It says "Please run /login", but `/login` is an
  interactive slash command inside the CLI — not how a container
  authenticates, and following it does nothing for this deployment.
- **Blaming auth for everything.** When credentials are healthy the message
  reports the underlying failure without a login command, so a CLI crash or a
  rate limit does not send anyone through a pointless re-authentication.

A background job has no user-facing surface to answer with; the same message
reaches the worker log instead. No background job is switchable today — `ask` is
the only surface in `BACKEND_SURFACES` — so in practice this path is reached
only by a job sharing the credentials for another reason.

### 5.3 Monitoring: the gauge and the alerts

The webserver's `/metrics` exposes `library_claude_credentials_healthy`: `1`
while credentials are `healthy`, `0` when a human must act (`unhealthy` or
`degraded`), and no series where OAuth is not in use. It reads the same check
as `/healthz` (`app.claude_credential_status`), evaluated on each scrape, so
the two cannot disagree.

Two Grafana rules page through Pushover. Both messages carry the §5.1 commands
and a link to it.

| Rule | Condition | Catches |
| --- | --- | --- |
| Library Ask: Claude login needs renewing | gauge `< 1` for 5 min; a missing series also fires | a dead or missing login, found by the timer before anyone asks; the webserver not being scraped |
| Library Ask: questions failing on Claude login | any `library_ask_errors_total{kind="subscription_auth"}` increase in 15 min | a question that already failed on auth, even if the gauge still reads `1` |

The rules live in Grafana's database, not this repo. Their definitions are
recorded in the home-server repo's `documentation/grafana-alerting.md`.

## 6. Where the guard lives

An earlier version validated credentials at startup. That check could not
survive a runtime-editable setting: the backend can become `subscription` long
after boot, so a startup check both misses the real case *and* would refuse to
start a container whose credentials are provisioned a moment later — during the
§5.1 recovery, exactly when starting matters most.

The guard therefore lives in two places that still work:

- **Write time.** `set_backend` refuses a backend that cannot authenticate, so
  the API returns `409` and the UI shows the reason. The admin making the change
  hears about it, instead of the next person to ask a question.
- **`/healthz`.** Reports `claude_credentials` whenever a surface uses OAuth, so
  a refresh token revoked *after* the switch was flipped is still visible.

`ask` also drops its "no Anthropic API key" 503 when the resolved backend is
`subscription` — a subscription deployment needs no API key for ask at all,
titles included.

## 7. Implementation notes

- `src/library/llm/oauth.py` — refresh and health. Ported from
  `homelab-sre/sre-agent`, which learned each edge case from an outage.
- `src/library/llm/subscription.py` — the SDK adapter: `text_call` and
  `tool_loop`.
- **`ANTHROPIC_API_KEY` is blanked in the CLI subprocess environment.** The CLI
  ranks that variable *above* the credentials file and sends it as `X-Api-Key`.
  Library sets it for the API backend, so leaving it visible makes the CLI send
  an API-key header carrying an OAuth token and fail. It is set in exactly one
  place, `build_options`, because a rebuild that dropped it caused a production
  outage in the project this was ported from.
- **The CLI offers library's tools and nothing else.** `tools=[]` turns off
  every built-in by allowlist, so a built-in added by a later CLI release stays
  off. `strict_mcp_config=True` limits MCP servers to the one library passes in,
  which keeps out the claude.ai account's connectors. `setting_sources=[]` was
  never enough for that. `skills=[]` turns off skills. The old blocklist
  (`_BLOCKED_BUILTINS`) stays as a second layer. `setting_sources=[]` stops the
  host's `CLAUDE.md` and settings leaking into library's prompts.
  `tests/test_llm_subscription_cli.py` starts the real bundled CLI with no
  credentials and a dead API address. It asserts that the tool list in the
  CLI's startup message is exactly library's, so a CLI upgrade that adds a tool
  fails CI.
- **Turn transcripts stay in Anthropic block form on both backends.** `ask`'s
  preview-then-confirm write gate re-reads stored `tool_use` blocks to decide
  what a later turn may edit, so the SDK path reconstructs the same vocabulary
  from its message stream. A thread stays readable, and the gate keeps working,
  after the knob is flipped either way.
- **History is stuffed, not replayed.** `query()` is stateless and its streaming
  input accepts user turns only, so prior turns are rendered into a
  `<conversation_history>` preamble rather than sent as real message turns. This
  is a genuine behavioural difference from the API backend. Images from earlier
  turns are dropped from that preamble, deliberately — re-sending every
  historical attachment would grow the prompt without bound.

## 8. Licensing

Anthropic's consumer subscription terms cover use through the Claude apps and
Claude Code. Using a subscription as the billing backend for a self-hosted
service is not clearly within them. This is flagged, not resolved — check the
current terms if it matters to you. It is a reason to keep the batch pipeline on
metered API billing quite apart from the technical ones in §2 and §3.
