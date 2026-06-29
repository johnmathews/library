"""Integration tests for the taxonomy REST endpoints (W11):
GET /api/kinds, /api/senders, /api/tags.

Backed by the shared ``library.taxonomy`` service (also behind the MCP
list tools, covered in test_mcp.py). The taxonomy tables are shared
across API tests, so assertions target seeded markers rather than exact
table contents.
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def get_json(client: TestClient, path: str) -> list[dict[str, object]]:
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    return body


def test_kinds_lists_seeded_set_with_counts(api_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w11-tax-kind", kind_slug="warranty")

    kinds = get_json(api_client, "/api/kinds")
    by_slug = {kind["slug"]: kind for kind in kinds}
    # The full seeded set is present (zero-count kinds included).
    assert {"invoice", "receipt", "warranty", "other"} <= set(by_slug)
    assert by_slug["warranty"]["name"] == "Warranty"
    assert by_slug["warranty"]["document_count"] >= 1
    assert all(isinstance(kind["document_count"], int) for kind in kinds)
    assert [kind["slug"] for kind in kinds] == sorted(kind["slug"] for kind in kinds)


def test_senders_ordered_by_name_with_counts(api_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w11-tax-sender-1", sender_name="W11 Aaa Energie")
    seed_document(api_database_url, "w11-tax-sender-2", sender_name="W11 Zzz Verzekering")
    seed_document(api_database_url, "w11-tax-sender-3", sender_name="W11 Zzz Verzekering")

    senders = get_json(api_client, "/api/senders")
    names = [sender["name"] for sender in senders]
    assert names == sorted(names)
    by_name = {sender["name"]: sender for sender in senders}
    assert by_name["W11 Aaa Energie"]["document_count"] == 1
    assert by_name["W11 Zzz Verzekering"]["document_count"] == 2
    assert isinstance(by_name["W11 Aaa Energie"]["id"], int)


def test_recipients_ordered_by_name_with_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    seed_document(api_database_url, "w2-tax-recipient-1", recipient_name="W2 Aaa John")
    seed_document(api_database_url, "w2-tax-recipient-2", recipient_name="W2 Zzz Wife")
    seed_document(api_database_url, "w2-tax-recipient-3", recipient_name="W2 Zzz Wife")

    recipients = get_json(api_client, "/api/recipients")
    names = [recipient["name"] for recipient in recipients]
    assert names == sorted(names)
    by_name = {recipient["name"]: recipient for recipient in recipients}
    assert by_name["W2 Aaa John"]["document_count"] == 1
    assert by_name["W2 Zzz Wife"]["document_count"] == 2
    assert isinstance(by_name["W2 Aaa John"]["id"], int)


def test_tags_ordered_by_name_count_excludes_deleted(
    api_client: TestClient, api_database_url: str
) -> None:
    keep = seed_document(api_database_url, "w11-tax-tag-keep", tag_slugs=["w11-tax-tag"])
    gone = seed_document(api_database_url, "w11-tax-tag-gone", tag_slugs=["w11-tax-tag"])
    assert keep != gone
    assert api_client.delete(f"/api/documents/{gone}").status_code == 204

    tags = get_json(api_client, "/api/tags")
    names = [tag["name"] for tag in tags]
    assert names == sorted(names)
    tag = next(tag for tag in tags if tag["slug"] == "w11-tax-tag")
    assert tag["document_count"] == 1  # the deleted document is not counted


def test_taxonomy_requires_authentication(anon_client: TestClient) -> None:
    for path in ("/api/kinds", "/api/senders", "/api/recipients", "/api/tags"):
        assert anon_client.get(path).status_code == 401, path
