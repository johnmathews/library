"""Integration tests for authored-series signature/suggestion/odd-one-out endpoints."""

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
    database_url: str, sender_name: str, kind_slug: str, count: int, *, currency: str = "EUR"
) -> list[int]:
    """Seed ``count`` indexed, amount-bearing documents; return their ids."""
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
            for n in range(count):
                marker = f"suggest-api:{sender_name}:{kind_slug}:{n}:{uuid.uuid4()}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    sender_id=sender.id,
                    kind_id=kind.id,
                    document_date=date(2025, 1, n + 1),
                    amount_total=Decimal("100.00"),
                    currency=currency,
                    title=f"{sender_name} {n}",
                )
                session.add(doc)
                await session.flush()
                ids.append(doc.id)
            await session.commit()
            return ids
    finally:
        await engine.dispose()


def seed_docs(
    database_url: str, sender: str, kind_slug: str, count: int, *, currency: str = "EUR"
) -> list[int]:
    return asyncio.run(_seed_docs(database_url, sender, kind_slug, count, currency=currency))


def _create_series(client: TestClient, name: str, document_ids: list[int]) -> int:
    response = client.post(
        "/api/charts/authored",
        json={"name": name, "currency": "EUR", "document_ids": document_ids},
    )
    assert response.status_code == 201, response.text
    return response.json()["authored_id"]


def test_signature_endpoint_shape(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"SigCo-{tag}", "utility-bill", 3)
    aid = _create_series(api_client, "Sig", ids)

    body = api_client.get(f"/api/charts/authored/{aid}/signature")
    assert body.status_code == 200, body.text
    sig = body.json()
    assert set(sig) == {
        "sender_id",
        "kind_id",
        "currency",
        "member_count",
        "dominant_count",
        "dominance",
    }
    assert sig["currency"] == "EUR"
    assert sig["member_count"] == 3
    assert sig["dominant_count"] == 3
    assert sig["dominance"] == 1.0


def test_suggestions_then_accept(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"AcceptCo-{tag}", "utility-bill", 4)
    members, candidate = ids[:3], ids[3]
    aid = _create_series(api_client, "Accept", members)

    listed = api_client.get(f"/api/charts/authored/{aid}/suggestions").json()
    assert listed["count"] == 1
    assert listed["suggestions"][0]["id"] == candidate
    assert set(listed["suggestions"][0]) == {
        "id",
        "title",
        "sender",
        "kind",
        "currency",
        "document_date",
        "amount",
    }

    accepted = api_client.post(f"/api/charts/authored/{aid}/suggestions/{candidate}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["count"] == 4  # now a member

    after = api_client.get(f"/api/charts/authored/{aid}/suggestions").json()
    assert after["count"] == 0  # no longer suggested


def test_dismiss_suppresses_resuggestion(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"DismissCo-{tag}", "utility-bill", 4)
    members, candidate = ids[:3], ids[3]
    aid = _create_series(api_client, "Dismiss", members)

    assert api_client.get(f"/api/charts/authored/{aid}/suggestions").json()["count"] == 1

    dismissed = api_client.post(f"/api/charts/authored/{aid}/suggestions/{candidate}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["count"] == 0

    after = api_client.get(f"/api/charts/authored/{aid}/suggestions").json()
    assert candidate not in {s["id"] for s in after["suggestions"]}


def test_odd_ones_out_shape(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    main = seed_docs(api_database_url, f"OddMain-{tag}", "utility-bill", 3)
    stray = seed_docs(api_database_url, f"OddStray-{tag}", "utility-bill", 1)
    aid = _create_series(api_client, "Odd", main + stray)

    body = api_client.get(f"/api/charts/authored/{aid}/odd-ones-out")
    assert body.status_code == 200, body.text
    members = body.json()["members"]
    assert len(members) == 1
    entry = members[0]
    assert entry["id"] == stray[0]
    assert entry["axis"] == "sender"
    assert entry["reason"] is None  # no api key configured in tests
    assert {
        "id",
        "title",
        "sender",
        "kind",
        "currency",
        "document_date",
        "amount",
        "axis",
        "reason",
    } == set(entry)


def test_charts_entries_carry_signature_and_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    ids = seed_docs(api_database_url, f"ChartsExtra-{tag}", "utility-bill", 4)
    name = f"Extras-{tag}"
    _create_series(api_client, name, ids[:3])

    listed = api_client.get("/api/charts").json()["series"]
    entry = next(e for e in listed if e.get("authored_id") is not None and e.get("title") == name)
    assert "signature" in entry
    assert isinstance(entry["suggestion_count"], int)
    assert isinstance(entry["odd_one_out_count"], int)
    assert entry["suggestion_count"] == 1  # the 4th matching doc
    assert entry["odd_one_out_count"] == 0


def test_new_endpoints_require_authentication(
    anon_client: TestClient, api_database_url: str
) -> None:
    assert anon_client.get("/api/charts/authored/1/signature").status_code == 401
    assert anon_client.get("/api/charts/authored/1/suggestions").status_code == 401
    assert anon_client.get("/api/charts/authored/1/odd-ones-out").status_code == 401
    assert anon_client.post("/api/charts/authored/1/suggestions/1/accept").status_code == 401
    assert anon_client.post("/api/charts/authored/1/suggestions/1/dismiss").status_code == 401
