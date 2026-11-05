#!/usr/bin/env python3
"""MCP smoke test against a running Library: the compose-smoke CI step.

Every in-repo MCP test runs in-process with ``Host: testserver``. This one
talks HTTP to the built image with the headers production sees behind the
tunnel and reverse proxy (a public ``Host``, ``X-Forwarded-Proto: https``),
which is where a FastMCP Host/Origin regression shows (fastmcp 3.4.3 once
broke exactly that; see pyproject.toml). It:

1. logs in with a password and creates an API token (the documented path,
   docs/mcp.md §1.1; there is no token CLI);
2. checks that ``POST /mcp/`` without a token is ``401``, and, when asked,
   that the 401 names the OAuth protected-resource metadata;
3. runs ``initialize``, ``notifications/initialized`` and
   ``tools/call library_stats``, and requires ``isError`` to be false.

Stdlib only, so it runs on a bare CI runner. Any failure exits non-zero with
a one-line ``mcp-smoke: FAIL …`` message naming the step.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CSRF_COOKIE = "library_csrftoken"


class SmokeError(Exception):
    """A failed check; the message says which and what came back."""


def parse_body(body: str, content_type: str) -> dict[str, Any]:
    """The JSON-RPC message in a Streamable-HTTP response.

    The server is configured for plain JSON responses, but the transport
    allows SSE framing (``data: {...}`` lines), so both are accepted rather
    than failing on a framing change that clients would handle.
    """
    if content_type.startswith("text/event-stream"):
        data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        if not data:
            raise SmokeError(f"SSE response with no data line: {body[:300]!r}")
        body = data[-1]
    try:
        message = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"response is not JSON ({exc}): {body[:300]!r}") from exc
    if not isinstance(message, dict):
        raise SmokeError(f"response is not a JSON-RPC object: {body[:300]!r}")
    return message


class Client:
    def __init__(self, base_url: str, host: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.host = host
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.session_id: str | None = None

    def request(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], str]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read().decode(errors="replace")
        except urllib.error.URLError as error:
            raise SmokeError(f"{method} {path}: cannot connect: {error.reason}") from error

    def create_token(self, username: str, password: str) -> str:
        status, _, body = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )
        if status != 200:
            raise SmokeError(f"login: expected 200, got {status}: {body[:300]}")
        csrf = next((c.value for c in self.cookies if c.name == CSRF_COOKIE), None)
        if not csrf:
            raise SmokeError(f"login: no {CSRF_COOKIE} cookie was set")
        status, _, body = self.request(
            "POST", "/api/auth/tokens", {"name": "mcp-smoke"}, {"X-CSRF-Token": csrf}
        )
        if status != 201:
            raise SmokeError(f"create token: expected 201, got {status}: {body[:300]}")
        token = parse_body(body, "application/json").get("token")
        if not isinstance(token, str) or not token:
            raise SmokeError(f"create token: no token in {body[:300]}")
        return token

    def mcp(
        self, token: str | None, message: dict[str, Any], mcp_path: str
    ) -> tuple[int, dict[str, str], str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": self.host,
            "X-Forwarded-Proto": "https",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return self.request("POST", mcp_path, message, headers)


def _header(headers: dict[str, str], name: str) -> str | None:
    return next((v for k, v in headers.items() if k.lower() == name.lower()), None)


def smoke(args: argparse.Namespace) -> None:
    client = Client(args.base_url, args.host)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-smoke", "version": "0"},
        },
    }

    status, headers, body = client.mcp(None, initialize, args.mcp_path)
    if status != 401:
        raise SmokeError(f"no token: expected 401, got {status}: {body[:300]}")
    challenge = _header(headers, "WWW-Authenticate") or ""
    if args.resource_metadata and f'resource_metadata="{args.resource_metadata}"' not in challenge:
        raise SmokeError(
            f"no token: WWW-Authenticate {challenge!r} does not name {args.resource_metadata}"
        )
    print(f"mcp-smoke: ok  no token -> 401 ({challenge})")

    token = client.create_token(args.username, args.password)
    print("mcp-smoke: ok  logged in and created an API token")

    status, headers, body = client.mcp(token, initialize, args.mcp_path)
    if status != 200:
        raise SmokeError(f"initialize: expected 200, got {status}: {body[:300]}")
    reply = parse_body(body, _header(headers, "Content-Type") or "")
    server = reply.get("result", {}).get("serverInfo", {})
    if "error" in reply or not server:
        raise SmokeError(f"initialize: no serverInfo in {body[:300]}")
    client.session_id = _header(headers, "Mcp-Session-Id")
    print(f"mcp-smoke: ok  initialize -> {server.get('name')} {server.get('version')}")

    status, _, body = client.mcp(
        token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, args.mcp_path
    )
    if status not in (200, 202):
        raise SmokeError(f"notifications/initialized: expected 202, got {status}: {body[:300]}")
    print(f"mcp-smoke: ok  notifications/initialized -> {status}")

    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "library_stats", "arguments": {}},
    }
    status, headers, body = client.mcp(token, call, args.mcp_path)
    if status != 200:
        raise SmokeError(f"tools/call library_stats: expected 200, got {status}: {body[:300]}")
    reply = parse_body(body, _header(headers, "Content-Type") or "")
    result = reply.get("result")
    if not isinstance(result, dict) or result.get("isError") is not False:
        raise SmokeError(f"tools/call library_stats: isError is not false: {body[:500]}")
    print("mcp-smoke: ok  tools/call library_stats -> isError false")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--host", required=True, help="the public Host header to send")
    parser.add_argument("--mcp-path", default="/mcp/")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--resource-metadata",
        help="require the no-token 401 to name this protected-resource metadata URL",
    )
    args = parser.parse_args(argv)
    try:
        smoke(args)
    except SmokeError as exc:
        print(f"mcp-smoke: FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
