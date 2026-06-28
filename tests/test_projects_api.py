"""Integration tests for the projects REST API (W6):
GET/POST /api/projects, GET/PATCH/DELETE /api/projects/{slug}.

Backed by the shared ``library.projects`` service. The projects table is
shared across API tests, so assertions target seeded markers (unique
slugs) rather than exact table contents.
"""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fetch_all
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def create_project(client: TestClient, name: str, **body: object) -> dict[str, object]:
    response = client.post("/api/projects", json={"name": name, **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_get_with_counts(admin_client: TestClient, api_database_url: str) -> None:
    project = create_project(admin_client, "W6 Kitchen Reno", description="renovation papers")
    assert project["slug"] == "w6-kitchen-reno"  # slug defaults to slugify(name)
    assert project["name"] == "W6 Kitchen Reno"
    assert project["description"] == "renovation papers"
    assert project["archived"] is False
    assert project["document_count"] == 0

    # Attach a document so the count is non-zero.
    seed_document(api_database_url, "w6-proj-api-doc", project_slugs=["w6-kitchen-reno"])

    one = admin_client.get("/api/projects/w6-kitchen-reno")
    assert one.status_code == 200, one.text
    assert one.json()["document_count"] == 1

    listing = admin_client.get("/api/projects").json()
    names = [p["name"] for p in listing]
    assert names == sorted(names)  # ordered by name
    row = next(p for p in listing if p["slug"] == "w6-kitchen-reno")
    assert row["document_count"] == 1


def test_create_explicit_slug_is_normalised(admin_client: TestClient) -> None:
    project = create_project(admin_client, "W6 Explicit", slug="W6 Custom Slug!!")
    assert project["slug"] == "w6-custom-slug"


def test_create_duplicate_slug_409(admin_client: TestClient) -> None:
    create_project(admin_client, "W6 Dup")
    response = admin_client.post("/api/projects", json={"name": "W6 Dup"})
    assert response.status_code == 409, response.text
    assert "w6-dup" in response.json()["detail"]


def test_get_unknown_404(api_client: TestClient) -> None:
    assert api_client.get("/api/projects/w6-does-not-exist").status_code == 404


def test_patch_updates_name_description_and_archive(admin_client: TestClient) -> None:
    create_project(admin_client, "W6 Patch Me", description="before")

    response = admin_client.patch(
        "/api/projects/w6-patch-me", json={"name": "W6 Patched", "description": "after"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slug"] == "w6-patch-me"  # slug stays stable across rename
    assert body["name"] == "W6 Patched"
    assert body["description"] == "after"

    # Archiving hides it from the default list but keeps it with include_archived.
    archived = admin_client.patch("/api/projects/w6-patch-me", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    default_slugs = [p["slug"] for p in admin_client.get("/api/projects").json()]
    assert "w6-patch-me" not in default_slugs
    all_slugs = [
        p["slug"]
        for p in admin_client.get("/api/projects", params={"include_archived": True}).json()
    ]
    assert "w6-patch-me" in all_slugs

    # Unarchiving restores it.
    restored = admin_client.patch("/api/projects/w6-patch-me", json={"archived": False})
    assert restored.json()["archived"] is False
    assert "w6-patch-me" in [p["slug"] for p in admin_client.get("/api/projects").json()]


def test_patch_unknown_404(admin_client: TestClient) -> None:
    assert admin_client.patch("/api/projects/w6-nope", json={"name": "x"}).status_code == 404


def test_delete_removes_memberships_but_not_documents(
    admin_client: TestClient, api_database_url: str
) -> None:
    create_project(admin_client, "W6 Delete Me")
    document_id = seed_document(
        api_database_url, "w6-proj-delete-doc", project_slugs=["w6-delete-me"]
    )

    assert admin_client.delete("/api/projects/w6-delete-me").status_code == 204

    # Project gone, but the document survives with no membership.
    assert admin_client.get("/api/projects/w6-delete-me").status_code == 404
    doc = admin_client.get(f"/api/documents/{document_id}")
    assert doc.status_code == 200
    assert doc.json()["projects"] == []

    rows = fetch_all(
        api_database_url,
        "SELECT count(*) FROM document_projects WHERE document_id = :id",
        id=document_id,
    )
    assert rows == [(0,)]


def test_delete_unknown_404(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/projects/w6-nope-delete").status_code == 404


def test_projects_require_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/projects").status_code == 401
    assert anon_client.post("/api/projects", json={"name": "x"}).status_code == 401
    assert anon_client.get("/api/projects/anything").status_code == 401


def test_project_mutations_require_admin(api_client: TestClient, admin_client: TestClient) -> None:
    """Normal authenticated users can read projects but cannot mutate them."""
    # A normal user can still list/read (GET is open to all authenticated users).
    assert api_client.get("/api/projects").status_code == 200

    # ...but every mutation is admin-only.
    assert api_client.post("/api/projects", json={"name": "W3 Forbidden"}).status_code == 403
    # Seed a real project as admin so PATCH/DELETE hit the guard, not a 404.
    create_project(admin_client, "W3 Gated")
    assert api_client.patch("/api/projects/w3-gated", json={"name": "x"}).status_code == 403
    assert api_client.delete("/api/projects/w3-gated").status_code == 403
    # The project is untouched by the rejected mutations.
    assert admin_client.get("/api/projects/w3-gated").json()["name"] == "W3 Gated"
