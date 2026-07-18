"""Integration tests for the matters REST API:
GET/POST /api/matters, GET/PATCH/DELETE /api/matters/{slug}.

Backed by the shared ``library.matters`` service. The matters table is shared
across API tests, so assertions target seeded markers (unique slugs) rather
than exact table contents.
"""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fetch_all
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def create_matter(client: TestClient, name: str, **body: object) -> dict[str, object]:
    response = client.post("/api/matters", json={"name": name, **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_get_with_counts(admin_client: TestClient, api_database_url: str) -> None:
    matter = create_matter(admin_client, "M2 Car Insurance", hint="vehicle policies")
    assert matter["slug"] == "m2-car-insurance"  # slug defaults to slugify(name)
    assert matter["name"] == "M2 Car Insurance"
    assert matter["hint"] == "vehicle policies"
    assert matter["archived"] is False
    assert matter["document_count"] == 0

    # Attach a document so the count is non-zero.
    seed_document(api_database_url, "m2-matter-api-doc", matter_slugs=["m2-car-insurance"])

    one = admin_client.get("/api/matters/m2-car-insurance")
    assert one.status_code == 200, one.text
    assert one.json()["document_count"] == 1

    listing = admin_client.get("/api/matters").json()
    names = [m["name"] for m in listing]
    assert names == sorted(names)  # ordered by name
    row = next(m for m in listing if m["slug"] == "m2-car-insurance")
    assert row["document_count"] == 1


def test_create_explicit_slug_is_normalised(admin_client: TestClient) -> None:
    matter = create_matter(admin_client, "M2 Explicit", slug="M2 Custom Slug!!")
    assert matter["slug"] == "m2-custom-slug"


def test_create_duplicate_slug_409(admin_client: TestClient) -> None:
    create_matter(admin_client, "M2 Dup")
    response = admin_client.post("/api/matters", json={"name": "M2 Dup"})
    assert response.status_code == 409, response.text
    assert "m2-dup" in response.json()["detail"]


def test_get_unknown_404(api_client: TestClient) -> None:
    assert api_client.get("/api/matters/m2-does-not-exist").status_code == 404


def test_patch_updates_name_hint_and_archive(admin_client: TestClient) -> None:
    create_matter(admin_client, "M2 Patch Me", hint="before")

    response = admin_client.patch(
        "/api/matters/m2-patch-me", json={"name": "M2 Patched", "hint": "after"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slug"] == "m2-patch-me"  # slug stays stable across rename
    assert body["name"] == "M2 Patched"
    assert body["hint"] == "after"

    # Archiving hides it from the default list but keeps it with include_archived.
    archived = admin_client.patch("/api/matters/m2-patch-me", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    default_slugs = [m["slug"] for m in admin_client.get("/api/matters").json()]
    assert "m2-patch-me" not in default_slugs
    all_slugs = [
        m["slug"]
        for m in admin_client.get("/api/matters", params={"include_archived": True}).json()
    ]
    assert "m2-patch-me" in all_slugs

    # Unarchiving restores it.
    restored = admin_client.patch("/api/matters/m2-patch-me", json={"archived": False})
    assert restored.json()["archived"] is False
    assert "m2-patch-me" in [m["slug"] for m in admin_client.get("/api/matters").json()]


def test_patch_unknown_404(admin_client: TestClient) -> None:
    assert admin_client.patch("/api/matters/m2-nope", json={"name": "x"}).status_code == 404


def test_delete_removes_memberships_but_not_documents(
    admin_client: TestClient, api_database_url: str
) -> None:
    create_matter(admin_client, "M2 Delete Me")
    document_id = seed_document(
        api_database_url, "m2-matter-delete-doc", matter_slugs=["m2-delete-me"]
    )

    assert admin_client.delete("/api/matters/m2-delete-me").status_code == 204

    # Matter gone, but the document survives with no membership.
    assert admin_client.get("/api/matters/m2-delete-me").status_code == 404
    assert admin_client.get(f"/api/documents/{document_id}").status_code == 200

    rows = fetch_all(
        api_database_url,
        "SELECT count(*) FROM document_matters WHERE document_id = :id",
        id=document_id,
    )
    assert rows == [(0,)]


def test_delete_unknown_404(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/matters/m2-nope-delete").status_code == 404


def test_matters_require_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/matters").status_code == 401
    assert anon_client.post("/api/matters", json={"name": "x"}).status_code == 401
    assert anon_client.get("/api/matters/anything").status_code == 401


def test_matter_mutations_require_admin(api_client: TestClient, admin_client: TestClient) -> None:
    """Normal authenticated users can read matters but cannot mutate them."""
    # A normal user can still list/read (GET is open to all authenticated users).
    assert api_client.get("/api/matters").status_code == 200

    # ...but every mutation is admin-only.
    assert api_client.post("/api/matters", json={"name": "M2 Forbidden"}).status_code == 403
    # Seed a real matter as admin so PATCH/DELETE hit the guard, not a 404.
    create_matter(admin_client, "M2 Gated")
    assert api_client.patch("/api/matters/m2-gated", json={"name": "x"}).status_code == 403
    assert api_client.delete("/api/matters/m2-gated").status_code == 403
    # The matter is untouched by the rejected mutations.
    assert admin_client.get("/api/matters/m2-gated").json()["name"] == "M2 Gated"
