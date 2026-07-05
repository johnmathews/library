"""Integration tests for the comment CRUD API (U3): free-text comments
attached to an existing document, with a re-embed deferred on every mutation
so /ask picks up the change (see docs/api.md).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from library.models import DocumentSource
from tests.conftest import fetch_all
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def _embed_jobs(database_url: str, document_id: int) -> list[tuple[Any, ...]]:
    return fetch_all(
        database_url,
        "SELECT task_name FROM procrastinate_jobs "
        "WHERE task_name = 'library.jobs.embed_document' "
        "AND (args ->> 'document_id')::bigint = :id",
        id=document_id,
    )


def test_comment_crud_and_events(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "u3-comment-crud")

    # create
    response = api_client.post(
        f"/api/documents/{doc_id}/comments", json={"body": "this is my current house"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cid = body["id"]
    assert body["body"] == "this is my current house"
    assert body["document_id"] == doc_id
    assert body["author_id"] is not None
    assert body["created_at"]

    # list newest-first
    response = api_client.get(f"/api/documents/{doc_id}/comments")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [cid]

    # edit
    response = api_client.patch(
        f"/api/documents/{doc_id}/comments/{cid}", json={"body": "current house (edited)"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "current house (edited)"

    # detail payload includes comments
    response = api_client.get(f"/api/documents/{doc_id}")
    assert response.status_code == 200
    assert any(c["id"] == cid for c in response.json()["comments"])

    # delete
    assert api_client.delete(f"/api/documents/{doc_id}/comments/{cid}").status_code == 204
    assert api_client.get(f"/api/documents/{doc_id}/comments").json() == []

    # each mutation (create/edit/delete) wrote an audit event...
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id ORDER BY id",
        id=doc_id,
    )
    event_names = [row[0] for row in events]
    assert "comment_added" in event_names
    assert "comment_edited" in event_names
    assert "comment_deleted" in event_names

    # ...and deferred a re-embed (one job row per mutation).
    assert len(_embed_jobs(api_database_url, doc_id)) == 3


def test_comments_newest_first(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "u3-comment-order")
    first = api_client.post(f"/api/documents/{doc_id}/comments", json={"body": "first"}).json()
    second = api_client.post(f"/api/documents/{doc_id}/comments", json={"body": "second"}).json()

    response = api_client.get(f"/api/documents/{doc_id}/comments")
    assert [c["id"] for c in response.json()] == [second["id"], first["id"]]


def test_comment_body_min_length(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "u3-comment-empty")
    response = api_client.post(f"/api/documents/{doc_id}/comments", json={"body": ""})
    assert response.status_code == 422


def test_comment_404_on_unknown_or_deleted_document(
    api_client: TestClient, api_database_url: str
) -> None:
    assert (
        api_client.post("/api/documents/987654321/comments", json={"body": "x"}).status_code == 404
    )
    assert api_client.get("/api/documents/987654321/comments").status_code == 404

    doc_id = seed_document(api_database_url, "u3-comment-deleted")
    assert api_client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert (
        api_client.post(f"/api/documents/{doc_id}/comments", json={"body": "x"}).status_code == 404
    )


def test_comment_404_on_unknown_comment_id(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "u3-comment-unknown-cid")
    assert (
        api_client.patch(f"/api/documents/{doc_id}/comments/999999", json={"body": "x"}).status_code
        == 404
    )
    assert api_client.delete(f"/api/documents/{doc_id}/comments/999999").status_code == 404


def test_comment_scoped_to_its_document(api_client: TestClient, api_database_url: str) -> None:
    """A comment on document A is not reachable through document B's routes."""
    doc_a = seed_document(api_database_url, "u3-comment-scope-a")
    doc_b = seed_document(api_database_url, "u3-comment-scope-b")
    comment = api_client.post(f"/api/documents/{doc_a}/comments", json={"body": "a's"}).json()

    assert (
        api_client.patch(
            f"/api/documents/{doc_b}/comments/{comment['id']}", json={"body": "hijack"}
        ).status_code
        == 404
    )
    assert api_client.delete(f"/api/documents/{doc_b}/comments/{comment['id']}").status_code == 404


def test_works_on_notes_too(api_client: TestClient, api_database_url: str) -> None:
    """A comment can be attached to any document, including a note."""
    doc_id = seed_document(api_database_url, "u3-comment-on-note", source=DocumentSource.NOTE)
    response = api_client.post(f"/api/documents/{doc_id}/comments", json={"body": "noted"})
    assert response.status_code == 201, response.text
