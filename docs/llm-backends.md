# LLM backends

**Status:** active. **Last updated:** 2026-08-20 (initial version: adds the subscription backend for `ask` and, off by default, `series_insight`).
**Last verified:** 2026-08-20 — method: the §3.1 harness figures and the model-access claim are from live calls against a Claude subscription (both spikes reproduced the numbers); the §3.1.1 figures and the §7 claims about tool bridging, block reconstruction and history stuffing are from running `library.llm.subscription.tool_loop`/`text_call` themselves against the live API — the transcript came back with unprefixed tool names, both tools dispatched, the history preamble honoured and an image attachment read. The `CLAUDE_CONFIG_DIR` rule in §4 was established by bisecting a real auth failure. Not verified against a deployed container — no surface has been switched to `subscription` in production, and the Linux credentials-file path (§4) is untested since macOS uses the Keychain.

> **Purpose** — Library can reach Claude two ways: the metered Anthropic
> Messages API, or a Claude subscription via the bundled Claude Code CLI. This
> explains which surfaces may use which, why the batch pipeline may not, and how
> to provision and operate the subscription path.

## 1. The two backends

| | `api` (default) | `subscription` |
| --- | --- | --- |
| Package | `anthropic` | `claude-agent-sdk` |
| Transport | `POST /v1/messages` | Claude Code CLI subprocess |
| Auth | `x-api-key` from `LIBRARY_ANTHROPIC_API_KEY` | OAuth from `.credentials.json` |
| Billed as | Dollars per token | Subscription quota |
| Structured outputs | Yes (`messages.parse`) | **No** |

The second row is the whole mechanism. Subscription access is not a different
credential you can hand to the Messages API — it comes from *being the Claude
Code CLI*. `claude-agent-sdk` bundles that CLI in its wheel and shells out to
it; the CLI reads the OAuth credentials `claude setup-token` writes.

An OAuth subscription token sent directly to `/v1/messages` reaches Haiku only —
Sonnet and Opus are refused. Going through the CLI is what unlocks them.

## 2. Which surfaces can use it

Only two, and they are the only two library has that are *not* built on
structured outputs:

| Surface | Setting | Default |
| --- | --- | --- |
| `ask` tool loop + thread titles | `LIBRARY_ASK_LLM_BACKEND` | `api` |
| `series_insight` descriptions | `LIBRARY_SERIES_INSIGHT_LLM_BACKEND` | `api` |

The other six LLM call sites — extraction, the extraction judge, extraction
repair, markdown generation, email labelling, matter classification — all use
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
- **`series_insight` does not, which is why it defaults to `api`.** It runs on
  `extraction_model` (`claude-haiku-4-5`, the cheapest) with a deliberately
  bounded prompt, once per ingested document. Routing it through the SDK spends
  ~32k of quota per document to avoid a fraction of a cent — and that quota is
  shared with `ask` and with anything else using the same credentials, so an
  ingest burst can starve the interactive surface. The switch exists so the
  trade can be measured, not because it is recommended.

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

The credentials must exist on the deploy host before any surface is switched
over. `Settings` refuses to start otherwise (§6).

1. **Authenticate on the host** — on the LXC, not your laptop:

   ```bash
   claude setup-token
   ```

   Use `setup-token`, not `claude login`: the login flow fails on a headless
   host with a redirect-URI error. This writes `~/.claude/.credentials.json`.

   > On macOS the CLI stores credentials in the Keychain instead of that file,
   > so a Mac host has nothing to mount. This path is for the Linux deploy host.
   >
   > Library sets `CLAUDE_CONFIG_DIR` for the CLI subprocess **only when
   > `.credentials.json` actually exists** in the configured directory. That
   > variable is an override, not a hint: setting it makes the CLI look only
   > there, so naming an empty directory turns working auth into "Not logged in
   > · Please run /login". Setting it unconditionally broke local development
   > against the Keychain, which is why the check exists.

2. **Check the mount is writable by the container user.** The compose file
   mounts `${CLAUDE_CONFIG_DIR:-~/.claude}` at `/app/.claude` **read-write** on
   both `api` and `worker`. Write access is required — see §5. The image runs as
   the unprivileged `app` user, so the host directory's uid must permit it;
   `chown` it or pin `user:` in compose if the ids do not line up.

3. **Flip the switch** in `.env` and restart:

   ```
   LIBRARY_ASK_LLM_BACKEND=subscription
   ```

4. **Verify** — `/healthz` reports credential status whenever a surface uses
   OAuth:

   ```bash
   curl -s http://localhost:8000/healthz | jq '{status, claude_credentials, claude_credentials_detail}'
   ```

   `claude_credentials: "healthy"` means the next call will authenticate. Then
   ask a real question, because health checks the credentials, not the path.

## 5. Token refresh, and the failure that needs a human

OAuth access tokens expire roughly every 8 hours, and the CLI does not refresh
them itself in a headless container. `library.llm.oauth` refreshes before each
call: it reads `expiresAt`, and within a five-minute buffer exchanges the
refresh token at `POST /v1/oauth/token`.

Three properties matter operationally:

- **Refresh tokens are single-use.** The replacement must be persisted or the
  credentials die. Refreshes are serialised behind a lock and written
  atomically, and this is why the mount cannot be `:ro`.
- **Refresh is best-effort.** Any failure is logged and swallowed so the call
  still proceeds and surfaces its own auth error, rather than refresh becoming a
  second way for ask to fail.
- **A rejected refresh token is not self-healing.** `400 invalid_grant` means it
  has been revoked and no amount of retrying will help.

### 5.1 Runbook: "every question fails, `/healthz` says degraded"

Symptom — `claude_credentials: "unhealthy"` with detail
`refresh token rejected by Anthropic (invalid_grant)`, and logs showing
`Command failed with exit code 1` from the SDK.

Recovery:

```bash
claude setup-token                    # on the host — writes fresh credentials
docker compose restart api worker
```

Health keys the rejection on a hash of the token itself, so re-authenticating
clears the alarm immediately rather than at the next refresh cycle.

Transient failures (5xx, network) are deliberately *not* flagged — they recover
on the next call, and flagging them would train you to ignore the signal.

## 6. Fail-fast behaviour

Setting a backend to `subscription` while `claude_config_dir` is not a directory
is a startup error, not a runtime one — otherwise the misconfiguration surfaces
as an "Invalid API key" from a subprocess on the first real question, in a
traceback that names neither the mount nor the setting.

The check is on the *directory*, not the credentials file: recovery (§5.1) runs
`claude setup-token` against a mounted but momentarily empty directory, and
refusing to start in that window would make recovery harder.

`ask` also drops its "no Anthropic API key" 503 when the backend is
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
