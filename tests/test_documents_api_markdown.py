"""Integration tests for GET /api/documents/{id}/markdown."""

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Document, DocumentPage, DocumentSource, DocumentStatus

pytestmark = pytest.mark.integration


async def _seed_document_with_pages(
    database_url: str,
    marker: str,
    pages: list[tuple[int, str]],
) -> int:
    """Insert a document and optional DocumentPage rows; return the document id."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
            )
            session.add(document)
            await session.flush()
            for page_number, markdown in pages:
                session.add(
                    DocumentPage(
                        document_id=document.id,
                        page_number=page_number,
                        markdown=markdown,
                        char_count=len(markdown),
                    )
                )
            await session.commit()
            return document.id
    finally:
        await engine.dispose()


def seed_document_with_pages(
    database_url: str,
    marker: str,
    pages: list[tuple[int, str]],
) -> int:
    """Sync wrapper around _seed_document_with_pages."""
    return asyncio.run(_seed_document_with_pages(database_url, marker, pages))


def test_markdown_endpoint_returns_pages(api_client: TestClient, api_database_url: str) -> None:
    """200 with ordered pages and correct page_count when pages exist."""
    doc_id = seed_document_with_pages(
        api_database_url,
        "md-api-pages",
        # Seed out-of-order to verify ordering
        [(2, "# Two"), (1, "# One")],
    )
    response = api_client.get(f"/api/documents/{doc_id}/markdown")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page_count"] == 2
    assert body["pages"] == [
        {"page_number": 1, "markdown": "# One"},
        {"page_number": 2, "markdown": "# Two"},
    ]


def test_markdown_endpoint_empty_when_no_pages(
    api_client: TestClient, api_database_url: str
) -> None:
    """200 with empty pages list when the document has no DocumentPage rows."""
    doc_id = seed_document_with_pages(api_database_url, "md-api-empty", [])
    response = api_client.get(f"/api/documents/{doc_id}/markdown")
    assert response.status_code == 200, response.text
    assert response.json() == {"page_count": 0, "pages": []}


def test_markdown_endpoint_nonexistent_document_404(api_client: TestClient) -> None:
    """404 for a document id that does not exist."""
    response = api_client.get("/api/documents/999999999/markdown")
    assert response.status_code == 404


def test_markdown_endpoint_deleted_document_404(
    api_client: TestClient, api_database_url: str
) -> None:
    """404 for a soft-deleted document."""
    doc_id = seed_document_with_pages(
        api_database_url,
        "md-api-deleted",
        [(1, "# Page 1")],
    )
    # Soft-delete the document via the API
    delete_response = api_client.delete(f"/api/documents/{doc_id}")
    assert delete_response.status_code == 204
    response = api_client.get(f"/api/documents/{doc_id}/markdown")
    assert response.status_code == 404


def test_markdown_endpoint_deleted_with_include_deleted_returns_pages(
    api_client: TestClient, api_database_url: str
) -> None:
    """The read-only detail view of a trashed document must still render its text,
    so markdown honours ?include_deleted=true (404 without it, as above)."""
    doc_id = seed_document_with_pages(
        api_database_url,
        "md-api-deleted-included",
        [(1, "# Page 1")],
    )
    assert api_client.delete(f"/api/documents/{doc_id}").status_code == 204
    response = api_client.get(f"/api/documents/{doc_id}/markdown", params={"include_deleted": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page_count"] == 1
    assert body["pages"] == [{"page_number": 1, "markdown": "# Page 1"}]
