"""Log why the ``/mcp`` mount refused a request.

Clients that speak a newer MCP revision than the installed SDK first send a
request the SDK answers with a 400 (``Unsupported protocol version``), then
fall back to ``initialize`` (docs/mcp.md 1.2). Without a log line those 400s
look like unexplained failures. :class:`McpRejectedLog` writes one line per
refused request::

    mcp_rejected status=400 method=tools/list protocol_version=2026-07-28 reason="..."

It is a plain ASGI wrapper, not ``BaseHTTPMiddleware``, which buffers the
response and breaks streaming. Request chunks are copied as they pass to the
app and the app still receives them unchanged; only the JSON-RPC ``method``
is read from them, never ``params``. The response is captured only for a 4xx
other than 401 and only up to a few KB, to read the error message the SDK
gave. 401 is left out: it is the normal OAuth challenge, not a mystery.
"""

import json
import logging
import re
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: Request bytes kept to find the JSON-RPC method; a larger body logs "-".
_REQUEST_CAP = 64 * 1024
#: Response bytes kept to find the error message.
_RESPONSE_CAP = 4 * 1024
#: Longest ``reason`` written to the log.
_REASON_MAX_CHARS = 200
#: Longest ``method`` / ``protocol_version`` written to the log.
_FIELD_MAX_CHARS = 64
#: Anything but printable, non-space ASCII in a bare token: ESC, NUL and the
#: like are not whitespace to ``str.split`` and a terminal would act on them.
_NOT_PRINTABLE = re.compile(r"[^\x21-\x7e]")


def _clean(text: str, limit: int) -> str:
    """One line, whitespace collapsed, cut to ``limit``; ``-`` when empty.

    Values come from the client, so nothing in them may break the log line
    in two.
    """
    flat = " ".join(text.split())
    return flat[:limit] or "-"


def _token(text: str, limit: int) -> str:
    """Like :func:`_clean`, but with no spaces left: whitespace becomes ``_``.

    ``method`` and ``protocol_version`` are bare ``key=value`` fields, so a
    space in a client-sent value would let it pose as an extra field.
    """
    return _NOT_PRINTABLE.sub("_", "_".join(text.split()))[:limit] or "-"


def _jsonrpc_method(body: bytes) -> str:
    """The ``method`` of a single JSON-RPC request object, else ``-``."""
    try:
        data: Any = json.loads(body)
    except (ValueError, RecursionError):  # deep nesting raises RecursionError
        return "-"
    if isinstance(data, dict) and isinstance(data.get("method"), str):
        return _token(data["method"], _FIELD_MAX_CHARS)
    return "-"


def _reason(body: bytes) -> str:
    """The error message in a refused response: JSON-RPC ``error.message``,
    an OAuth ``error_description``/``error``, else the start of the text."""
    text = body.decode("utf-8", errors="replace")
    try:
        data: Any = json.loads(text)
    except (ValueError, RecursionError):
        return _clean(text, _REASON_MAX_CHARS)
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return _clean(error["message"], _REASON_MAX_CHARS)
        if isinstance(data.get("error_description"), str):
            return _clean(data["error_description"], _REASON_MAX_CHARS)
        if isinstance(error, str):
            return _clean(error, _REASON_MAX_CHARS)
    return _clean(text, _REASON_MAX_CHARS)


class McpRejectedLog:
    """ASGI wrapper: one ``mcp_rejected`` log line per 4xx (not 401) response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_chunks: list[bytes] = []
        request_size = 0
        status = 0
        response_chunks: list[bytes] = []
        response_size = 0

        async def watched_receive() -> Message:
            nonlocal request_size
            message = await receive()
            if message["type"] == "http.request" and request_size <= _REQUEST_CAP:
                chunk: bytes = message.get("body", b"")
                request_size += len(chunk)
                request_chunks.append(chunk)
            return message

        async def watched_send(message: Message) -> None:
            nonlocal status, response_size
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and self._wanted(status):
                chunk: bytes = message.get("body", b"")
                if response_size < _RESPONSE_CAP:
                    response_chunks.append(chunk[: _RESPONSE_CAP - response_size])
                    response_size += len(chunk)
            await send(message)

        try:
            await self.app(scope, watched_receive, watched_send)
        finally:
            if self._wanted(status):
                # Runs after the response went out: a fault here must not
                # reach the request path, drop the connection or mask an
                # exception the app raised.
                try:
                    self._log(scope, status, request_chunks, request_size, response_chunks)
                except Exception:
                    logger.debug("could not write the rejected-request log line", exc_info=True)

    @staticmethod
    def _wanted(status: int) -> bool:
        return 400 <= status < 500 and status != 401

    @staticmethod
    def _log(
        scope: Scope,
        status: int,
        request_chunks: list[bytes],
        request_size: int,
        response_chunks: list[bytes],
    ) -> None:
        method = "-" if request_size > _REQUEST_CAP else _jsonrpc_method(b"".join(request_chunks))
        version = "-"
        for name, value in scope.get("headers", []):
            if name == b"mcp-protocol-version":
                version = _token(value.decode("latin-1"), _FIELD_MAX_CHARS)
                break
        # reason is free text, so it is JSON-quoted: its spaces and any
        # "key=value" inside it stay one field.
        logger.info(
            "mcp_rejected status=%d method=%s protocol_version=%s reason=%s",
            status,
            method,
            version,
            json.dumps(_reason(b"".join(response_chunks))),
        )
