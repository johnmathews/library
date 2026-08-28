"""The facet REST surface, exercised through the app."""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _make_facet(api_client: TestClient) -> str:
    """Create a fresh, uniquely-keyed facet to work in.

    Deliberately not the shipped vocabulary: it is shared across the whole
    integration suite and this file must not depend on or assert against it.
    """
    key = f"api-{uuid.uuid4().hex[:8]}"
    response = api_client.post("/api/facets", json={"key": key, "label": "Api"})
    assert response.status_code == 201, response.text
    return key


def test_the_vocabulary_lists_facets_and_values(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    assert (
        api_client.post(
            f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"}
        ).status_code
        == 201
    )
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert [v["key"] for v in facet["values"]] == ["alpha"]


def test_setting_and_reading_a_documents_labels(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    put = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}}
    )
    assert put.status_code == 200, put.text
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "alpha"
    )


def test_a_null_clears_a_label(api_client: TestClient, seeded_document_id: int) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: None}})
    assert key not in api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"]


def test_an_unknown_value_is_rejected_with_422_not_created(
    api_client: TestClient, seeded_document_id: int
) -> None:
    """The closed set holds at the API boundary too, not only in the labeller."""
    key = _make_facet(api_client)
    response = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "invented"}}
    )
    assert response.status_code == 422
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert facet["values"] == []


def test_deleting_a_value_in_use_returns_409(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    assert api_client.delete(f"/api/facets/{key}/values/alpha").status_code == 409


def test_merge_moves_labels_and_reports_the_count(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(f"/api/facets/{key}/values/alpha/merge", json={"into": "beta"})
    assert response.status_code == 200
    assert response.json()["moved"] == 1
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "beta"
    )


def test_a_dry_run_merge_reports_the_count_without_moving_anything(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(
        f"/api/facets/{key}/values/alpha/merge", json={"into": "beta", "dry_run": True}
    )
    assert response.json()["moved"] == 1
    # nothing moved: the label and the source value both survive
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "alpha"
    )
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert "alpha" in {v["key"] for v in facet["values"]}


def test_anonymous_access_is_refused(anon_client: TestClient) -> None:
    assert anon_client.get("/api/facets").status_code in (401, 403)
