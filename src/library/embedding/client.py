"""Async client for the local embedding sidecar (text-embeddings-inference).

The sidecar serves bge-m3 and exposes ``POST /embed`` taking
``{"inputs": [...], "normalize": true}`` and returning a list of float
vectors. We request normalized vectors so cosine distance in pgvector is a
plain dot product. Long inputs are auto-truncated server-side
(``--auto-truncate``); we still batch to keep request bodies bounded.
"""

from __future__ import annotations

import httpx

from library.config import Settings
from library.models import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    """The embedding sidecar was unreachable or returned an unusable response."""


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def embed_texts(
    texts: list[str],
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    """Embed ``texts`` into 1024-dim unit vectors, preserving order.

    Batches by ``settings.embedding_batch_size``. Raises ``EmbeddingError`` on
    transport failure or a dimension mismatch. An empty input returns ``[]``.
    """
    if not texts:
        return []

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.embedding_timeout_s)
    url = settings.embedding_service_url.rstrip("/") + "/embed"
    vectors: list[list[float]] = []
    try:
        for batch in _batches(texts, max(1, settings.embedding_batch_size)):
            response = await client.post(url, json={"inputs": batch, "normalize": True})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) != len(batch):
                raise EmbeddingError(
                    f"embedder returned {len(payload) if isinstance(payload, list) else '?'} "
                    f"vectors for {len(batch)} inputs"
                )
            for vector in payload:
                if len(vector) != EMBEDDING_DIM:
                    raise EmbeddingError(
                        f"embedder returned dim {len(vector)}, expected {EMBEDDING_DIM}"
                    )
                vectors.append([float(value) for value in vector])
    except httpx.HTTPError as error:
        raise EmbeddingError(f"embedding request failed: {error}") from error
    finally:
        if owns_client:
            await client.aclose()
    return vectors


async def embed_query(
    text: str,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> list[float]:
    """Embed a single query string into one 1024-dim unit vector."""
    vectors = await embed_texts([text], settings=settings, client=client)
    return vectors[0]
