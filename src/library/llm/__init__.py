"""Subscription-backed LLM access via the Claude Agent SDK.

Library's default backend is the metered Anthropic Messages API (``anthropic``
SDK, ``x-api-key``). This package adds a *second* backend that reaches Claude
through the bundled Claude Code CLI using the OAuth credentials a Claude
subscription writes — see ``docs/llm-backends.md`` for which surfaces may use
it and why the batch extraction pipeline may not.
"""
