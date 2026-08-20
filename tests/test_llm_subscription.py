"""Tests for the Claude Agent SDK adapter (``library.llm.subscription``).

The SDK itself is never exercised here — ``query`` is replaced with a stub that
yields real SDK message dataclasses. What is under test is library's side of the
contract: option construction, the tool bridge, and the translation back into
Anthropic message blocks that keeps ask threads backend-portable.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    Message,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from library.llm import subscription
from library.llm.subscription import SubscriptionBackendError, Usage


def _assistant(*blocks: Any) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="claude-opus-4-8")


def _result(
    *,
    usage: dict[str, Any] | None = None,
    subtype: str = "success",
    is_error: bool = False,
    result: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        usage=usage,
        result=result,
    )


def _stub_query(monkeypatch: pytest.MonkeyPatch, messages: list[Message]) -> list[dict[str, Any]]:
    """Replace ``query`` with a stub yielding ``messages``; record its kwargs."""
    seen: list[dict[str, Any]] = []

    def fake_query(*, prompt: Any, options: Any, **_: Any) -> AsyncIterator[Message]:
        async def gen() -> AsyncIterator[Message]:
            seen.append({"prompt": prompt, "options": options})
            for message in messages:
                yield message

        return gen()

    monkeypatch.setattr(subscription, "query", fake_query)
    return seen


@pytest.fixture(autouse=True)
def _no_oauth_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token refresh has its own tests; keep these off the network."""

    async def noop(config_dir: Path) -> None:
        return None

    monkeypatch.setattr(subscription.oauth, "ensure_valid_token", noop)


# --------------------------------------------------------------------------
# Usage accounting
# --------------------------------------------------------------------------


def test_usage_counts_cache_tokens_as_input() -> None:
    """The ~32k harness tax arrives as cache_creation; it must not be hidden.

    Counting only ``input_tokens`` would report a 3-token prompt for a call that
    actually put 32k of Claude Code system prompt on the wire, making the
    subscription backend look free when its real cost is quota.
    """
    usage = Usage()
    usage.add(
        {
            "input_tokens": 3,
            "cache_creation_input_tokens": 32231,
            "cache_read_input_tokens": 100,
            "output_tokens": 4,
        }
    )
    assert usage.input_tokens == 3 + 32231 + 100
    assert usage.output_tokens == 4


def test_usage_tolerates_missing_and_null_fields() -> None:
    usage = Usage()
    usage.add(None)
    usage.add({})
    usage.add({"input_tokens": None, "output_tokens": 5})
    assert (usage.input_tokens, usage.output_tokens) == (0, 5)


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


def test_options_blank_the_api_key_for_the_subprocess(tmp_path: Path) -> None:
    """The outage this guards: the CLI ranks ANTHROPIC_API_KEY above OAuth.

    Library sets that variable for the API backend. If the CLI subprocess can
    see it, it sends an X-Api-Key header carrying an OAuth token and fails with
    "Invalid API key" instead of using the credentials file that would work.
    """
    options = subscription.build_options(
        model="claude-opus-4-8", system_prompt="s", config_dir=tmp_path, max_turns=8
    )
    assert options.env["ANTHROPIC_API_KEY"] == ""
    assert options.env["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_options_block_every_builtin_and_ignore_host_settings(tmp_path: Path) -> None:
    options = subscription.build_options(
        model="claude-opus-4-8", system_prompt="s", config_dir=tmp_path, max_turns=8
    )
    for builtin in ("Read", "Write", "Bash", "WebFetch", "Task", "ToolSearch"):
        assert builtin in options.disallowed_tools
    # Host CLAUDE.md / settings must not leak into library's prompts.
    assert options.setting_sources == []


def test_options_authorise_only_librarys_mcp_tools(tmp_path: Path) -> None:
    with_tools = subscription.build_options(
        model="m", system_prompt="s", config_dir=tmp_path, max_turns=8, mcp_servers={"library": {}}
    )
    assert with_tools.allowed_tools == ["mcp__library__*"]

    without = subscription.build_options(
        model="m", system_prompt="s", config_dir=tmp_path, max_turns=1
    )
    assert without.allowed_tools == []


# --------------------------------------------------------------------------
# text_call
# --------------------------------------------------------------------------


async def test_text_call_returns_text_and_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_query(
        monkeypatch,
        [
            _assistant(TextBlock(text="Quarterly gas bills from Octopus.")),
            _result(
                usage={"input_tokens": 10, "cache_creation_input_tokens": 32000, "output_tokens": 7}
            ),
        ],
    )

    result = await subscription.text_call(
        config_dir=tmp_path, model="claude-haiku-4-5", system_prompt="sys", prompt="describe"
    )

    assert result.text == "Quarterly gas bills from Octopus."
    assert result.usage.input_tokens == 32010
    assert result.usage.output_tokens == 7


async def test_error_result_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_query(
        monkeypatch,
        [_result(subtype="error_during_execution", is_error=True, result="CLI exited 1")],
    )

    with pytest.raises(SubscriptionBackendError, match="CLI exited 1"):
        await subscription.text_call(config_dir=tmp_path, model="m", system_prompt="s", prompt="p")


async def test_max_turns_result_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running out of turns is a normal outcome the caller handles, not a crash."""
    _stub_query(
        monkeypatch,
        [
            _assistant(TextBlock(text="partial")),
            _result(subtype="error_max_turns", is_error=True),
        ],
    )

    result = await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="q",
        tools=[],
        dispatch=_unused_dispatch,
        max_turns=2,
    )
    assert result.hit_turn_limit is True
    assert result.answer == "partial"


async def _unused_dispatch(name: str, args: dict[str, Any]) -> Any:  # pragma: no cover
    raise AssertionError("dispatch should not be called")


# --------------------------------------------------------------------------
# Tool bridge
# --------------------------------------------------------------------------


async def test_tool_loop_bridges_to_the_callers_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Library's own ``_dispatch_tool`` must be the only tool implementation."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(name: str, args: dict[str, Any]) -> Any:
        calls.append((name, args))
        return {"documents": [{"id": 7}]}

    _stub_query(monkeypatch, [_assistant(TextBlock(text="done")), _result()])

    tools = [
        {"name": "search_documents", "description": "d", "input_schema": {"type": "object"}},
    ]
    seen_servers: dict[str, Any] = {}

    real_build = subscription._build_mcp_server

    def capture(tools_arg: Any, dispatch_arg: Any, used: list[str]) -> Any:
        seen_servers["handlers"] = {t["name"]: t for t in tools_arg}
        seen_servers["dispatch"] = dispatch_arg
        seen_servers["used"] = used
        return real_build(tools_arg, dispatch_arg, used)

    monkeypatch.setattr(subscription, "_build_mcp_server", capture)

    await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="q",
        tools=tools,
        dispatch=dispatch,
        max_turns=4,
    )

    # The bridge received library's dispatcher verbatim; invoking it reaches it.
    assert seen_servers["dispatch"] is dispatch
    await seen_servers["dispatch"]("search_documents", {"query": "gas"})
    assert calls == [("search_documents", {"query": "gas"})]


async def test_tool_loop_reports_used_tools_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_query(monkeypatch, [_assistant(TextBlock(text="ok")), _result()])

    async def dispatch(name: str, args: dict[str, Any]) -> Any:
        return {}

    captured: dict[str, list[str]] = {}
    real_build = subscription._build_mcp_server

    def capture(tools_arg: Any, dispatch_arg: Any, used: list[str]) -> Any:
        captured["used"] = used
        used.extend(["search_documents", "get_document", "search_documents"])
        return real_build(tools_arg, dispatch_arg, used)

    monkeypatch.setattr(subscription, "_build_mcp_server", capture)

    result = await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="q",
        tools=[],
        dispatch=dispatch,
        max_turns=4,
    )

    assert result.used_tools == ["search_documents", "get_document"]


# --------------------------------------------------------------------------
# Block translation — what keeps threads portable across backends
# --------------------------------------------------------------------------


async def test_tool_loop_rebuilds_anthropic_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored turn must look exactly like the API backend's.

    ``ask``'s write-permission gate re-reads stored ``tool_use`` blocks to decide
    what a later turn may edit, so an SDK-shaped transcript would silently
    disable that gate on follow-ups.
    """
    _stub_query(
        monkeypatch,
        [
            _assistant(
                TextBlock(text="Looking that up."),
                ToolUseBlock(
                    id="toolu_1", name="mcp__library__search_documents", input={"query": "gas"}
                ),
            ),
            UserMessage(
                content=[ToolResultBlock(tool_use_id="toolu_1", content='{"documents": [7]}')]
            ),
            _assistant(TextBlock(text="Your gas bill is #7.")),
            _result(),
        ],
    )

    result = await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="where is my gas bill",
        tools=[],
        dispatch=_unused_dispatch,
        max_turns=4,
    )

    assert result.answer == "Looking that up.Your gas bill is #7."
    assert result.blocks == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking that up."},
                # Namespacing stripped: library's own tool name is what the
                # history parser looks for.
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search_documents",
                    "input": {"query": "gas"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"documents": [7]}',
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Your gas bill is #7."}]},
    ]


async def test_turn_ending_on_a_tool_result_is_closed_with_an_assistant_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive user turns would 400 the Messages API on the next question."""
    _stub_query(
        monkeypatch,
        [
            _assistant(ToolUseBlock(id="t1", name="mcp__library__search_documents", input={})),
            UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="{}")]),
            _result(subtype="error_max_turns", is_error=True),
        ],
    )

    result = await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="q",
        tools=[],
        dispatch=_unused_dispatch,
        max_turns=1,
    )

    assert result.blocks[-1]["role"] == "assistant"


async def test_images_are_sent_as_content_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask supports attachments; verified end-to-end against the live API in spike.py."""
    seen = _stub_query(monkeypatch, [_assistant(TextBlock(text="ok")), _result()])

    await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="what is this",
        tools=[],
        dispatch=_unused_dispatch,
        max_turns=2,
        images=[{"media_type": "image/jpeg", "data": "BASE64DATA"}],
    )

    messages = [message async for message in seen[0]["prompt"]]
    content = messages[0]["message"]["content"]
    assert content[0]["type"] == "text"
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "BASE64DATA"},
    }


# --------------------------------------------------------------------------
# History rendering
# --------------------------------------------------------------------------


def test_render_history_is_empty_for_a_first_turn() -> None:
    assert subscription.render_history([]) == ""


def test_render_history_renders_text_tool_calls_and_results() -> None:
    rendered = subscription.render_history(
        [
            {"role": "user", "content": [{"type": "text", "text": "where is my gas bill"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "search_documents",
                        "input": {"q": "gas"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "It is #7."}]},
        ]
    )

    assert rendered.startswith("<conversation_history>")
    assert rendered.endswith("</conversation_history>")
    assert "<user>where is my gas bill</user>" in rendered
    assert '<tool_call name="search_documents">' in rendered
    assert "<tool_result>{}</tool_result>" in rendered
    assert "<assistant>It is #7.</assistant>" in rendered


def test_render_history_accepts_plain_string_content() -> None:
    rendered = subscription.render_history([{"role": "user", "content": "hello"}])
    assert "<user>hello</user>" in rendered


async def test_history_is_prepended_to_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub_query(monkeypatch, [_assistant(TextBlock(text="ok")), _result()])

    await subscription.tool_loop(
        config_dir=tmp_path,
        model="m",
        system_prompt="s",
        question="and the one before that?",
        tools=[],
        dispatch=_unused_dispatch,
        max_turns=2,
        history_blocks=[{"role": "assistant", "content": [{"type": "text", "text": "It is #7."}]}],
    )

    messages = [message async for message in seen[0]["prompt"]]
    text = messages[0]["message"]["content"][0]["text"]
    assert "<assistant>It is #7.</assistant>" in text
    assert text.endswith("and the one before that?")
