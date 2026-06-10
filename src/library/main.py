"""ASGI entrypoint: `uvicorn library.main:app`."""

from fastapi import FastAPI

from library.app import create_app

app: FastAPI = create_app()
