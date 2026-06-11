"""Thin async client for the paperless-ngx REST API.

Pinned to API version 9 via the ``Accept`` header (understood by both
paperless-ngx 2.x and 3.0); token auth; cursorless page-following
pagination (`{count, next, previous, results}`); original downloads
verified against the MD5 ``original_checksum`` from the ``metadata/``
endpoint with one retry.
"""

import hashlib
import logging
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)

ACCEPT_HEADER: str = "application/json; version=9"
DEFAULT_PAGE_SIZE: int = 100


class PaperlessError(Exception):
    """A paperless-ngx API interaction failed."""


class ChecksumMismatchError(PaperlessError):
    """A downloaded original did not match paperless's recorded MD5 checksum."""

    def __init__(self, document_id: int, expected: str, actual: str) -> None:
        self.document_id = document_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"checksum mismatch for paperless document {document_id}: "
            f"expected md5 {expected}, got {actual}"
        )


class PaperlessClient:
    """Async httpx wrapper over the paperless-ngx endpoints the importer needs."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Token {token}", "Accept": ACCEPT_HEADER},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response

    async def paginate(
        self, endpoint: str, *, page_size: int = DEFAULT_PAGE_SIZE
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every item of a paginated list endpoint, following ``next`` links."""
        url: str | None = endpoint
        params: dict[str, Any] | None = {"page_size": page_size}
        while url is not None:
            payload = (await self._get(url, params=params)).json()
            for item in payload["results"]:
                yield item
            # The `next` URL is absolute and already carries the query string.
            url = payload.get("next")
            params = None

    async def list_tags(self) -> list[dict[str, Any]]:
        return [item async for item in self.paginate("/api/tags/")]

    async def list_correspondents(self) -> list[dict[str, Any]]:
        return [item async for item in self.paginate("/api/correspondents/")]

    async def list_document_types(self) -> list[dict[str, Any]]:
        return [item async for item in self.paginate("/api/document_types/")]

    async def list_custom_fields(self) -> list[dict[str, Any]]:
        return [item async for item in self.paginate("/api/custom_fields/")]

    async def list_storage_paths(self) -> list[dict[str, Any]]:
        return [item async for item in self.paginate("/api/storage_paths/")]

    def iter_documents(
        self, *, page_size: int = DEFAULT_PAGE_SIZE
    ) -> AsyncIterator[dict[str, Any]]:
        return self.paginate("/api/documents/", page_size=page_size)

    async def document_metadata(self, document_id: int) -> dict[str, Any]:
        """The ``{id}/metadata/`` payload (original_checksum, has_archive_version, ...)."""
        return (await self._get(f"/api/documents/{document_id}/metadata/")).json()

    async def download_original(self, document_id: int) -> bytes:
        """The bit-exact original file (``download/?original=true``)."""
        response = await self._get(
            f"/api/documents/{document_id}/download/", params={"original": "true"}
        )
        return response.content

    async def download_original_verified(self, document_id: int) -> bytes:
        """Download the original and verify its MD5 against ``metadata/``.

        A mismatch is retried once (transient read/proxy corruption); a
        second mismatch raises ChecksumMismatchError so the caller can
        record the failure and continue with the rest of the run.
        """
        metadata = await self.document_metadata(document_id)
        expected = metadata.get("original_checksum")
        last_actual = ""
        for attempt in (1, 2):
            content = await self.download_original(document_id)
            if not expected:
                return content  # nothing to verify against
            last_actual = hashlib.md5(content).hexdigest()
            if last_actual == expected.lower():
                return content
            logger.warning(
                "md5 mismatch for paperless document %s (attempt %s): expected %s, got %s",
                document_id,
                attempt,
                expected,
                last_actual,
            )
        raise ChecksumMismatchError(document_id, expected, last_actual)
