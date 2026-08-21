# OpenTelemetry metrics for Ask

**Date:** 2026-08-21

Built because two questions came up today that the system could not answer:
*is the prompt cache hitting?* (projected 40–50%, measured once by hand at
76.6%, never again) and *did adaptive thinking change what a turn costs?*
(answered with `n=1`, by a comparison that was invalid anyway — see below).

Both are aggregate questions over time, which is what metrics are for.

## What shipped

**In-process OTel metrics**, instrumented at the point where the two LLM
backends converge (`api/ask.py`), not inside the engine — one call site covers
both, and their numbers stay comparable:

| Metric | Answers |
| --- | --- |
| `library.ask.tokens` (`kind`=fresh/cache_read/cache_write/output) | cache hit rate |
| `library.ask.cost` | spend, per backend |
| `library.ask.turns` (`outcome`) | volume, and how often the loop gives up |
| `library.ask.duration` | latency, including failures |
| `library.ask.tool_calls` | whether the 4→8 ceiling raise is used |
| `library.ask.citations` | grounding, as a quality proxy |
| `library.ask.errors` (`kind`) | failures, previously only greppable |

**Two exporters, both optional and independent.** Prometheus pulls from
`GET /metrics`; OTLP pushes to a collector. One MeterProvider carries both
readers, so it is the same measurements to two destinations rather than double
instrumentation.

**Claude Code's own CLI telemetry**, for the subscription backend, wired through
`ClaudeAgentOptions.env`.

## Three decisions worth recording

**1. `subscription.Usage` keeps its components additively.** It gained
`fresh_input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`
while `input_tokens` keeps meaning *the total*. The tempting alternative —
redefining `input_tokens` as fresh-only — is a trap, and I know because I set it
earlier today on the API path: changing a column's meaning mid-flight
invalidated my own before/after comparison and I could not tell whether thinking
had raised costs or whether the metric had simply started counting different
things. Additive keeps history comparable.

**2. Instrumentation is never conditional on configuration.** With no exporter
enabled the instruments still exist and still accept measurements — OTel returns
no-op instruments when no provider is installed. The alternative is
`if telemetry_enabled:` at every call site, which makes the code running in
production different from the code running under test.

**3. We hold our own MeterProvider reference** rather than relying on OTel's
global. `set_meter_provider` is one-shot per process — a second call logs
"Overriding of current MeterProvider is not allowed" and is *ignored* — so
whatever configures OTel first permanently captures our instruments. Found this
by writing a test that couldn't reconfigure, which is a better way to find it
than in production.

## The privacy guard, which is the load-bearing part

Claude Code can export **content**, not just counts: `OTEL_LOG_USER_PROMPTS`,
`OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`,
`OTEL_LOG_RAW_API_BODIES`. For Ask, prompts embed retrieved passages and tool
results *are* document passages — so enabling any of them ships the user's
archive to the telemetry backend and breaks the promise in `docs/ask.md` as
thoroughly as an indexing leak would, and far less visibly.

Three layers:

1. All five are **blanked** in the CLI's environment whether telemetry is on or
   off. Not omitted — `env` is *merged* over the inherited environment, so a
   container-level variable would otherwise reach the CLI. Same defence the
   existing `ANTHROPIC_API_KEY: ""` blanking uses.
2. The app **refuses to start** the subscription backend if any is set. Raising,
   not warning: a warning in a container log is not a control.
3. `OTEL_LOGS_EXPORTER=none`, since events are the other channel content travels.

Eight tests, including one per forbidden variable — a loop checking only the
first would pass while the other four sailed through.

This got more attention than it otherwise might because earlier today I leaked a
real policy number from the archive into a public repo. The lesson generalised:
the dangerous paths are the ones where content moves somewhere it is not
obviously moving.

## Two things the tooling caught that I had wrong

1. **Ruff found non-asserting tests.** Three "tests" were
   `assert_no_content_logging({}) is None` — a discarded comparison, not an
   assertion. `B015` flagged it. They now just call the function, since the
   contract is "does not raise".
2. **A gate I did not know existed.** `test_env_example_documents_every_setting`
   failed because I added five settings without documenting them in
   `.env.example`. Exactly the kind of promise-vs-reality check that usually
   *doesn't* exist.

## Verification

- 17 new tests. Full backend suite **1659 passed, 7 skipped** (was 1642).
- `ruff check`, `ruff format --check`, `mypy` clean.
- The end-to-end test records metrics then scrapes `GET /metrics` and asserts the
  series appear with `kind="cache_read"`. Every other test in that file exercises
  a function in isolation; this is the only one that would catch a reader never
  being attached to the provider.

## What is deliberately not done

1. **No dashboard, no alerts.** The metrics exist; nothing renders them and
   nothing fires on a cost spike. Next step, and a separate one.
2. **No verified scrape against the deployed host.** The Prometheus config in
   `docs/observability.md` is written from the endpoint's real output, not from
   an observed scrape. Stated as unverified in the doc's stamp.
3. **Ask only.** Ingestion, OCR, extraction and the job queue are uninstrumented.
4. **No traces.** Aggregate questions do not need them, and they cost far more to
   store. A decision to revisit, not a limitation to work around.
5. **The subscription cost is still priced at the full input rate.** Now that the
   components exist it is tempting to weight them 0.1x/1.25x like the API path —
   but that changes what the number *means*, and the current conservative
   ceiling is deliberate for an unmetered path. Separate decision.
