"""Unit tests for the ``mcp_rejected`` log wrapper, against a stand-in ASGI app.

The end-to-end cases (the real SDK's 400, the 200 and 401 paths) live in
tests/test_mcp.py; these pin the wrapper's own edges: body and response
caps, the reason shapes, header handling and pass-through.
"""

import json
import logging
from collections.abc import Callable

import pytest
from starlette.types import Message, Receive, Scope, Send

from library.mcp_rejected_log import McpRejectedLog

LOGGER = "library.mcp_rejected_log"


def _scope(headers: list[tuple[bytes, bytes]] | None = None, kind: str = "http") -> Scope:
    return {"type": kind, "method": "POST", "path": "/", "headers": headers or []}


def _responder(status: int, body: bytes, *, chunk: int = 0) -> Callable[..., object]:
    """An app that reads the whole request, then answers ``status`` / ``body``."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": status, "headers": []})
        parts = [body[i : i + chunk] for i in range(0, len(body), chunk)] if chunk else [body]
        for index, part in enumerate(parts):
            await send(
                {"type": "http.response.body", "body": part, "more_body": index < len(parts) - 1}
            )

    return app


async def _run(
    app: Callable[..., object],
    request_chunks: list[bytes],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[Message]:
    queue = [
        {"type": "http.request", "body": chunk, "more_body": index < len(request_chunks) - 1}
        for index, chunk in enumerate(request_chunks)
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await McpRejectedLog(app)(_scope(headers), receive, send)  # type: ignore[arg-type]
    return sent


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


async def test_response_and_request_pass_through_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[bytes] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            seen.append(message["body"])
            if not message["more_body"]:
                break
        await send({"type": "http.response.start", "status": 400, "headers": [(b"x", b"y")]})
        await send({"type": "http.response.body", "body": b"abc", "more_body": True})
        await send({"type": "http.response.body", "body": b"def", "more_body": False})

    with caplog.at_level(logging.INFO, logger=LOGGER):
        sent = await _run(app, [b'{"method": ', b'"ping"}'])
    assert seen == [b'{"method": ', b'"ping"}']
    assert [m.get("body") for m in sent[1:]] == [b"abc", b"def"]
    assert sent[0]["headers"] == [(b"x", b"y")]
    assert "method=ping " in _lines(caplog)[0]


async def test_non_http_scope_is_passed_through(caplog: pytest.LogCaptureFixture) -> None:
    called: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(scope["type"])

    async def receive() -> Message:
        return {}

    async def send(message: Message) -> None:
        return None

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await McpRejectedLog(app)({"type": "lifespan"}, receive, send)  # type: ignore[arg-type]
    assert called == ["lifespan"]
    assert _lines(caplog) == []


@pytest.mark.parametrize("status", [200, 202, 301, 401, 500, 503])
async def test_only_4xx_other_than_401_is_logged(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _run(_responder(status, b"{}"), [b"{}"])
    assert _lines(caplog) == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (json.dumps({"error": {"code": -32600, "message": "Bad  thing\nhere"}}), "Bad thing here"),
        (
            json.dumps({"error": "invalid_grant", "error_description": "code expired"}),
            "code expired",
        ),
        (json.dumps({"error": "invalid_client"}), "invalid_client"),
        (json.dumps({"detail": "nope"}), '{"detail": "nope"}'),
        ("plain text refusal", "plain text refusal"),
        ("", "-"),
    ],
)
async def test_reason_shapes(body: str, reason: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _run(_responder(404, body.encode()), [b"{}"])
    (line,) = _lines(caplog)
    assert line.endswith(f"reason={reason}")
    assert line.startswith("mcp_rejected status=404 method=- ")


async def test_reason_is_cut_and_response_capture_is_capped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = json.dumps({"error": {"message": "x" * 10_000}}).encode()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        sent = await _run(_responder(400, body, chunk=1500), [b"{}"])
    (line,) = _lines(caplog)
    # Cut at the 4 KB capture, the JSON no longer parses, so the reason is the
    # start of the raw text, at most 200 characters.
    reason = line.split("reason=", 1)[1]
    assert len(reason) == 200
    assert reason.startswith('{"error": {"message": "xxx')
    # Every byte still reached the client.
    assert b"".join(m.get("body", b"") for m in sent[1:]) == body


@pytest.mark.parametrize(
    "request_chunks",
    [
        [b'["a", "b"]'],
        [b'{"method": 5}'],
        [b"not json"],
        [b""],
        [b'{"method": "tools/list", "pad": "' + b"p" * 70_000 + b'"}'],
        [b"x" * 40_000, b"y" * 40_000],
    ],
)
async def test_method_unreadable_logs_a_dash(
    request_chunks: list[bytes], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _run(_responder(400, b"{}"), request_chunks)
    assert " method=- " in _lines(caplog)[0]


async def test_header_value_is_cleaned_and_params_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = json.dumps({"method": "tools/call\nforged=1", "params": {"secret": "s3cret-value"}})
    headers = [(b"mcp-protocol-version", b"2026-07-28\r\nforged=1 " + b"v" * 100)]
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _run(_responder(400, b"{}"), [body.encode()], headers=headers)
    (line,) = _lines(caplog)
    assert "\n" not in line and "\r" not in line
    assert "s3cret-value" not in line
    assert " protocol_version=2026-07-28 forged=1 " in line
    assert " method=tools/call forged=1 " in line


async def test_logged_even_when_the_app_raises_after_responding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"bad", "more_body": False})
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(RuntimeError):
        await _run(app, [b"{}"])
    assert len(_lines(caplog)) == 1
