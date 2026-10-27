# LLM backends

**Status:** active. **Last updated:** 2026-10-27 (every login instruction names the host directory, #195: the 503, `/healthz` and the settings view used to print `CLAUDE_CONFIG_DIR=/app/.claude`, the container path, or a bare `claude auth login`; new `LIBRARY_CLAUDE_HOST_CONFIG_DIR` in §5.1, §5.2 example rewritten). Earlier: 2026-10-22 (§5 gains the scheduled refresh, which finds a dead refresh token without a question being asked; §5.1 is renamed "Runbook: log in again" and gives the host-side commands, so the alerts can link to it; new §5.3 documents the `library_claude_credentials_healthy` gauge and the two Grafana alerts). Earlier: 2026-08-31 (the `series_insight` surface was deleted with the legacy series stack, so §2's table is one row: `ask`. The switchable-backend *mechanism* is unchanged — `BACKEND_SURFACES` is still a map and a second row is still all it takes — and §3.2 keeps the reason a per-document job should not be switchable. Also corrected the structured-output call-site count, six → nine.) Earlier: 2026-08-20 (credential failures now answer 503 with the command that fixes them instead of a bare 500 — §5.2). Earlier the same day: corrected the provisioning command — `claude auth login --claudeai`, not `setup-token`, which writes nothing; and `/healthz` now reports credential health whenever credentials exist rather than keying on the environment default, which left the alarm silent for deployments enabled via the toggle). Earlier the same day: credential-file permissions: refreshes now preserve `0600` instead of widening it, plus directory-ownership guidance in §4). Earlier the same day: the backend became an admin-editable instance setting resolved per request — Settings → LLM backend, §4.1 — and `ask` ships defaulting to `subscription`; the startup validator is replaced by write-time and health checks, §6). Earlier the same day: initial version, adding the subscription backend for `ask` and, off by default, `series_insight`.
**Last verified:** 2026-10-27 — method: partial, scoped to §5.1's setting paragraph and §5.2's example. The example is the text `oauth.login_instructions` and `subscription._explain` now produce, pinned by tests in `tests/test_llm_oauth.py` and `tests/test_llm_subscription.py` that failed before the change (7 red) and pass after. Earlier: 2026-10-22 — method: partial, scoped to §5, §5.1 and §5.3. The timer and the gauge are pinned by tests in `tests/test_llm_oauth.py`, `tests/test_telemetry.py` and `tests/test_llm_backend_config.py`; the loop's error guard was mutation-checked (removing it turns its test red). The §5.1 commands were run on the deploy host on 2026-10-22 and `/healthz` then read `healthy`. The §5.3 rules are verified in the home-server repo, not here. Earlier: 2026-08-31 — method: partial, scoped to §2, §3.2 and §5.2's closing paragraph. Read `BACKEND_SURFACES` in `src/library/llm/backends.py` (one key, `ask`), confirmed no `series_insight` field survives in `src/library/config.py`, and re-derived the structured-output call sites with `grep -rn 'output_format=' src/library/` — nine files: `charts/draft.py`, `email_label.py`, `extraction/extractor.py`, `extraction/judge.py`, `extraction/repair.py`, `facets/labeller.py`, `markdown/generator.py`, `matter_classifier.py`, `money/backfill.py`. No live LLM calls were made this pass; every measured figure carries forward from the 2026-08-20 verification, whose method was: the §3.1 harness figures and the model-access claim are from live calls against a Claude subscription (both spikes reproduced the numbers); the §3.1.1 figures and the §7 claims about tool bridging, block reconstruction and history stuffing are from running `library.llm.subscription.tool_loop`/`text_call` themselves against the live API — the transcript came back with unprefixed tool names, both tools dispatched, the history preamble honoured and an image attachment read. The §5.2 behaviour was established by running the adapter against an unprovisioned config directory on the production container, which raised the SDK's own `ResultError` ("Not logged in · Please run /login") before any result message — the case the translation now covers; the 503 and the healthy-credentials wording are covered by tests confirmed to fail against the previous behaviour. The §4 provisioning commands were run on the deploy host: `setup-token` left `claude auth status` reporting `loggedIn: false` with no credentials file anywhere, while `claude auth login --claudeai` over SSH produced a `claudeAiOauth` file and `loggedIn: true`. A live Opus call and a full Ask turn then ran on the subscription from the production container (131,966 input tokens for a one-tool turn), and the container's write path was checked to confirm §5's refresh can persist. The `CLAUDE_CONFIG_DIR` rule in §4 was established by bisecting a real auth failure, and the §4/§5 file-permission claims by tests asserting the on-disk mode after a refresh (including under umask 0), confirmed to fail against the previous implementation. The §2/§4.1/§6 claims about runtime resolution, override precedence, write-time refusal and the 409 are covered by executed tests (`tests/test_llm_backends.py`, `frontend/src/views/__tests__/SettingsLlmBackend.spec.ts`) in a run of the full suite: 1623 backend tests and 1058 frontend tests passing, ruff and mypy clean. Still unverified: the ~1-year `CLAUDE_CODE_OAUTH_TOKEN` form is documented from the CLI's own output, not exercised.

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

## 3. The harness tax — read this before flipping a switch

Every subscription call carries the full Claude Code system prompt, whether or
not you want it.

### 3.1 Measured

With every built-in tool disallowed and a prompt of literally
`"Reply with exactly: OK"`:

| Model | Real input | Context actually sent | Notional cost |
| --- | --- | --- | --- |
| `claude-sonnet-4-6` | 3 tok | **32,231 tok** | $0.121 |
| `claude-opus-4-8` | 2 tok | **43,320 tok** | $0.813 |

It cannot be configured away. Same prompt, same model (`sonnet-4-6`), varying
only the options:

| Configuration | Context |
| --- | --- |
| no `system_prompt` | 32,234 |
| custom string `system_prompt` | 32,239 |
| explicit `claude_code` preset | 38,165 |
| custom `system_prompt` + `setting_sources=[]` | 32,239 |

A custom system prompt does not replace the preset — it costs five tokens more.

### 3.1.1 What it costs on a real turn

The floor above is per *API call*, and one `tool_loop` makes several. Running
library's own adapter against the live subscription — a two-tool question
("how much is my gas bill and who is it from?", `semantic_search` then
`get_document`, Sonnet) — recorded **134,962 input tokens** for a turn whose
actual content is a sentence and two small JSON tool results. A one-shot
`text_call` for a six-word thread title recorded **26,068 input / 887 output**.

Budget for the real figure, not the floor: the preamble is re-sent on each
iteration of the agent loop.

### 3.2 What follows from it

The tax is *fixed per call*, so it is only tolerable where each call is already
large and calls are infrequent.

- **`ask` earns it — but it is not cheap.** `ask_model` is `claude-opus-4-8`,
  the priciest configured model, and calls are human-paced, so the dollar cost
  going to zero is worth real quota. Size it honestly, though: §3.1.1 measured
  ~135k input tokens for a single two-tool Sonnet turn, and Opus carries a
  larger preamble. A busy Ask session can consume a meaningful slice of a
  subscription's rolling window — and it is the *same* window everything else
  on those credentials draws from.
- **A per-document job would not have earned it.** `series_insight`, the second
  switchable surface until it was deleted, defaulted to `api` for exactly this
  reason: it ran on `extraction_model` (`claude-haiku-4-5`, the cheapest) with a
  bounded prompt, once per ingested document. Routing it through the SDK would
  have spent ~32k of quota per document to avoid a fraction of a cent — and that
  quota is shared with `ask` and with anything else on the same credentials, so
  an ingest burst could starve the interactive surface. Keep the rule even
  though the surface is gone: **a high-frequency, small-prompt call site should
  not be made switchable at all.**

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
- **All Claude Code built-ins are blocked**, and `setting_sources=[]` stops the
  host's `CLAUDE.md` and settings leaking into library's prompts.
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
