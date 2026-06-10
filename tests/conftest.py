"""Shared test fixtures for the Library backend."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from library.app import create_app


@pytest.fixture
def app() -> FastAPI:
    """A fresh application instance for each test."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """An HTTP test client bound to the app fixture."""
    with TestClient(app) as test_client:
        yield test_client
