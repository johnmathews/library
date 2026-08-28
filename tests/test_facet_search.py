"""Facet filtering on the document list. Scoped by a unique facet per test."""

import asyncio
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Document, DocumentSource, DocumentStatus

pytestmark = pytest.mark.integration


async def _seed_extra_document(database_url: str) -> int:
    """Seed one more indexed document, distinct from `seeded_document_id`."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            marker = f"facet-search-extra:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
            )
            session.add(doc)
            await session.flush()
            await session.commit()
            return doc.id
    finally:
        await engine.dispose()


def seed_extra_document(database_url: str) -> int:
    return asyncio.run(_seed_extra_document(database_url))


def test_filtering_by_one_facet_narrows_to_labelled_documents(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = f"srch-{uuid.uuid4().hex[:8]}"
    api_client.post("/api/facets", json={"key": key, "label": "Search"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})

    hit = api_client.get("/api/documents", params={"facet": f"{key}:alpha"}).json()
    miss = api_client.get("/api/documents", params={"facet": f"{key}:beta"}).json()
    assert [d["id"] for d in hit["items"]] == [seeded_document_id]
    assert seeded_document_id not in [d["id"] for d in miss["items"]]


def test_two_facets_and_compose(
    api_client: TestClient, seeded_document_id: int, api_database_url: str
) -> None:
    """AND, not OR. The second document carries only facet A, so an OR-composed
    implementation would return both documents and this test would fail — which
    is the regression it exists to catch."""
    a = f"srcha-{uuid.uuid4().hex[:8]}"
    b = f"srchb-{uuid.uuid4().hex[:8]}"
    for key in (a, b):
        api_client.post("/api/facets", json={"key": key, "label": key})
        api_client.post(f"/api/facets/{key}/values", json={"key": "one", "label": "One"})

    # the document under test carries BOTH facets
    api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {a: "one", b: "one"}}
    )
    # a second document carries ONLY facet A
    other_id = seed_extra_document(api_database_url)
    api_client.put(f"/api/documents/{other_id}/labels", json={"labels": {a: "one"}})

    both = api_client.get(
        "/api/documents", params=[("facet", f"{a}:one"), ("facet", f"{b}:one")]
    ).json()
    returned = [d["id"] for d in both["items"]]
    assert returned == [seeded_document_id]
    assert other_id not in returned


def test_a_malformed_facet_parameter_is_a_422(api_client: TestClient) -> None:
    assert api_client.get("/api/documents", params={"facet": "no-colon"}).status_code == 422


def test_the_same_facet_given_two_different_values_is_422(api_client: TestClient) -> None:
    key = f"srchd-{uuid.uuid4().hex[:8]}"
    api_client.post("/api/facets", json={"key": key, "label": key})
    for value in ("one", "two"):
        api_client.post(f"/api/facets/{key}/values", json={"key": value, "label": value})
    response = api_client.get(
        "/api/documents", params=[("facet", f"{key}:one"), ("facet", f"{key}:two")]
    )
    assert response.status_code == 422


def test_the_same_facet_pair_repeated_is_accepted(api_client: TestClient) -> None:
    """An exact repeat is harmless — only a CONFLICTING repeat is an error."""
    key = f"srche-{uuid.uuid4().hex[:8]}"
    api_client.post("/api/facets", json={"key": key, "label": key})
    api_client.post(f"/api/facets/{key}/values", json={"key": "one", "label": "One"})
    response = api_client.get(
        "/api/documents", params=[("facet", f"{key}:one"), ("facet", f"{key}:one")]
    )
    assert response.status_code == 200
