# Observability

**Status:** active. **Last updated:** 2026-11-07 (§3: the `library.ask.input_tokens_per_call` histogram and its query; subscription cost is now the API-equivalent, not a full-rate ceiling). Earlier entries are in this file's history.
**Last verified:** 2026-11-07 — method: partial, scoped to the `input_tokens_per_call` row and its subsection. `tests/test_telemetry.py::test_input_tokens_per_call_reaches_the_scrape` reads the series from a real `/metrics` scrape and `tests/test_api_ask.py::test_a_turn_records_its_input_tokens_per_model_call` pins the call site, both seen red first. The rest carries its earlier verifications forward.
**Covers:** src/library/mcp_rejected_log.py, src/library/telemetry.py

> Purpose
>
> What the application measures about itself, how those measurements leave the
> process, and the two rules — cardinality and privacy — that keep the system
> from hurting you. Ask and the MCP server's tool calls are the instrumented
> surfaces today.

## 1. Why this exists

Ask's cost and behaviour were only visible by querying `ask_turns` after the
fact. Good for archaeology, useless for "is it behaving right now", and it could
not answer the two questions that actually came up during development:

1. **Is the prompt cache hitting?** Projected at 40–50%, measured once by hand at
   76.6%, then never again.
2. **Did enabling adaptive thinking change what a turn costs?** Answered with
   `n=1` — and by a comparison that was invalid anyway, because the meaning of
   `ask_turns.input_tokens` changed in the same deploy.

Both are aggregate questions over time. That is what metrics are for.

## 2. The OpenTelemetry pieces, briefly

These are easy to conflate, and the names do not help:

| Piece | What it does |
| --- | --- |
| **Instrument** | The thing you call — `counter.add(5, {...})` |
| **Meter** | Makes instruments, under a namespace (`library.ask`) |
| **Reader** | Decides *when* measurements leave: on scrape (pull) or on a timer (push) |
| **Exporter** | Decides *where* and in what wire format |
| **MeterProvider** | Owns the readers, hands out meters |

The useful consequence: **one provider can carry several readers at once.** So
Prometheus can pull from `/metrics` while an OTLP exporter simultaneously pushes
to a collector — same instruments, same measurements, two destinations, no
double counting. Each reader gets its own view of the stream.

**Metrics, no traces.** The questions above are aggregate; traces answer
per-request questions and cost far more to store. That is a decision to
revisit, not a limitation to work around. The one structured log stream is
the MCP call line (§3.1).

## 3. What is measured

Emitted under the meters `library.ask` and `library.mcp`; Prometheus renders
`.` as `_`, appends `_total` to counters and `_seconds` to second-valued
histograms.

| Metric | Type | Attributes | The question it answers |
| --- | --- | --- | --- |
| `library.ask.tokens` | counter | `backend`, `model`, `kind` | Cache hit rate. `kind` is `fresh` / `cache_read` / `cache_write` / `output` |
| `library.ask.cost` | counter (USD) | `backend`, `model` | Spend over time, per backend |
| `library.ask.turns` | counter | `backend`, `model`, `outcome` | Volume, and how often the tool loop gives up (`outcome=no_answer`) |
| `library.ask.duration` | histogram (s) | `backend`, `model`, `outcome` | Latency, including for failed turns |
| `library.ask.tool_calls` | histogram | `backend`, `model`, `outcome` | Whether raising the loop ceiling from 4 to 8 is being used |
| `library.ask.citations` | histogram | `backend`, `model`, `outcome` | Whether answers are grounded — a quality proxy |
| `library.ask.errors` | counter | `backend`, `kind` | Failures by category, previously only greppable in logs |
| `library.ask.input_tokens_per_call` | histogram (tokens) | `backend`, `model` | Whether the fixed cost of a model call has grown. One sample per turn: its input tokens over its model calls. Buckets 500 to 200k. See below |
| `library.mcp.calls` (`library_mcp_calls_total`) | counter | `tool`, `outcome` | Which MCP tools are used, and how often they fail. §3.1 |
| `library.mcp.duration` (`library_mcp_duration_seconds`) | histogram (s) | `tool`, `outcome` | Which tools are slow. Buckets 10 ms to 30 s. §3.1 |
| `library.claude_credentials.healthy` | gauge | none | Whether Ask's subscription login works: `1` usable, `0` a human must log in again, absent where OAuth is not in use. Read on each scrape; see [llm-backends.md §5.3](llm-backends.md) |

`backend` is `api` or `subscription`, and it changes what `cost` *means*: the
subscription path is not metered, so its cost is what the API would have
charged ([llm-backends.md §3.3](llm-backends.md)). Do not sum the two and call
it a bill.

**Every `library.ask.*` metric is instrumented where the backends converge**
(`api/ask.py`), not inside the engine — so one call site covers both and their
numbers stay comparable.

### Input tokens per model call

The subscription's fixed cost per call once grew from ~43k to ~100k tokens as
the CLI offered more tools, mostly the claude.ai account's connectors, and
nothing noticed ([llm-backends.md §3](llm-backends.md)). After
the tool lockdown a call carries library's own prompt and tools (~4k tokens)
plus the conversation, so a median that climbs past ~50k means something is
being added to every call again:

```promql
histogram_quantile(0.5, sum by (le, backend) (
  rate(library_ask_input_tokens_per_call_bucket[1d])))
```

The alert rule belongs with the others in the home-server repository; it is not
defined yet.

### Cache hit rate, as a query

```promql
sum(rate(library_ask_tokens_total{kind="cache_read"}[1h]))
/
sum(rate(library_ask_tokens_total{kind=~"cache_read|fresh|cache_write"}[1h]))
```

### 3.1 MCP tool calls

`ToolCallMetrics`, a FastMCP `on_call_tool` middleware in
`src/library/mcp_server.py`, sees every `tools/call` from every client. It
calls `telemetry.record_mcp_call(tool, outcome, duration_s)` and re-raises any
exception untouched, so results and error masking are unchanged.

- `tool` is the tool's name. A name the server does not have is recorded as
  `unknown`, because a client can send any string.
- `outcome` is `ok`; `tool_error`, a refusal written for the model (not found,
  bad arguments, a stale edit preview); or `error`, an unexpected exception,
  which the client sees only as the masked `Error calling tool '…'`.
  `tool_error` also counts a dependency outage a tool turns into advice for
  the model: `semantic_search` with the embedding service down, and
  FastMCP's own errors for an upstream timeout or 429. So an embedder outage
  shows as a rise in `tool_error` for `semantic_search`, not in `error`.

17 tools plus `unknown`, x 3 outcomes, is a fixed ceiling. The user is deliberately **not** a
label (§4).

Each call also logs one line at INFO on the `library.mcp_server.calls` logger,
which `main.py` turns on in the API process (uvicorn sets up no handler for
application loggers, so without it an INFO line is dropped):

```text
2026-11-05 13:45:12,345 INFO library.mcp_server.calls mcp_call tool=get_document user_id=2 outcome=ok duration_ms=41
```

`user_id` is the Library user the token belongs to (`None` only if a token
carries none). The line **never** contains arguments or results: both can
hold document text, a search query or a note. FastMCP's own `fastmcp` logger
is separate and less careful: it logs a traceback for an unexpected error, the
validation detail (which includes the offending value) for a call with invalid
arguments, and at DEBUG every call's arguments, so leave `FASTMCP_LOG_LEVEL` at
its default (`INFO`) in production.

A request the `/mcp` transport refuses before any tool runs (a 4xx other than
the 401 sign-in challenge) logs one line on `library.mcp_rejected_log`, also at
INFO:

```text
2026-11-05 13:45:12,345 INFO library.mcp_rejected_log mcp_rejected status=400 method=tools/list protocol_version=2026-07-28 reason="Bad Request: Unsupported protocol version: 2026-07-28. Supported versions: …"
```

`method` is the JSON-RPC method (`-` when the body is not a single JSON
object), `reason` is the error message the transport gave, cut to 200
characters and JSON-quoted. In `method` and `protocol_version`, both
client-sent, whitespace and any character outside printable ASCII become
`_`, so no value can pose as an extra field or reach a terminal as an escape
code. Params, arguments and bodies are never logged. Writing the line can
never fail the request: it runs after the response is sent, and a fault there
is logged at debug and dropped. The usual cause
is a client on a newer MCP revision probing before it falls back to
`initialize` ([mcp.md §1.2](mcp.md)).

```promql
# Error rate per tool over the last hour
sum by (tool) (rate(library_mcp_calls_total{outcome="error"}[1h]))
/ sum by (tool) (rate(library_mcp_calls_total[1h]))

# p95 latency per tool
histogram_quantile(0.95, sum by (tool, le) (rate(library_mcp_duration_seconds_bucket[1h])))
```

## 4. Two rules

**Cardinality.** Every distinct combination of attribute values is a separate
time series. `backend` (2) x `model` (a few) x `kind` (4) is fine. Adding
`thread_id`, the question text, or a user identifier would be unbounded and
would eventually take the metrics store down. Any new attribute must be argued
for.

**Privacy.** Nothing recorded here contains question text, answer text, document
content, or document identifiers — only counts, sums and durations. Ask prompts
embed the user's archive, and [ask.md](ask.md) promises document text does not
leave the host. A telemetry side-channel would break that just as thoroughly as
an indexing one, and far less visibly.

## 5. Turning it on

Everything is off by default. With both exporters off the instruments still run
and record into a no-op provider — **instrumentation is never conditional on
configuration**, so the code path in production is the code path under test.

### Prometheus (pull)

```bash
LIBRARY_OTEL_METRICS_ENABLED=true
```

`GET /metrics` is then served unauthenticated, like `/healthz`. While disabled it
returns **404, not an empty 200** — an empty exposition is indistinguishable
from "running and recording nothing", so a scrape would look healthy while
collecting no series.

```yaml
scrape_configs:
  - job_name: library
    static_configs:
      - targets: ['lxc-library:8000']
```

### OTLP (push)

```bash
LIBRARY_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/metrics
LIBRARY_OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer xxx   # optional
LIBRARY_OTEL_METRIC_EXPORT_INTERVAL_MS=60000                  # optional
```

`http/protobuf`, not gRPC — it keeps the dependency tree httpx-shaped, with no C
extension and no extra port.

Both may be enabled together.

## 6. Claude Code's own telemetry (subscription backend only)

The subscription backend runs the Claude Code CLI through `claude-agent-sdk`,
and that CLI has its own OpenTelemetry support. It emits
`claude_code.token.usage` with a `type` attribute of
`input` / `output` / `cacheRead` / `cacheCreation`, plus `claude_code.cost.usage`
— richer than this app can see from the outside.

```bash
LIBRARY_CLAUDE_CODE_TELEMETRY_ENABLED=true
LIBRARY_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/metrics
```

It **complements** rather than replaces the in-process metrics: it sees only the
subscription path, and its data is session-scoped rather than per-turn, so it
cannot be joined to an `ask_turns` row.

### The content-logging guard

Claude Code can also export **content** — prompts, responses, tool inputs and
outputs. For Ask that is the text of your documents.

Five variables are involved: `OTEL_LOG_USER_PROMPTS`,
`OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`,
`OTEL_LOG_RAW_API_BODIES`. This app:

1. **Blanks all five** in the CLI's environment, whether telemetry is on or off.
   Not merely omits — the SDK *merges* over the inherited environment, so a
   variable set on the container would otherwise reach the CLI.
2. **Refuses to run** if any is set, raising rather than warning. A warning in a
   container log is not a control: nobody reads it, and the failure it guards
   against is silent and irreversible.
3. Sets `OTEL_LOGS_EXPORTER=none`, since events are the other channel content
   travels down.

Token and cost metrics need none of them.

Note the CLI also attaches identity attributes by default (`user.email`,
`user.account_uuid`, `organization.id`). That is identity, not document content,
but it is worth knowing before pointing this at a shared collector.

## 7. What is not covered

1. **Ask and MCP only.** Ingestion, OCR, extraction, the REST API and the job
   queue are uninstrumented.
2. **No traces.** Per-request timing breakdowns are not available.
3. **No dashboard.** The metrics exist; nothing renders them yet.
4. **Alerting covers the login only.** Two Grafana rules page on the
   credentials gauge and on `subscription_auth` errors
   ([llm-backends.md §5.3](llm-backends.md)). Nothing fires on a cost spike or
   a general error-rate jump.
