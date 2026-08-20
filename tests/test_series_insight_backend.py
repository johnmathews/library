"""Backend routing for series-insight descriptions (``describe_series``).

These are deliberately DB-free: what is under test is which transport gets used
and how its result is shaped, not the surrounding persistence (covered by
``test_series_insight.py``).
"""

from pathlib import Path
from typing import Any

import pytest

from library import series_insight
from library.config import Settings
from library.llm.subscription import TextResult, Usage


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"database_url": "postgresql+asyncpg://u:p@localhost/db"}
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.fixture
def summary(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A stand-in summary; prompt building is tested in test_series_insight.py."""
    monkeypatch.setattr(
        series_insight, "build_series_prompt", lambda summary, overrides=(): "PROMPT"
    )
    return object()


async def test_api_backend_uses_the_messages_api(
    summary: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake_generate(client: Any, model: str, s: Any, o: Any = ()) -> tuple[str, int, int]:
        calls.append((client, model))
        return ("Quarterly gas bills.", 120, 40)

    monkeypatch.setattr(series_insight, "generate_description", fake_generate)
    sentinel = object()

    result = await series_insight.describe_series(
        _settings(),
        summary,
        client=sentinel,  # type: ignore[arg-type]
    )

    assert result == ("Quarterly gas bills.", 120, 40)
    assert calls == [(sentinel, "claude-haiku-4-5")]


async def test_api_backend_without_a_key_returns_none(summary: Any) -> None:
    """Callers translate None into "skip this series" — it must not raise."""
    assert await series_insight.describe_series(_settings(), summary) is None


async def test_subscription_backend_uses_the_agent_sdk(
    summary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_text_call(**kwargs: Any) -> TextResult:
        seen.update(kwargs)
        return TextResult(
            text="Quarterly gas bills.", usage=Usage(input_tokens=32120, output_tokens=40)
        )

    monkeypatch.setattr(series_insight.subscription, "text_call", fake_text_call)

    result = await series_insight.describe_series(
        _settings(series_insight_llm_backend="subscription", claude_config_dir=tmp_path),
        summary,
    )

    assert result == ("Quarterly gas bills.", 32120, 40)
    assert seen["config_dir"] == tmp_path
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["system_prompt"] == series_insight.SERIES_SYSTEM_PROMPT
    assert seen["prompt"] == "PROMPT"


async def test_subscription_backend_ignores_an_injected_api_client(
    summary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray API client must not silently re-route a subscription call."""

    async def fake_text_call(**kwargs: Any) -> TextResult:
        return TextResult(text="ok", usage=Usage(input_tokens=1, output_tokens=1))

    async def explode(*args: Any, **kwargs: Any) -> tuple[str, int, int]:
        raise AssertionError("the Messages API path must not be taken")

    monkeypatch.setattr(series_insight.subscription, "text_call", fake_text_call)
    monkeypatch.setattr(series_insight, "generate_description", explode)

    result = await series_insight.describe_series(
        _settings(series_insight_llm_backend="subscription", claude_config_dir=tmp_path),
        summary,
        client=object(),  # type: ignore[arg-type]
    )

    assert result is not None


async def test_subscription_usage_carries_the_harness_tax_into_cost(
    summary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded cost must reflect the ~32k tax, not just the prompt.

    This is the number that makes the bad trade visible: a bounded Haiku prompt
    that would cost a fraction of a cent on the API records the true context it
    put on the wire when routed through the SDK.
    """
    from library.extraction.extractor import estimate_cost_usd

    async def fake_text_call(**kwargs: Any) -> TextResult:
        return TextResult(text="d", usage=Usage(input_tokens=32120, output_tokens=40))

    monkeypatch.setattr(series_insight.subscription, "text_call", fake_text_call)

    described = await series_insight.describe_series(
        _settings(series_insight_llm_backend="subscription", claude_config_dir=tmp_path),
        summary,
    )
    assert described is not None
    _, input_tokens, output_tokens = described

    api_equivalent = estimate_cost_usd("claude-haiku-4-5", 120, 40)
    via_sdk = estimate_cost_usd("claude-haiku-4-5", input_tokens, output_tokens)
    assert via_sdk > api_equivalent * 50
