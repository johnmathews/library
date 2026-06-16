"""Unit tests for the embedding sidecar client (mocked HTTP transport)."""

import json

import httpx
import pytest

from library.config import Settings
from library.embedding import EmbeddingError, embed_query, embed_texts
from library.models import EMBEDDING_DIM


def _settings(**overrides: object) -> Settings:
    return Settings(embedding_service_url="http://embedder:80", **overrides)


def _vector(seed: float = 0.0) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = seed
    return vector


def _client(handler: httpx.MockTransport | object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_embed_texts_returns_one_vector_per_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        return httpx.Response(200, json=[_vector(float(i)) for i in range(len(inputs))])

    vectors = await embed_texts(["a", "b"], settings=_settings(), client=_client(handler))
    assert len(vectors) == 2
    assert all(len(vector) == EMBEDDING_DIM for vector in vectors)
    assert vectors[1][0] == 1.0


async def test_embed_query_returns_single_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        assert json.loads(request.content)["normalize"] is True
        return httpx.Response(200, json=[_vector(0.5)])

    vector = await embed_query("hello", settings=_settings(), client=_client(handler))
    assert len(vector) == EMBEDDING_DIM
    assert vector[0] == 0.5


async def test_embed_texts_empty_input_skips_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no HTTP call expected for empty input")

    assert await embed_texts([], settings=_settings(), client=_client(handler)) == []


async def test_embed_texts_batches_by_configured_size() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        calls.append(len(inputs))
        return httpx.Response(200, json=[_vector() for _ in inputs])

    vectors = await embed_texts(
        ["a", "b", "c"], settings=_settings(embedding_batch_size=2), client=_client(handler)
    )
    assert len(vectors) == 3
    assert calls == [2, 1]


async def test_embed_texts_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model loading")

    with pytest.raises(EmbeddingError):
        await embed_texts(["a"], settings=_settings(), client=_client(handler))


async def test_embed_texts_raises_on_dimension_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[0.1, 0.2, 0.3]])

    with pytest.raises(EmbeddingError):
        await embed_texts(["a"], settings=_settings(), client=_client(handler))
