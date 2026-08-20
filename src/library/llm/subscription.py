"""Claude Agent SDK adapter — the subscription-backed LLM backend.

Subscription access is not a different credential on the Messages API; it comes
from *being the Claude Code CLI*. ``claude-agent-sdk`` bundles that CLI and
speaks to it over a subprocess, and the CLI reads the OAuth credentials a
Claude subscription writes. This module wraps that in the two shapes library
needs:

* :func:`text_call` — one prompt in, one string out. Used by series-insight
  descriptions and ask thread titles.
* :func:`tool_loop` — an agentic loop over library's own tools, bridged into
  the SDK as an in-process MCP server. Used by ``ask``.

**The harness is not free.** Every call carries the Claude Code system prompt —
measured at ~32k tokens for Sonnet and ~43k for Opus on an empty prompt, and it
cannot be configured away (a custom ``system_prompt`` replaces nothing and costs
five tokens more). That fixed per-call tax is why only large, infrequent calls
belong here; see ``docs/llm-backends.md``.

Usage reported back to callers deliberately includes cache-creation and
cache-read tokens in ``input_tokens``, so the cost estimate library derives from
it reflects the true context size — harness tax included — rather than
flattering the subscription path.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    SdkMcpTool,
    create_sdk_mcp_server,
    query,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    Message,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from library.llm import oauth

logger = logging.getLogger(__name__)

# Every Claude Code built-in. Library supplies all of its own tools; the CLI
# must not be able to touch the filesystem, run commands, or reach the network
# on its own. Blocking ToolSearch matters for a second reason: with it
# available the model burns turns discovering tools it has already been given.
_BLOCKED_BUILTINS = [
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookRead",
    "NotebookEdit",
    "Bash",
    "BashOutput",
    "KillShell",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "TodoRead",
    "TodoWrite",
    "Task",
    "Agent",
    "computer_use",
    "ToolSearch",
]

# The SDK namespaces MCP tools as ``mcp__<server>__<tool>``. Library's tools go
# in one server so a single wildcard authorises them all.
_MCP_SERVER = "library"
_MCP_PREFIX = f"mcp__{_MCP_SERVER}__"

# The CLI's inactivity timer is not reset by MCP tool responses
# (anthropics/claude-agent-sdk-typescript#114), so a long tool dance can have
# stdin closed underneath it. An hour is far past any ask turn.
_STREAM_CLOSE_TIMEOUT_MS = "3600000"

# ``ResultMessage.subtype`` when the agent ran out of turns mid-dance.
_MAX_TURNS_SUBTYPE = "error_max_turns"


class SubscriptionBackendError(RuntimeError):
    """The subscription backend could not complete a call."""


@dataclass
class Usage:
    """Token usage for one subscription call.

    ``input_tokens`` is the *total* context billed against the subscription —
    fresh input plus cache creation plus cache reads. Callers feed it to
    ``estimate_cost_usd``, so under-reporting here would understate what the
    harness costs and make the backend look cheaper than it is.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, raw: dict[str, Any] | None) -> None:
        if not raw:
            return
        self.input_tokens += (
            int(raw.get("input_tokens", 0) or 0)
            + int(raw.get("cache_creation_input_tokens", 0) or 0)
            + int(raw.get("cache_read_input_tokens", 0) or 0)
        )
        self.output_tokens += int(raw.get("output_tokens", 0) or 0)


@dataclass
class TextResult:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class ToolLoopResult:
    """Outcome of an agentic turn, in library's own vocabulary.

    ``blocks`` is the turn rendered as Anthropic message blocks — the same shape
    the Messages API backend produces — so a thread stays rehydratable no matter
    which backend answered any given turn.
    """

    answer: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    hit_turn_limit: bool = False


def build_options(
    *,
    model: str,
    system_prompt: str,
    config_dir: Path,
    max_turns: int,
    mcp_servers: dict[str, Any] | None = None,
) -> ClaudeAgentOptions:
    """Build ``ClaudeAgentOptions`` for one call.

    Rebuilt per call because the system prompt carries today's date. Everything
    here is load-bearing — in particular the ``env`` override, which was
    silently dropped on an options rebuild in sre-agent and cost a production
    outage. It is therefore set in exactly one place: here.
    """
    env = {
        # The CLI ranks ANTHROPIC_API_KEY (sent as X-Api-Key) *above* the OAuth
        # credentials file. Library sets that variable for the API backend, and
        # leaving it visible here makes the CLI send an API-key header carrying
        # an OAuth token — which fails as "Invalid API key" rather than falling
        # through to the credentials that would work.
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT": _STREAM_CLOSE_TIMEOUT_MS,
    }
    # Point the CLI at the mounted credentials — but only when they are actually
    # there. CLAUDE_CONFIG_DIR is not a hint, it is an override: setting it makes
    # the CLI look *only* in that directory, so naming a directory with no
    # credentials file turns a working setup into "Not logged in · Please run
    # /login". That is exactly what happens on a macOS dev box, where the CLI
    # keeps credentials in the Keychain and there is no file to point at.
    # Deployment mounts them at a non-default path, so when the file does exist
    # the variable is required.
    if oauth.credentials_path(config_dir).exists():
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers or {},
        allowed_tools=[f"{_MCP_PREFIX}*"] if mcp_servers else [],
        disallowed_tools=_BLOCKED_BUILTINS,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        # Don't inherit the host's CLAUDE.md, settings or slash commands: the
        # prompt library sends must be the prompt library wrote.
        setting_sources=[],
        env=env,
    )


def _explain(failure: str, config_dir: Path) -> str:
    """Turn an SDK failure into something an operator can act on.

    The SDK's own message is often the right diagnosis wrapped in the wrong
    instruction: a container with no credentials reports "Not logged in ·
    Please run /login", but `/login` is an interactive slash command inside the
    CLI, which is not how this deployment authenticates. Appending the
    credential status and the actual command turns a dead end into a fix.
    """
    status, detail = oauth.token_health(config_dir)
    if status == "healthy":
        # Credentials are fine, so this is something else — a CLI crash, a
        # network failure, a rate limit. Don't send anyone to re-authenticate
        # for a problem that is not about authentication.
        return f"the Claude subscription backend failed: {failure}"
    return (
        f"the Claude subscription backend could not authenticate: {detail}. "
        f"Run `CLAUDE_CONFIG_DIR={config_dir} claude auth login --claudeai` on "
        f"the host, then `chown 999:999 {oauth.credentials_path(config_dir)}` "
        f"(see docs/llm-backends.md §4). Original error: {failure}"
    )


async def _run(
    prompt: str | AsyncIterator[dict[str, Any]],
    options: ClaudeAgentOptions,
    config_dir: Path,
) -> tuple[list[Message], Usage]:
    """Drive one ``query()`` to completion, returning its messages and usage.

    Two kinds of failure arrive by different routes, and both are translated to
    :class:`SubscriptionBackendError` so callers have one thing to catch:

    * The SDK raises :class:`ClaudeSDKError` mid-iteration — which is what a
      missing credential actually does, before any result message is seen.
    * A result message arrives flagged as an error. That one is recorded and
      raised *after* the loop, never from inside it: raising mid-iteration
      abandons the SDK's async generator while it is still running, and the
      unwind then fails with ``RuntimeError: aclose(): asynchronous generator
      is already running``, burying the real cause. The result message is the
      last thing yielded, so deferring costs nothing.
    """
    messages: list[Message] = []
    usage = Usage()
    failure: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            messages.append(message)
            if isinstance(message, ResultMessage):
                usage.add(message.usage)
                # Running out of turns is also reported as an error result, but
                # it is a normal outcome the caller handles — not a failure.
                if message.is_error and message.subtype != _MAX_TURNS_SUBTYPE:
                    failure = message.result or message.subtype
    except ClaudeSDKError as exc:
        raise SubscriptionBackendError(_explain(str(exc), config_dir)) from exc

    if failure is not None:
        raise SubscriptionBackendError(_explain(failure, config_dir))
    return messages, usage


def _text_of(messages: list[Message]) -> str:
    """Concatenate assistant text across the run."""
    return "".join(
        block.text
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextBlock)
    ).strip()


async def text_call(
    *,
    config_dir: Path,
    model: str,
    system_prompt: str,
    prompt: str,
) -> TextResult:
    """One prompt in, one answer out — no tools.

    ``max_turns=1`` because with every tool blocked there is nothing a second
    turn could do.
    """
    await oauth.ensure_valid_token(config_dir)
    options = build_options(
        model=model, system_prompt=system_prompt, config_dir=config_dir, max_turns=1
    )
    messages, usage = await _run(prompt, options, config_dir)
    return TextResult(text=_text_of(messages), usage=usage)


# --------------------------------------------------------------------------
# Tool loop
# --------------------------------------------------------------------------

# A dispatcher takes (tool_name, arguments) and returns the tool's JSON-able
# output. Library already has exactly one of these; the bridge below wraps it
# rather than reimplementing any tool.
Dispatcher = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _build_mcp_server(tools: list[dict[str, Any]], dispatch: Dispatcher, used: list[str]) -> Any:
    """Expose library's tool list to the SDK as one in-process MCP server.

    ``tools`` are library's Anthropic-format tool definitions (``name``,
    ``description``, ``input_schema``); each becomes an MCP tool whose handler
    defers to ``dispatch``. No tool *implementation* is duplicated here — this
    is a calling-convention adapter and nothing else.
    """

    def make_handler(name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            used.append(name)
            try:
                output = await dispatch(name, dict(args))
            except Exception as exc:
                # Surface the failure to the model as a tool error so it can
                # recover or explain, rather than killing the whole turn.
                logger.exception("Subscription tool %s failed", name)
                return {
                    "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                    "isError": True,
                }
            return {"content": [{"type": "text", "text": json.dumps(output, default=str)}]}

        return handler

    sdk_tools = [
        SdkMcpTool(
            name=spec["name"],
            description=spec.get("description", ""),
            input_schema=spec.get("input_schema", {}),
            handler=make_handler(spec["name"]),
        )
        for spec in tools
    ]
    return create_sdk_mcp_server(name=_MCP_SERVER, version="1.0.0", tools=sdk_tools)


def _strip_prefix(name: str) -> str:
    """Undo the SDK's ``mcp__library__`` namespacing for library-facing names."""
    return name.removeprefix(_MCP_PREFIX)


def render_history(history_blocks: list[dict[str, Any]]) -> str:
    """Render prior turns as a text preamble for the SDK path.

    ``query()`` is stateless and its streaming input accepts user turns only, so
    conversation history is stuffed into the prompt rather than replayed as real
    turns. This is the approach sre-agent settled on. It is a genuine difference
    from the API backend, where history is replayed as message turns — recorded
    here and in ``docs/llm-backends.md`` rather than papered over.
    """
    if not history_blocks:
        return ""

    lines: list[str] = ["<conversation_history>"]
    for message in history_blocks:
        role = message.get("role", "user")
        content = message.get("content")
        if isinstance(content, str):
            lines.append(f"<{role}>{content}</{role}>")
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                lines.append(f"<{role}>{block.get('text', '')}</{role}>")
            elif kind == "tool_use":
                lines.append(
                    f'<tool_call name="{block.get("name")}">'
                    f"{json.dumps(block.get('input', {}), default=str)}</tool_call>"
                )
            elif kind == "tool_result":
                lines.append(f"<tool_result>{block.get('content', '')}</tool_result>")
            # Images from earlier turns are deliberately dropped: re-sending
            # every historical attachment would grow the prompt without bound.
    lines.append("</conversation_history>")
    return "\n".join(lines)


def _blocks_from_messages(messages: list[Message], answer: str) -> list[dict[str, Any]]:
    """Rebuild the turn as Anthropic message blocks.

    Threads are persisted in this shape and rehydrated by the API backend's
    history parsing — which reads ``tool_use`` blocks to decide what a later
    turn is allowed to edit — so the SDK path must speak the same vocabulary
    rather than invent a format of its own.
    """
    blocks: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            content: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    content.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": _strip_prefix(block.name),
                            "input": dict(block.input),
                        }
                    )
            if content:
                blocks.append({"role": "assistant", "content": content})
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content
                    if isinstance(block.content, str)
                    else json.dumps(block.content, default=str),
                }
                for block in message.content
                if isinstance(block, ToolResultBlock)
            ]
            if results:
                blocks.append({"role": "user", "content": results})

    # The stored turn must end on an assistant message: a thread ending on a
    # tool_result would put two consecutive user turns in front of the next
    # question, which the Messages API rejects with a 400 if the backend is
    # ever switched back.
    if not blocks or blocks[-1].get("role") != "assistant":
        blocks.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
    return blocks


async def tool_loop(
    *,
    config_dir: Path,
    model: str,
    system_prompt: str,
    question: str,
    tools: list[dict[str, Any]],
    dispatch: Dispatcher,
    max_turns: int,
    history_blocks: list[dict[str, Any]] | None = None,
    images: list[dict[str, str]] | None = None,
) -> ToolLoopResult:
    """Answer ``question`` by letting Claude drive library's own tools.

    ``tools`` and ``dispatch`` come straight from the caller — this function
    owns the transport, never the tool semantics.
    """
    await oauth.ensure_valid_token(config_dir)

    used: list[str] = []
    server = _build_mcp_server(tools, dispatch, used)
    options = build_options(
        model=model,
        system_prompt=system_prompt,
        config_dir=config_dir,
        max_turns=max_turns,
        mcp_servers={_MCP_SERVER: server},
    )

    preamble = render_history(history_blocks or [])
    text = f"{preamble}\n\n{question}" if preamble else question
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in images or []:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["media_type"],
                    "data": image["data"],
                },
            }
        )

    async def stream() -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": "library-ask",
        }

    messages, usage = await _run(stream(), options, config_dir)
    answer = _text_of(messages)
    hit_limit = any(
        isinstance(message, ResultMessage) and message.subtype == _MAX_TURNS_SUBTYPE
        for message in messages
    )

    return ToolLoopResult(
        answer=answer,
        blocks=_blocks_from_messages(messages, answer),
        # De-duplicate preserving first-use order, matching the API backend.
        used_tools=list(dict.fromkeys(used)),
        usage=usage,
        hit_turn_limit=hit_limit,
    )
