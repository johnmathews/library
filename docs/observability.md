# Observability

**Status:** active. **Last updated:** 2026-08-21 (new: OpenTelemetry metrics for the Ask surface, a Prometheus scrape endpoint, an optional OTLP exporter, and Claude Code's own CLI telemetry for the subscription backend).
**Last verified:** 2026-08-21 — method: every metric name, attribute and setting below resolved against `src/library/telemetry.py`, `src/library/api/ask.py` and `src/library/config.py`; the exposition format checked by scraping `GET /metrics` in a test that records first and asserts the series appear; the content-logging guard checked by 8 tests including one per forbidden variable. **Not verified:** no dashboard has been built and no scrape has run against the deployed host — the Prometheus config below is written from the endpoint's actual output, not from an observed scrape.

> Purpose
>
> What the application measures about itself, how those measurements leave the
> process, and the two rules — cardinality and privacy — that keep the system
> from hurting you. Ask is the only instrumented surface today.

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

**Metrics only, no traces or logs.** The questions above are aggregate; traces
answer per-request questions and cost far more to store. That is a decision to
revisit, not a limitation to work around.

## 3. What is measured

All emitted by `library.ask`; Prometheus renders `.` as `_` and appends `_total`
to counters.

| Metric | Type | Attributes | The question it answers |
| --- | --- | --- | --- |
| `library.ask.tokens` | counter | `backend`, `model`, `kind` | Cache hit rate. `kind` is `fresh` / `cache_read` / `cache_write` / `output` |
| `library.ask.cost` | counter (USD) | `backend`, `model` | Spend over time, per backend |
| `library.ask.turns` | counter | `backend`, `model`, `outcome` | Volume, and how often the tool loop gives up (`outcome=no_answer`) |
| `library.ask.duration` | histogram (s) | `backend`, `model`, `outcome` | Latency, including for failed turns |
| `library.ask.tool_calls` | histogram | `backend`, `model`, `outcome` | Whether raising the loop ceiling from 4 to 8 is being used |
| `library.ask.citations` | histogram | `backend`, `model`, `outcome` | Whether answers are grounded — a quality proxy |
| `library.ask.errors` | counter | `backend`, `kind` | Failures by category, previously only greppable in logs |

`backend` is `api` or `subscription`, and it changes what `cost` *means*: the
subscription path is not metered, so its cost is a notional ceiling priced at
the full input rate. Do not sum the two and call it a bill.

**Instrumented at the point where the backends converge** (`api/ask.py`), not
inside the engine — so one call site covers both and their numbers stay
comparable.

### Cache hit rate, as a query

```promql
sum(rate(library_ask_tokens_total{kind="cache_read"}[1h]))
/
sum(rate(library_ask_tokens_total{kind=~"cache_read|fresh|cache_write"}[1h]))
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

1. **Ask only.** Ingestion, OCR, extraction and the job queue are uninstrumented.
2. **No traces.** Per-request timing breakdowns are not available.
3. **No dashboard.** The metrics exist; nothing renders them yet.
4. **No alerting.** No rule fires on a cost spike or an error-rate jump.
5. **No verified scrape.** The Prometheus config above is written from the
   endpoint's real output, but no scrape has been run against the deployed host.
