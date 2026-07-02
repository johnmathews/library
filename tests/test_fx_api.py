"""Unit tests for the live FX provider client (mocked HTTP transport)."""

from decimal import Decimal

import httpx
import pytest

from library.config import Settings
from library.fx_api import FxApiError, fetch_rate_to_base


def _settings(**overrides: object) -> Settings:
    return Settings(fx_api_url="https://fx.test/v6/latest", **overrides)


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _ok(rates: dict[str, float]) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/USD")
        return httpx.Response(200, json={"result": "success", "base_code": "USD", "rates": rates})

    return handler


async def test_inverts_usd_per_unit_to_rate_to_base() -> None:
    # 0.8 EUR per 1 USD -> one EUR is worth 1/0.8 = 1.25 USD.
    rate = await fetch_rate_to_base("EUR", settings=_settings(), client=_client(_ok({"EUR": 0.8})))
    assert rate == Decimal("1.25000000")
    assert isinstance(rate, Decimal)


async def test_quantizes_to_eight_dp() -> None:
    rate = await fetch_rate_to_base("JPY", settings=_settings(), client=_client(_ok({"JPY": 150})))
    assert rate == Decimal("0.00666667")  # 1/150, rounded to 8dp


async def test_usd_is_base_without_a_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no HTTP call expected for the base currency")

    assert await fetch_rate_to_base(
        "usd", settings=_settings(), client=_client(handler)
    ) == Decimal(1)


async def test_unlisted_currency_returns_none() -> None:
    rate = await fetch_rate_to_base("XZZ", settings=_settings(), client=_client(_ok({"EUR": 0.8})))
    assert rate is None


async def test_transport_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(FxApiError):
        await fetch_rate_to_base("EUR", settings=_settings(), client=_client(handler))


async def test_non_json_body_raises_fx_api_error() -> None:
    # A 200 with a non-JSON body (e.g. an HTML rate-limit page) must degrade to
    # FxApiError, not an uncaught JSONDecodeError (which would 500 the route).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>rate limited</html>")

    with pytest.raises(FxApiError):
        await fetch_rate_to_base("EUR", settings=_settings(), client=_client(handler))


async def test_non_success_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "error", "error-type": "unsupported-code"})

    with pytest.raises(FxApiError):
        await fetch_rate_to_base("EUR", settings=_settings(), client=_client(handler))


async def test_non_positive_rate_raises() -> None:
    with pytest.raises(FxApiError):
        await fetch_rate_to_base("EUR", settings=_settings(), client=_client(_ok({"EUR": 0})))
