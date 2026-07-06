"""Integration tests for the saved-views REST API: per-user CRUD, reorder,
and cross-user isolation.

``auth_user`` (and thus ``api_client``) is a fresh user per test, so each
test's user starts with zero saved views — list/reorder assertions see only
what the test created.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def _create(client: TestClient, name: str, **body: Any) -> dict[str, Any]:
    response = client.post("/api/saved-views", json={"name": name, **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_list_round_trips_filter_state(api_client: TestClient) -> None:
    state = {"q": "rent", "tag": ["housing", "2026"], "sort": "added_date", "dir": "asc"}
    created = _create(api_client, "Rent docs", filter_state=state, pinned=True)
    assert created["name"] == "Rent docs"
    assert created["filter_state"] == state  # stored and returned verbatim
    assert created["pinned"] is True
    assert created["sort_order"] == 0

    listed = api_client.get("/api/saved-views").json()
    assert [view["id"] for view in listed] == [created["id"]]
    assert listed[0]["filter_state"] == state


def test_update_renames_retargets_and_toggles_pin(api_client: TestClient) -> None:
    view_id = _create(api_client, "Draft", filter_state={"q": "a"})["id"]

    response = api_client.patch(
        f"/api/saved-views/{view_id}",
        json={"name": "Final", "filter_state": {"q": "b"}, "pinned": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Final"
    assert body["filter_state"] == {"q": "b"}
    assert body["pinned"] is True

    # A partial patch touches only the named field.
    response = api_client.patch(f"/api/saved-views/{view_id}", json={"pinned": False})
    assert response.status_code == 200
    body = response.json()
    assert body["pinned"] is False
    assert body["name"] == "Final"  # unchanged


def test_delete_removes_view(api_client: TestClient) -> None:
    view_id = _create(api_client, "Temp")["id"]
    assert api_client.delete(f"/api/saved-views/{view_id}").status_code == 204
    assert api_client.get("/api/saved-views").json() == []
    # Gone: further edits/deletes 404.
    assert api_client.patch(f"/api/saved-views/{view_id}", json={"name": "x"}).status_code == 404
    assert api_client.delete(f"/api/saved-views/{view_id}").status_code == 404


def test_reorder_assigns_sort_order(api_client: TestClient) -> None:
    first = _create(api_client, "reorder-A")["id"]
    second = _create(api_client, "reorder-B")["id"]
    third = _create(api_client, "reorder-C")["id"]

    response = api_client.post("/api/saved-views/reorder", json={"ids": [third, first, second]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert [view["id"] for view in result] == [third, first, second]
    assert [view["sort_order"] for view in result] == [0, 1, 2]

    # The new order persists on a fresh list.
    listed = api_client.get("/api/saved-views").json()
    assert [view["id"] for view in listed] == [third, first, second]


def test_reorder_rejects_id_set_mismatch(api_client: TestClient) -> None:
    first = _create(api_client, "mismatch-A")["id"]
    _create(api_client, "mismatch-B")
    # Missing the second id → 400 (a stale client must not silently drop a view).
    assert api_client.post("/api/saved-views/reorder", json={"ids": [first]}).status_code == 400


def test_views_are_per_user(api_client: TestClient, api_database_url: str) -> None:
    """A view owned by another user is invisible and un-mutable — 404, not 403."""
    mine = _create(api_client, "mine")["id"]

    # Insert a foreign user + their saved view via a sync engine: create_user
    # uses asyncio.run, which conflicts with the active TestClient event loop.
    engine = create_engine(api_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as conn:
            foreign_user_id = conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, display_name, is_active)"
                    " VALUES ('foreign-saved-view-owner', 'x', '', true) RETURNING id"
                )
            ).scalar_one()
            foreign_view_id = conn.execute(
                text(
                    "INSERT INTO saved_views (user_id, name, filter_state)"
                    " VALUES (:uid, 'foreign view', '{}'::jsonb) RETURNING id"
                ),
                {"uid": foreign_user_id},
            ).scalar_one()
    finally:
        engine.dispose()

    listed = api_client.get("/api/saved-views").json()
    ids = {view["id"] for view in listed}
    assert mine in ids
    assert foreign_view_id not in ids  # never leaks into another user's list

    assert (
        api_client.patch(f"/api/saved-views/{foreign_view_id}", json={"name": "hijack"}).status_code
        == 404
    )
    assert api_client.delete(f"/api/saved-views/{foreign_view_id}").status_code == 404


def test_saved_views_require_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/saved-views").status_code == 401
    assert anon_client.post("/api/saved-views", json={"name": "x"}).status_code == 401
