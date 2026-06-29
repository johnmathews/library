"""Integration tests for authored (user-curated) series — /api/charts/authored (W14)."""

import asyncio
import hashlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Document, DocumentSource, DocumentStatus, Kind, Sender

pytestmark = pytest.mark.integration


async def _seed_docs(
    database_url: str, sender_name: str, kind_slug: str, rows: list[tuple[str, str]]
) -> list[int]:
    """Seed documents (one per row) and return their ids, in input order."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            sender = (
                await session.execute(select(Sender).where(Sender.name == sender_name))
            ).scalar_one_or_none()
            if sender is None:
                sender = Sender(name=sender_name)
                session.add(sender)
                await session.flush()
            kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
            ids: list[int] = []
            for doc_date, amount in rows:
                marker = f"authored:{sender_name}:{kind_slug}:{doc_date}:{amount}:{uuid.uuid4()}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    sender_id=sender.id,
                    kind_id=kind.id,
                    document_date=date.fromisoformat(doc_date),
                    amount_total=Decimal(amount),
                    currency="EUR",
                    title=f"{sender_name} {doc_date}",
                )
                session.add(doc)
                await session.flush()
                ids.append(doc.id)
            await session.commit()
            return ids
    finally:
        await engine.dispose()


def seed_docs(
    database_url: str, sender: str, kind_slug: str, rows: list[tuple[str, str]]
) -> list[int]:
    return asyncio.run(_seed_docs(database_url, sender, kind_slug, rows))


ROWS = [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")]


def test_create_authored_series_with_members(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"AuthoredCo-{tag}", "utility-bill", ROWS)

    response = api_client.post(
        "/api/charts/authored",
        json={"name": "My energy", "currency": "EUR", "document_ids": ids},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["authored_id"] is not None
    assert body["title"] == "My energy"
    assert body["currency"] == "EUR"
    # No emergent identity for an authored series.
    assert body["sender_id"] is None and body["kind_id"] is None
    assert len(body["points"]) == 3
    assert body["count"] == 3


def test_add_and_remove_members(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"AuthoredAdd-{tag}", "utility-bill", ROWS)

    created = api_client.post(
        "/api/charts/authored", json={"name": "Series", "currency": "EUR"}
    ).json()
    aid = created["authored_id"]
    assert created["count"] == 0

    # Add two members.
    api_client.post(f"/api/charts/authored/{aid}/members", json={"document_id": ids[0]})
    after_two = api_client.post(
        f"/api/charts/authored/{aid}/members", json={"document_id": ids[1]}
    ).json()
    assert after_two["count"] == 2

    # Adding the same document again is idempotent.
    again = api_client.post(
        f"/api/charts/authored/{aid}/members", json={"document_id": ids[1]}
    ).json()
    assert again["count"] == 2

    # Remove one.
    after_remove = api_client.delete(f"/api/charts/authored/{aid}/members/{ids[0]}").json()
    assert after_remove["count"] == 1


def test_add_member_unknown_document_404(api_client: TestClient, api_database_url: str) -> None:
    created = api_client.post("/api/charts/authored", json={"name": "S"}).json()
    aid = created["authored_id"]
    response = api_client.post(
        f"/api/charts/authored/{aid}/members", json={"document_id": 999999999}
    )
    assert response.status_code == 404


def test_list_includes_authored(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"AuthoredList-{tag}", "utility-bill", ROWS)
    name = f"Listed-{tag}"
    api_client.post(
        "/api/charts/authored",
        json={"name": name, "currency": "EUR", "document_ids": ids},
    )

    listed = api_client.get("/api/charts").json()["series"]
    authored = [e for e in listed if e.get("authored_id") is not None]
    assert any(e["title"] == name for e in authored)


def test_single_fetch_by_authored_id(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"AuthoredOne-{tag}", "utility-bill", ROWS)
    created = api_client.post(
        "/api/charts/authored",
        json={"name": "Single", "currency": "EUR", "document_ids": ids},
    ).json()
    aid = created["authored_id"]

    single = api_client.get(f"/api/charts/a-{aid}")
    assert single.status_code == 200, single.text
    body = single.json()
    assert body["authored_id"] == aid
    assert body["title"] == "Single"
    assert len(body["points"]) == 3


def test_single_fetch_unknown_authored_404(api_client: TestClient) -> None:
    assert api_client.get("/api/charts/a-999999999").status_code == 404


def test_summary_parity_with_emergent(api_client: TestClient, api_database_url: str) -> None:
    """An authored series over the same documents as an emergent one yields the
    same distribution + trend."""
    tag = uuid.uuid4().hex[:8]
    sender = f"ParityCo-{tag}"
    ids = seed_docs(api_database_url, sender, "utility-bill", ROWS)

    listed = api_client.get("/api/charts").json()["series"]
    emergent = next(e for e in listed if e.get("sender") == sender)

    created = api_client.post(
        "/api/charts/authored",
        json={"name": "Parity", "currency": "EUR", "document_ids": ids},
    ).json()

    for field in ("mean", "median", "stdev", "min", "max", "count"):
        assert created[field] == emergent[field], field
    assert created["trend"]["direction"] == emergent["trend"]["direction"]
    assert created["trend"]["change_pct"] == emergent["trend"]["change_pct"]
    # Same point amounts, in date order.
    assert [p["amount"] for p in created["points"]] == [p["amount"] for p in emergent["points"]]


def test_patch_name_and_description(api_client: TestClient, api_database_url: str) -> None:
    created = api_client.post(
        "/api/charts/authored", json={"name": "Old name", "description": "old"}
    ).json()
    aid = created["authored_id"]

    patched = api_client.patch(
        f"/api/charts/authored/{aid}",
        json={"name": "New name", "description": "new desc"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["title"] == "New name"
    assert body["description"] == "new desc"

    # Partial update leaves the other field.
    again = api_client.patch(
        f"/api/charts/authored/{aid}", json={"description": "only desc"}
    ).json()
    assert again["title"] == "New name"
    assert again["description"] == "only desc"


def test_patch_unknown_404(api_client: TestClient) -> None:
    assert api_client.patch("/api/charts/authored/999999999", json={"name": "x"}).status_code == 404


def test_delete_authored_series(api_client: TestClient, api_database_url: str) -> None:
    created = api_client.post("/api/charts/authored", json={"name": "Doomed"}).json()
    aid = created["authored_id"]

    assert api_client.delete(f"/api/charts/authored/{aid}").status_code == 204
    assert api_client.get(f"/api/charts/a-{aid}").status_code == 404
    assert api_client.delete(f"/api/charts/authored/{aid}").status_code == 404


def test_authored_requires_authentication(anon_client: TestClient) -> None:
    assert anon_client.post("/api/charts/authored", json={"name": "x"}).status_code == 401
    assert anon_client.patch("/api/charts/authored/1", json={"name": "x"}).status_code == 401
    assert anon_client.delete("/api/charts/authored/1").status_code == 401
