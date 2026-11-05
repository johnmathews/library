"""ASGI entrypoint: `uvicorn library.main:app`."""

import logging

from fastapi import FastAPI

from library.app import create_app
from library.mcp_server import call_logger

# uvicorn configures only its own loggers, so without a root handler every
# `library` record below WARNING is dropped (Python's last-resort handler
# prints WARNING and up, unformatted). The root handler gives those warnings
# the worker's format; the MCP call log is the one INFO stream turned on
# (docs/observability.md). basicConfig is a no-op if a handler already exists.
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
call_logger.setLevel(logging.INFO)

app: FastAPI = create_app()
