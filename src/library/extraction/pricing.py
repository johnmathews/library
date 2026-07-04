"""Static model pricing table.

A leaf module (no intra-package imports) so both ``config.py`` — which
validates configured ``*_model`` knobs against it at startup — and
``extractor.py`` — which estimates per-call cost — can import it without a
circular import.
"""

# USD per million tokens (input, output), June 2026 list prices.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}
