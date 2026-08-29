"""The payment endpoints, exercised through the app."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_a_documents_payment_group_lists_its_partners(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    body = api_client.get(f"/api/documents/{a}/payment").json()
    assert sorted(d["id"] for d in body["documents"]) == sorted([a, b])


def test_split_then_merge_round_trips(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    split = api_client.post("/api/payments/split", json={"doc_a": a, "doc_b": b})
    assert split.status_code == 200
    assert [d["id"] for d in split.json()["documents"]] == [a]

    merge = api_client.post("/api/payments/merge", json={"doc_a": a, "doc_b": b})
    assert merge.status_code == 200
    assert sorted(d["id"] for d in merge.json()["documents"]) == sorted([a, b])


def test_merge_then_split_round_trips(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    """The other direction of the round trip above, and the one the UI needs.

    "Not the same payment" is the branch's only correction surface. A `SPLIT`
    recorded *after* a `MERGE` has to win, or the button answers 200 and the
    panel re-renders with the pair still merged — a silent no-op.
    """
    a, b = payment_pair
    merge = api_client.post("/api/payments/merge", json={"doc_a": a, "doc_b": b})
    assert merge.status_code == 200
    assert sorted(d["id"] for d in merge.json()["documents"]) == sorted([a, b])

    split = api_client.post("/api/payments/split", json={"doc_a": a, "doc_b": b})
    assert split.status_code == 200
    assert [d["id"] for d in split.json()["documents"]] == [a]


def test_an_override_on_one_document_is_rejected(api_client: TestClient) -> None:
    assert api_client.post("/api/payments/merge", json={"doc_a": 5, "doc_b": 5}).status_code == 422


def test_merge_with_an_unknown_document_is_a_404_not_a_500(
    api_client: TestClient, seeded_document_id: int
) -> None:
    resp = api_client.post(
        "/api/payments/merge", json={"doc_a": seeded_document_id, "doc_b": 99999999}
    )
    assert resp.status_code == 404


def test_an_unknown_document_is_a_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/99999999/payment").status_code == 404


def test_duplicates_lists_the_collapsed_group(
    api_client: TestClient, payment_pair: tuple[int, int]
) -> None:
    a, b = payment_pair
    groups = api_client.get("/api/payments/duplicates").json()["groups"]
    assert any(sorted(g["document_ids"]) == sorted([a, b]) for g in groups)


def test_anonymous_access_is_refused(anon_client: TestClient) -> None:
    assert anon_client.get("/api/payments/duplicates").status_code in (401, 403)
