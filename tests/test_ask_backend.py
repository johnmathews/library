"""Backend routing for ``ask`` (``run_ask`` / ``generate_thread_title``).

The point of these tests is *equivalence*: flipping ``ask_llm_backend`` must not
change what a caller sees or what gets persisted. The stored transcript in
particular is load-bearing — ``ask``'s write gate re-reads it on follow-up turns
— so a thread answered on one backend has to rehydrate correctly on the other.
"""

from pathlib import Path
from typing import Any, ClassVar

import pytest

from library.ask import engine
from library.ask.engine import AskResult, run_ask
from library.config import Settings
from library.llm.subscription import TextResult, ToolLoopResult, Usage


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "ask_model": "claude-opus-4-8",
    }
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.fixture(autouse=True)
def _no_citation_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Citation hydration needs the DB; it is covered by test_api_ask.py."""

    async def fake_citations(session: Any, ids: Any, pages: Any) -> list[Any]:
        return []

    monkeypatch.setattr(engine, "_citations_for", fake_citations)


def _sub_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return _settings(ask_llm_backend="subscription", claude_config_dir=tmp_path, **overrides)


def _stub_tool_loop(monkeypatch: pytest.MonkeyPatch, result: ToolLoopResult) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake_tool_loop(**kwargs: Any) -> ToolLoopResult:
        seen.update(kwargs)
        return result

    monkeypatch.setattr(engine.subscription, "tool_loop", fake_tool_loop)
    return seen


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


async def test_subscription_backend_passes_librarys_own_tools_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK path adds a transport, never a second tool implementation."""
    seen = _stub_tool_loop(
        monkeypatch, ToolLoopResult(answer="It is #7.", blocks=[], used_tools=[])
    )

    await run_ask(
        object(),  # type: ignore[arg-type]
        question="where is my gas bill",
        settings=_sub_settings(tmp_path, ask_max_tool_turns=6),
        client=object(),  # type: ignore[arg-type]
    )

    assert seen["tools"] is engine.TOOLS
    assert seen["model"] == "claude-opus-4-8"
    assert seen["max_turns"] == 6
    assert seen["config_dir"] == tmp_path
    assert seen["question"] == "where is my gas bill"
    # Same system prompt text as the API path builds.
    assert "cite" in seen["system_prompt"].lower() or seen["system_prompt"]


async def test_subscription_dispatcher_reaches_librarys_tool_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dispatch`` must be a closure over ``_dispatch_tool`` with turn state bound."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_dispatch_tool(
        session: Any,
        settings: Any,
        name: str,
        args: dict[str, Any],
        cited: set[int],
        pages: dict[int, int],
        editable_ids: set[int],
        previewed_ids: set[int],
    ) -> dict[str, Any]:
        calls.append((name, args))
        cited.add(7)  # what a read tool does: mark a document citable
        return {"documents": [{"id": 7}]}

    monkeypatch.setattr(engine, "_dispatch_tool", fake_dispatch_tool)
    seen = _stub_tool_loop(
        monkeypatch, ToolLoopResult(answer="It is #7.", blocks=[], used_tools=["semantic_search"])
    )

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
    )

    # Invoking the handed-over dispatcher reaches library's dispatch with the
    # turn's mutable state bound.
    await seen["dispatch"]("semantic_search", {"query": "gas"})
    assert calls == [("semantic_search", {"query": "gas"})]
    assert result.used_tools == ["semantic_search"]


async def test_images_and_history_reach_the_subscription_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub_tool_loop(monkeypatch, ToolLoopResult(answer="a"))
    history = [{"role": "assistant", "content": [{"type": "text", "text": "earlier"}]}]
    images = [{"media_type": "image/png", "data": "AAAA"}]

    await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
        history_messages=history,
        images=images,
    )

    assert seen["history_blocks"] == history
    assert seen["images"] == images


# --------------------------------------------------------------------------
# Result equivalence
# --------------------------------------------------------------------------


async def test_stored_turn_opens_with_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The question is not in the SDK's reply stream, so it must be prepended.

    Without it a thread rehydrates missing the user's own turn, and the next
    question lands against a transcript that starts with an assistant message.
    """
    _stub_tool_loop(
        monkeypatch,
        ToolLoopResult(
            answer="It is #7.",
            blocks=[{"role": "assistant", "content": [{"type": "text", "text": "It is #7."}]}],
        ),
    )

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="where is my gas bill",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
        images=[{"media_type": "image/png", "data": "AAAA"}],
    )

    first = result.turn_messages[0]
    assert first["role"] == "user"
    assert first["content"][0] == {"type": "text", "text": "where is my gas bill"}
    # Attachments are part of the stored question turn on both backends.
    assert first["content"][1]["type"] == "image"
    assert result.turn_messages[-1]["role"] == "assistant"


async def test_write_gate_state_survives_a_subscription_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subscription turn must persist tool_use blocks the gate can re-read.

    ``_ids_from_history`` reconstructs which documents a later turn may edit by
    parsing stored ``tool_use``/``tool_result`` blocks. If the SDK path stored a
    different shape, follow-up turns would silently lose their edit permissions.
    """
    blocks = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "semantic_search",
                    "input": {"query": "gas"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": '{"documents": [{"id": 7}]}',
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "It is #7."}]},
    ]
    _stub_tool_loop(monkeypatch, ToolLoopResult(answer="It is #7.", blocks=blocks))

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
    )

    # Feeding the stored turn back as history recovers the editable ids.
    assert engine._ids_from_history(result.turn_messages) == {7}


async def test_turn_limit_without_an_answer_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_tool_loop(monkeypatch, ToolLoopResult(answer="", hit_turn_limit=True))

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
    )

    assert result.answer == engine._NO_ANSWER


async def test_cost_records_the_true_context_including_harness_overhead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under a subscription cost_usd is notional — but it must not read as free.

    Recording only the prompt would hide the ~43k-token Claude Code preamble
    each opus call carries, which is the real resource being spent (quota).
    """
    _stub_tool_loop(
        monkeypatch,
        ToolLoopResult(answer="a", usage=Usage(input_tokens=43_320, output_tokens=200)),
    )

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
    )

    assert result.input_tokens == 43_320
    assert result.output_tokens == 200
    assert result.cost_usd > 0.2  # opus-4-8 at $5/MTok in


async def test_result_shape_matches_the_api_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both backends return the same dataclass with every field populated."""
    _stub_tool_loop(
        monkeypatch,
        ToolLoopResult(answer="a", used_tools=["semantic_search"], usage=Usage(1, 2)),
    )

    result = await run_ask(
        object(),  # type: ignore[arg-type]
        question="q",
        settings=_sub_settings(tmp_path),
        client=object(),  # type: ignore[arg-type]
    )

    assert isinstance(result, AskResult)
    assert result.model == "claude-opus-4-8"
    assert result.citations == []
    assert result.used_tools == ["semantic_search"]
    assert result.turn_messages


# --------------------------------------------------------------------------
# Thread titles
# --------------------------------------------------------------------------


async def test_title_follows_the_ask_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subscription deployment must not still need an API key for titles."""
    seen: dict[str, Any] = {}

    async def fake_text_call(**kwargs: Any) -> TextResult:
        seen.update(kwargs)
        return TextResult(text="Gas bill location", usage=Usage(32_000, 8))

    monkeypatch.setattr(engine.subscription, "text_call", fake_text_call)

    title = await engine.generate_thread_title(
        object(),  # type: ignore[arg-type]
        model="claude-haiku-4-5",
        question="where is my gas bill",
        answer="It is #7.",
        settings=_sub_settings(tmp_path),
    )

    assert title.title == "Gas bill location"
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["config_dir"] == tmp_path


async def test_title_without_settings_uses_the_messages_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone backfill script passes no settings and must keep working."""

    async def explode(**kwargs: Any) -> TextResult:
        raise AssertionError("must not use the subscription backend")

    monkeypatch.setattr(engine.subscription, "text_call", explode)

    class _Usage:
        input_tokens = 10
        output_tokens = 4

    class _Response:
        content: ClassVar[list[Any]] = [type("B", (), {"type": "text", "text": "A title"})()]
        usage = _Usage()

    class _Messages:
        async def create(self, **kwargs: Any) -> Any:
            return _Response()

    class _Client:
        messages = _Messages()

    title = await engine.generate_thread_title(
        _Client(),  # type: ignore[arg-type]
        model="claude-haiku-4-5",
        question="q",
        answer="a",
    )

    assert title.title == "A title"
