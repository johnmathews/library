"""OpenTelemetry metrics for the LLM surface.

WHAT THIS IS FOR
----------------
Ask's cost and behaviour were, until now, only visible by querying `ask_turns`
after the fact. That is fine for archaeology and useless for "is it behaving
right now" — and it could not answer the two questions that actually came up:
*is the prompt cache hitting?* and *did enabling adaptive thinking change what a
turn costs?* Both were answered once, by hand, on a sample of one.

A QUICK MAP OF THE OTel PIECES, since they are easy to conflate
---------------------------------------------------------------
    Instrument  — the thing you call: `counter.add(5, {...})`.
    Meter       — makes instruments. Namespaced ("library.ask").
    Reader      — decides WHEN measurements leave: on scrape (pull), or on a
                  timer (push).
    Exporter    — decides WHERE and in what wire format.
    MeterProvider — owns the readers and hands out meters.

So one provider can carry several readers at once, which is exactly what is
wanted here: Prometheus **pulls** from `GET /metrics`, and an OTLP exporter can
simultaneously **push** to a collector. Same instruments, same measurements, two
destinations, no double-counting — each reader gets its own view of the stream.

WHY METRICS AND NOT TRACES
--------------------------
The questions above are aggregate ones ("what is the cache hit rate this week").
Traces answer per-request questions and cost far more to store. Metrics first;
traces are a later decision, not a prerequisite.

CARDINALITY — the one way to hurt yourself with this file
---------------------------------------------------------
Every distinct combination of attribute values becomes a separate time series in
Prometheus. `backend` (2) x `model` (a handful) x `kind` (4) is fine. Adding
`thread_id`, `question`, or a user identifier would create an unbounded number
of series and eventually take the metrics store down. **Attributes here are
low-cardinality by construction, and any new one must be argued for.**

PRIVACY
-------
Nothing in this module records question text, answer text, document content, or
document identifiers. Ask prompts contain the user's archive, and this app's
stated contract is that document text does not leave the host. Metrics are
counts, sums and durations only. Keep it that way.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

_METER_NAME = "library.ask"

# Set once by `configure_telemetry`.
#
# We keep our OWN reference rather than relying solely on OTel's global. The
# global `set_meter_provider` is one-shot per process — a second call logs
# "Overriding of current MeterProvider is not allowed" and is ignored — so any
# component that configures OTel before us would permanently capture our
# instruments. Holding the provider here means this module's metrics behave the
# same whoever else is in the process, and it is what makes the exposition
# testable at all. The global is still set, best-effort, so third-party
# instrumentation lands in the same place.
_configured = False
_provider: MeterProvider | None = None


def configure_telemetry(
    *,
    service_name: str,
    service_version: str,
    prometheus_enabled: bool,
    otlp_endpoint: str | None,
    otlp_headers: str | None = None,
    export_interval_ms: int = 60_000,
) -> None:
    """Install the global MeterProvider. Safe to call twice; the second is a no-op.

    Both exporters are optional. With neither enabled the instruments below
    still exist and still accept measurements — they simply go nowhere. That is
    deliberate: instrumentation must never be conditional on configuration, or
    the call sites grow `if telemetry_enabled` branches and the code that runs
    in production stops being the code that runs in tests.
    """
    global _configured, _provider
    if _configured:
        return

    readers: list[Any] = []

    if prometheus_enabled:
        # Pull-based: this reader keeps the latest values in memory and the
        # /metrics route renders them when Prometheus scrapes. No timer.
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        readers.append(PrometheusMetricReader())

    if otlp_endpoint:
        # Push-based: a timer flushes to the collector every interval.
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        headers = _parse_otlp_headers(otlp_headers)
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint, headers=headers or None),
                export_interval_millis=export_interval_ms,
            )
        )

    if not readers:
        logger.info("telemetry: no exporter configured; metrics are recorded but not exported")

    _provider = MeterProvider(
        resource=Resource.create(
            {"service.name": service_name, "service.version": service_version}
        ),
        metric_readers=readers,
    )
    # Best-effort: warns and no-ops if something already claimed the global.
    # Our own instruments do not depend on this succeeding.
    metrics.set_meter_provider(_provider)
    _configured = True
    logger.info(
        "telemetry configured: prometheus=%s otlp=%s",
        prometheus_enabled,
        otlp_endpoint or "off",
    )


def _parse_otlp_headers(raw: str | None) -> dict[str, str]:
    """`"k=v,k2=v2"` -> dict, the format the OTEL_EXPORTER_OTLP_HEADERS spec uses.

    Malformed pairs are skipped rather than raised: a typo in an auth header
    should degrade telemetry, never stop the app from serving documents.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def _meter() -> metrics.Meter:
    """Our provider when we have one, else the global (a no-op if unconfigured)."""
    if _provider is not None:
        return _provider.get_meter(_METER_NAME)
    return metrics.get_meter(_METER_NAME)


# --- Instruments ------------------------------------------------------------
#
# Created lazily on first use so that importing this module has no side effects
# and so they bind to whatever provider `configure_telemetry` installed. The
# OTel API returns no-op instruments when no provider is set, which is what
# makes the "always instrument, never branch" rule above safe.

_instruments: dict[str, Any] = {}


def _counter(name: str, unit: str, description: str) -> Any:
    if name not in _instruments:
        _instruments[name] = _meter().create_counter(name, unit=unit, description=description)
    return _instruments[name]


def _histogram(name: str, unit: str, description: str) -> Any:
    if name not in _instruments:
        _instruments[name] = _meter().create_histogram(name, unit=unit, description=description)
    return _instruments[name]


def record_tokens(
    *,
    backend: str,
    model: str,
    fresh: int,
    cache_read: int,
    cache_write: int,
    output: int,
) -> None:
    """Token usage split by kind.

    `kind` is the attribute that earns this metric its keep: `cache_read` vs
    `fresh` is the prompt cache working or not, and that ratio was previously
    unobservable — the number was measured once by hand and never again.
    """
    counter = _counter("library.ask.tokens", "{token}", "Tokens consumed by an Ask turn")
    base = {"backend": backend, "model": model}
    for kind, value in (
        ("fresh", fresh),
        ("cache_read", cache_read),
        ("cache_write", cache_write),
        ("output", output),
    ):
        if value:
            counter.add(value, {**base, "kind": kind})


def record_cost(*, backend: str, model: str, usd: float) -> None:
    """Estimated spend. On the subscription backend this is notional — that path
    is not metered — so `backend` is not decoration, it changes what the number
    means."""
    if usd:
        _counter("library.ask.cost", "USD", "Estimated cost of an Ask turn").add(
            usd, {"backend": backend, "model": model}
        )


def record_turn(
    *,
    backend: str,
    model: str,
    duration_s: float,
    tool_calls: int,
    citations: int,
    outcome: str,
) -> None:
    """Shape of one completed turn.

    `tool_calls` exists to answer a specific open question: the loop's ceiling
    was raised from 4 to 8, and nothing shows whether the extra headroom is used
    or whether turns are still bunched at the old limit.
    """
    attrs = {"backend": backend, "model": model, "outcome": outcome}
    _counter("library.ask.turns", "{turn}", "Completed Ask turns").add(1, attrs)
    _histogram("library.ask.duration", "s", "Wall-clock time of an Ask turn").record(
        duration_s, attrs
    )
    _histogram(
        "library.ask.tool_calls", "{call}", "Tool-loop iterations used by an Ask turn"
    ).record(tool_calls, attrs)
    _histogram("library.ask.citations", "{citation}", "Citations returned by an Ask turn").record(
        citations, attrs
    )


def record_error(*, backend: str, kind: str) -> None:
    """Failures by category — currently only visible by grepping container logs.

    `kind` is a short closed-vocabulary slug (`subscription_auth`, `no_api_key`,
    `turn_limit`, `upstream`), never an exception message: messages are
    unbounded and would blow up cardinality.
    """
    _counter("library.ask.errors", "{error}", "Failed Ask turns by category").add(
        1, {"backend": backend, "kind": kind}
    )


@contextmanager
def timed() -> Iterator[dict[str, float]]:
    """Wall-clock timer. Yields a dict that gains `elapsed` on exit.

    A context manager rather than a decorator because the call site needs the
    duration *and* several other values (tokens, tool calls) to record together.
    """
    started = time.perf_counter()
    box: dict[str, float] = {}
    try:
        yield box
    finally:
        box["elapsed"] = time.perf_counter() - started
