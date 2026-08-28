"""Facet filtering on the document list. Scoped by a unique facet per test."""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


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


def test_two_facets_and_compose(api_client: TestClient, seeded_document_id: int) -> None:
    a = f"srcha-{uuid.uuid4().hex[:8]}"
    b = f"srchb-{uuid.uuid4().hex[:8]}"
    for key in (a, b):
        api_client.post("/api/facets", json={"key": key, "label": key})
        api_client.post(f"/api/facets/{key}/values", json={"key": "one", "label": "One"})
    api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {a: "one", b: "one"}}
    )
    both = api_client.get(
        "/api/documents", params=[("facet", f"{a}:one"), ("facet", f"{b}:one")]
    ).json()
    assert [d["id"] for d in both["items"]] == [seeded_document_id]


def test_a_malformed_facet_parameter_is_a_422(api_client: TestClient) -> None:
    assert api_client.get("/api/documents", params={"facet": "no-colon"}).status_code == 422
