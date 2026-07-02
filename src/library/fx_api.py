"""Live foreign-exchange rate lookup, base = USD.

Backs the admin "Fetch rate" affordance (see ``fx_admin``): fetches the current
USD-per-unit rate for a currency so a seeded ``fx_rates`` row makes conversion
resolve. Uses ``open.er-api.com`` — free, no API key — which returns *USD->X*
rates (units of X per one USD). Since ``rate_to_base`` is the value of one unit
of X in USD, we invert: ``rate_to_base(X) = 1 / rates[X]``.

The result is a ``Decimal`` quantized to 8 dp to match ``FxRate.rate_to_base``
(``Numeric(18,8)``); money never becomes a float. ``None`` is returned when the
provider simply doesn't list the currency (a caller can then fall back to manual
entry); a transport failure or an error payload raises ``FxApiError``.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from library.config import Settings

# Eight fractional digits, matching FxRate.rate_to_base's Numeric(18,8).
_RATE_QUANT = Decimal("0.00000001")


class FxApiError(RuntimeError):
    """The FX provider was unreachable or returned an unusable response."""


async def fetch_rate_to_base(
    currency: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> Decimal | None:
    """The live USD-per-unit rate for ``currency`` (``None`` if unlisted).

    ``currency`` is expected already-normalised (upper-case ISO-4217 shape).
    USD is the base and returns ``Decimal(1)`` without a network call. Raises
    ``FxApiError`` on transport failure or a non-success payload.
    """
    code = currency.strip().upper()
    if code == "USD":
        return Decimal(1)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.fx_api_timeout_s)
    url = settings.fx_api_url.rstrip("/") + "/USD"
    try:
        response = await client.get(url)
        response.raise_for_status()
        # A keyless public API can answer 200 with a non-JSON body (an HTML rate-
        # limit/error page); json() then raises ValueError. Fold both into
        # FxApiError so the route degrades to the manual-entry path (502), never 500.
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FxApiError(f"FX provider request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, dict) or payload.get("result") != "success":
        raise FxApiError(f"FX provider returned a non-success payload: {payload!r:.200}")
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise FxApiError("FX provider payload had no rates map")

    per_usd = rates.get(code)
    if per_usd is None:
        return None
    try:
        per_usd_dec = Decimal(str(per_usd))
    except (ArithmeticError, ValueError) as exc:
        raise FxApiError(f"FX provider gave a non-numeric rate for {code}: {per_usd!r}") from exc
    if per_usd_dec <= 0:
        raise FxApiError(f"FX provider gave a non-positive rate for {code}: {per_usd_dec}")
    return (Decimal(1) / per_usd_dec).quantize(_RATE_QUANT)
