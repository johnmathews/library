"""The compose-smoke MCP script's own logic (scripts/mcp_smoke.py).

The script's real test is CI: it runs against the built image. These pin
the two parts that would otherwise fail quietly or confusingly there: how a
response body is read (JSON or SSE framing), and that any failure ends in
a non-zero exit with one readable line.
"""

import socket

import pytest

from scripts.mcp_smoke import SmokeError, main, parse_body


def test_parse_body_reads_plain_json() -> None:
    assert parse_body('{"jsonrpc": "2.0", "id": 1, "result": {}}', "application/json") == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }


def test_parse_body_reads_sse_framing() -> None:
    """The transport may answer as an event stream; the message is the last
    ``data:`` line."""
    body = 'event: message\ndata: {"jsonrpc": "2.0", "id": 2, "result": {"isError": false}}\n\n'
    assert parse_body(body, "text/event-stream; charset=utf-8")["result"] == {"isError": False}


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("<html>Bad Gateway</html>", "text/html"),
        ("event: message\n\n", "text/event-stream"),
        ("[1, 2]", "application/json"),
    ],
)
def test_parse_body_refuses_what_is_not_a_json_rpc_message(body: str, content_type: str) -> None:
    with pytest.raises(SmokeError):
        parse_body(body, content_type)


def test_unreachable_server_fails_with_one_readable_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Nothing listens on the port once the probe socket is closed.
    status = main(
        [
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--host",
            "library.example.com",
            "--username",
            "u",
            "--password",
            "p",
        ]
    )
    assert status == 1
    err = capsys.readouterr().err
    assert err.startswith("mcp-smoke: FAIL POST /mcp/: cannot connect")
    assert err.count("\n") == 1
