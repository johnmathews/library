"""Local embedding (bge-m3 via a text-embeddings-inference sidecar)."""

from library.embedding.client import EmbeddingError, embed_query, embed_texts

__all__ = ["EmbeddingError", "embed_query", "embed_texts"]
