"""Fail-fast pricing validation (W18).

A configured ``*_model`` knob with no row in ``MODEL_PRICING_USD_PER_MTOK``
must be rejected at ``Settings`` construction, and ``estimate_cost_usd`` must
raise for an unpriced model rather than silently returning 0 — otherwise the
recorded cost and the daily-spend budget gate are both zeroed.
"""

import pytest
from pydantic import ValidationError

from library.config import _PRICED_MODEL_FIELDS, Settings
from library.extraction.extractor import estimate_cost_usd
from library.extraction.pricing import MODEL_PRICING_USD_PER_MTOK


@pytest.mark.parametrize("field", _PRICED_MODEL_FIELDS)
def test_unpriced_model_knob_rejected(field: str) -> None:
    """Each ``*_model`` knob set to an unknown model fails, naming the knob."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{field: "nonexistent-model"})
    message = str(excinfo.value)
    assert field in message
    assert "nonexistent-model" in message


def test_default_configured_models_all_have_pricing() -> None:
    """The default value of every priced knob has a pricing row.

    Guards against a future default drifting to an unpriced model.
    """
    settings = Settings(_env_file=None)
    for field in _PRICED_MODEL_FIELDS:
        model = getattr(settings, field)
        assert model in MODEL_PRICING_USD_PER_MTOK, f"{field}={model!r} unpriced"


def test_estimate_cost_usd_raises_for_unknown_model() -> None:
    """An unpriced model raises rather than returning 0.0."""
    with pytest.raises(KeyError):
        estimate_cost_usd("nonexistent", 1_000, 200)


def test_estimate_cost_usd_priced_model() -> None:
    """A priced model returns a positive, table-derived cost."""
    # claude-haiku-4-5 = (1.0, 5.0) USD per Mtok.
    cost = estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(6.0)


def test_every_model_field_is_validated() -> None:
    """Every Settings ``*_model`` field must be in ``_PRICED_MODEL_FIELDS``.

    ``_PRICED_MODEL_FIELDS`` is hand-maintained, so a future ``*_model`` field
    added to Settings but forgotten here would silently escape startup pricing
    validation and only surface as a runtime KeyError mid-pipeline. This guard
    fails loudly at test time instead.
    """
    model_fields = {name for name in Settings.model_fields if name.endswith("_model")}
    missing = model_fields - set(_PRICED_MODEL_FIELDS)
    assert not missing, (
        f"{sorted(missing)} not in _PRICED_MODEL_FIELDS — will escape pricing validation"
    )
