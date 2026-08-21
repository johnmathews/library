"""Tests for the OpenTelemetry metrics surface and its privacy guard.

The guard tests are the load-bearing ones. Everything else here protects a
dashboard; those protect the promise that document text does not leave the host.
"""

from __future__ import annotations

import pytest

from library import telemetry
from library.config import Settings
from library.llm.subscription import (
    _CONTENT_LOGGING_VARS,
    SubscriptionBackendError,
    _telemetry_env,
    assert_no_content_logging,
)

# --- the privacy guard ------------------------------------------------------


@pytest.mark.parametrize("var", _CONTENT_LOGGING_VARS)
def test_content_logging_is_refused_for_every_variable(var: str) -> None:
    """Each variable individually, not just one representative.

    A loop that checked only the first would pass while the other four sailed
    through, and the whole point is that ANY of them exports document text.
    """
    with pytest.raises(SubscriptionBackendError) as excinfo:
        assert_no_content_logging({var: "1"})
    assert var in str(excinfo.value)


def test_content_logging_guard_is_silent_when_clean() -> None:
    # No assertion needed: the contract is "does not raise". Writing
    # `assert f(...) is None` here would be a discarded comparison, not a check.
    assert_no_content_logging({})


def test_content_logging_guard_ignores_empty_and_whitespace_values() -> None:
    """An explicitly-blanked variable is how this code DISABLES the setting, so
    treating it as "enabled" would make the guard refuse its own remedy."""
    assert_no_content_logging(dict.fromkeys(_CONTENT_LOGGING_VARS, ""))
    assert_no_content_logging({_CONTENT_LOGGING_VARS[0]: "   "})


def test_cli_env_blanks_content_vars_even_when_telemetry_is_off() -> None:
    """`env` is MERGED over the inherited environment, so leaving these unset
    would let a container-level variable through to the CLI. They must be
    actively set to empty, not omitted."""
    env = _telemetry_env(Settings(claude_code_telemetry_enabled=False))
    for var in _CONTENT_LOGGING_VARS:
        assert env[var] == "", f"{var} must be blanked, not omitted"
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == ""


def test_cli_env_blanks_content_vars_when_telemetry_is_on() -> None:
    env = _telemetry_env(
        Settings(
            claude_code_telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://collector:4318/v1/metrics",
        )
    )
    for var in _CONTENT_LOGGING_VARS:
        assert env[var] == ""
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"


def test_cli_env_disables_the_logs_exporter() -> None:
    """Events are the other channel content can travel down. Metrics only."""
    env = _telemetry_env(Settings(claude_code_telemetry_enabled=True))
    assert env["OTEL_LOGS_EXPORTER"] == "none"
    assert env["OTEL_TRACES_EXPORTER"] == "none"
    assert env["OTEL_METRICS_EXPORTER"] == "otlp"


def test_cli_env_strips_the_metrics_path_from_the_endpoint() -> None:
    """The in-process exporter takes the full signal URL; the CLI takes the base
    and appends the path itself. Configuring one value and deriving the other
    beats asking an operator to keep two in agreement."""
    env = _telemetry_env(
        Settings(
            claude_code_telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://collector:4318/v1/metrics",
        )
    )
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"


# --- header parsing ---------------------------------------------------------


def test_otlp_headers_parse_to_a_dict() -> None:
    assert telemetry._parse_otlp_headers("Authorization=Bearer x,team=library") == {
        "Authorization": "Bearer x",
        "team": "library",
    }


def test_otlp_headers_tolerate_malformed_input() -> None:
    """A typo in an auth header should degrade telemetry, never stop the app
    serving documents — so bad pairs are skipped rather than raised."""
    assert telemetry._parse_otlp_headers("") == {}
    assert telemetry._parse_otlp_headers(None) == {}
    assert telemetry._parse_otlp_headers("no-equals-sign") == {}
    assert telemetry._parse_otlp_headers("=novalue,good=yes") == {"good": "yes"}


# --- instruments are always safe to call ------------------------------------


def test_recording_without_a_configured_provider_is_a_no_op() -> None:
    """Instrumentation must never be conditional on configuration.

    If these raised when telemetry is off, every call site would grow an
    `if enabled:` branch and the code running in production would stop being the
    code running under test. The OTel API returns no-op instruments when no
    provider is installed, which is what makes that safe — this test pins it.
    """
    telemetry.record_tokens(
        backend="api", model="m", fresh=1, cache_read=2, cache_write=3, output=4
    )
    telemetry.record_cost(backend="api", model="m", usd=0.5)
    telemetry.record_turn(
        backend="api", model="m", duration_s=1.0, tool_calls=2, citations=3, outcome="ok"
    )
    telemetry.record_error(backend="api", kind="upstream")


def test_timed_reports_elapsed_even_when_the_body_raises() -> None:
    """The duration of a FAILED turn is exactly what you want when latency
    spikes, so the timer must not be skipped by an exception."""
    with pytest.raises(ValueError), telemetry.timed() as clock:
        raise ValueError("boom")
    assert clock["elapsed"] >= 0


# --- the endpoint, end to end -----------------------------------------------


def test_metrics_endpoint_is_404_when_disabled(api_client) -> None:  # type: ignore[no-untyped-def]
    """404 rather than an empty 200. An empty exposition is indistinguishable
    from "running and recording nothing", so a scrape would look healthy while
    collecting no series at all."""
    assert api_client.get("/metrics").status_code == 404


def test_metrics_endpoint_exposes_recorded_series(monkeypatch, api_client) -> None:  # type: ignore[no-untyped-def]
    """The outcome, not the plumbing: after recording, does a scrape actually
    return the series? Everything else in this file tests a function in
    isolation; this is the only test that would catch the reader never being
    attached to the provider."""
    from library.config import get_settings
    from library.telemetry import configure_telemetry

    monkeypatch.setenv("LIBRARY_OTEL_METRICS_ENABLED", "true")
    get_settings.cache_clear()

    # `configure_telemetry` is idempotent by design (a global provider is a
    # process-level fact), so force a fresh one for this assertion.
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr(telemetry, "_instruments", {})
    configure_telemetry(
        service_name="library-test",
        service_version="0",
        prometheus_enabled=True,
        otlp_endpoint=None,
    )

    telemetry.record_tokens(
        backend="api", model="test-model", fresh=10, cache_read=90, cache_write=5, output=7
    )
    telemetry.record_cost(backend="api", model="test-model", usd=0.25)

    body = api_client.get("/metrics").text
    assert "library_ask_tokens" in body
    # The attribute that earns the metric its keep: without `kind` the cache hit
    # rate is unobservable, which is the question this whole change exists for.
    assert 'kind="cache_read"' in body
    assert 'kind="fresh"' in body
    assert "library_ask_cost" in body

    get_settings.cache_clear()
