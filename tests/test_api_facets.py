"""The facet REST surface, exercised through the app."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Facet, FacetValueSuggestion

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


def _run[T](api_database_url: str, op: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run an async DB operation against the API test DB in the main thread.

    Nothing in the HTTP surface creates a ``FacetValueSuggestion`` row (that is
    the labeller's job, exercised elsewhere) so the suggestion tests below seed
    one directly. Mirrors ``tests/test_admin_api.py``'s ``_run_service``: a
    short-lived NullPool engine against the same database ``api_client`` reads
    from, run in the main thread rather than TestClient's event-loop thread.
    """

    async def _body() -> T:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await op(session)
        finally:
            await engine.dispose()

    return asyncio.run(_body())


def _seed_suggestion(
    api_database_url: str, facet_key: str, document_id: int, suggested_label: str
) -> int:
    """Insert a pending suggestion directly against ``facet_key``; returns its id."""

    async def _op(session: AsyncSession) -> int:
        facet_id = (
            await session.execute(select(Facet.id).where(Facet.key == facet_key))
        ).scalar_one()
        suggestion = FacetValueSuggestion(
            facet_id=facet_id,
            document_id=document_id,
            suggested_label=suggested_label,
            reason="the labeller thought this document belonged in this facet",
        )
        session.add(suggestion)
        await session.flush()
        await session.commit()
        return suggestion.id

    return _run(api_database_url, _op)


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


def test_a_pending_suggestion_is_listed(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    body = api_client.get("/api/facet-suggestions").json()
    row = next(s for s in body["suggestions"] if s["id"] == suggestion_id)
    assert row["facet"] == key
    assert row["suggested_label"] == "Gamma Label"
    assert row["document_id"] == seeded_document_id


def test_accepting_a_suggestion_creates_the_value_and_labels_the_document(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    """The one sanctioned path that widens the closed vocabulary."""
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 200, response.text
    assert response.json() == {"facet": key, "value": "gamma-label"}

    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert "gamma-label" in {v["key"] for v in facet["values"]}

    labels = api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"]
    assert labels[key] == "gamma-label"


def test_accepting_a_suggestion_whose_derived_key_already_exists_is_409(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(
        f"/api/facets/{key}/values", json={"key": "gamma-label", "label": "Gamma Label"}
    )
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 409

    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    values = [v for v in facet["values"] if v["key"] == "gamma-label"]
    assert len(values) == 1
    assert values[0]["label"] == "Gamma Label"


def test_dismissing_a_suggestion_removes_it_from_the_pending_list(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/dismiss")
    assert response.status_code == 200, response.text
    ids = {s["id"] for s in api_client.get("/api/facet-suggestions").json()["suggestions"]}
    assert suggestion_id not in ids


def test_creating_a_duplicate_facet_key_is_409(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    response = api_client.post("/api/facets", json={"key": key, "label": "Api"})
    assert response.status_code == 409, response.text


def test_creating_a_duplicate_value_key_is_409(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    response = api_client.post(
        f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha Duplicate"}
    )
    assert response.status_code == 409, response.text
