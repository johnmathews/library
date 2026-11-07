"""The subscription backend offers library's tools and nothing else — checked
against the real Claude Code CLI bundled with ``claude-agent-sdk``.

``test_llm_subscription.py`` stubs ``query`` and so can only check which options
library passes. Whether those options actually keep a tool out is a fact about
the CLI, and it changes between releases: CLI 2.1.237 offered 23 built-ins that
library's blocklist did not name, plus every connector on the claude.ai account
behind the credentials. Ask runs with ``bypassPermissions``, so an offered tool
is a callable tool.

The CLI announces its tool list in its ``init`` system message before it talks
to the API, so these tests need no credentials. Each one runs the CLI with an
empty config directory and an API address that refuses connections, and
disconnects as soon as the ``init`` message arrives: no call can be billed, on a
CI runner or on a developer machine whose keychain holds real credentials.

Limit: with no credentials the CLI has no account to load connectors from, so
this cannot show a connector leaking. ``strict_mcp_config`` covers that, pinned
in ``test_llm_subscription.py`` and measured live in ``docs/llm-backends.md`` §3.
"""

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, SystemMessage

from library.ask.engine import TOOLS
from library.llm import subscription

# Nothing listens on port 9 (discard), so any API request fails immediately.
_DEAD_API = "http://127.0.0.1:9"


def _isolated(options: ClaudeAgentOptions, home: Path) -> ClaudeAgentOptions:
    """The same options, pointed at an empty config and an API that refuses."""
    env = {
        **options.env,
        "CLAUDE_CONFIG_DIR": str(home),
        "HOME": str(home),
        "ANTHROPIC_BASE_URL": _DEAD_API,
        "ANTHROPIC_API_KEY": "",
    }
    return dataclasses.replace(options, env=env)


async def _init_message(options: ClaudeAgentOptions) -> dict[str, Any]:
    client = ClaudeSDKClient(options)
    async with asyncio.timeout(90):
        await client.connect()
        try:
            await client.query("Reply with exactly: OK")
            async for message in client.receive_messages():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    return dict(message.data)
        finally:
            await client.disconnect()
    raise AssertionError("the CLI ended without an init message")


async def _noop_dispatch(name: str, arguments: dict[str, Any]) -> Any:
    raise AssertionError(f"no tool may run in this test (got {name})")


async def test_the_ask_tool_loop_offers_exactly_librarys_tools(tmp_path: Path) -> None:
    # Built exactly as tool_loop builds it, with Ask's real tool list.
    server = subscription._build_mcp_server(TOOLS, _noop_dispatch, [])
    options = subscription.build_options(
        model="claude-haiku-4-5",
        system_prompt="s",
        config_dir=tmp_path,
        max_turns=1,
        mcp_servers={subscription._MCP_SERVER: server},
    )

    init = await _init_message(_isolated(options, tmp_path))

    expected = {f"mcp__library__{tool['name']}" for tool in TOOLS}
    assert set(init["tools"]) == expected
    assert [server["name"] for server in init["mcp_servers"]] == ["library"]


async def test_a_text_call_offers_no_tools_at_all(tmp_path: Path) -> None:
    options = subscription.build_options(
        model="claude-haiku-4-5", system_prompt="s", config_dir=tmp_path, max_turns=1
    )

    init = await _init_message(_isolated(options, tmp_path))

    assert init["tools"] == []
    assert init["mcp_servers"] == []
