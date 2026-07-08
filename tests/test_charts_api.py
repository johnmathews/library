"""Integration tests for GET /api/charts (aggregate series enumeration)."""

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


async def _seed(
    database_url: str, sender_name: str, kind_slug: str, rows: list[tuple[str, str]]
) -> None:
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
            for doc_date, amount in rows:
                marker = f"charts:{sender_name}:{kind_slug}:{doc_date}:{amount}"
                session.add(
                    Document(
                        sha256=hashlib.sha256(marker.encode()).hexdigest(),
                        mime_type="application/pdf",
                        source=DocumentSource.UPLOAD,
                        status=DocumentStatus.INDEXED,
                        sender_id=sender.id,
                        kind_id=kind.id,
                        document_date=date.fromisoformat(doc_date),
                        amount_total=Decimal(amount),
                        currency="EUR",
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


def seed(database_url: str, sender: str, kind_slug: str, rows: list[tuple[str, str]]) -> None:
    asyncio.run(_seed(database_url, sender, kind_slug, rows))


def test_charts_lists_eligible_series_and_excludes_sparse(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    eligible = f"ChartsEligible-{tag}"
    sparse = f"ChartsSparse-{tag}"
    seed(
        api_database_url,
        eligible,
        "utility-bill",
        [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")],
    )
    seed(api_database_url, sparse, "invoice", [("2025-01-01", "20.00"), ("2025-02-01", "22.00")])

    response = api_client.get("/api/charts")
    assert response.status_code == 200, response.text
    body = response.json()
    series = body["series"]

    by_sender = {entry["sender"]: entry for entry in series}
    assert eligible in by_sender  # 3 docs -> charted
    assert sparse not in by_sender  # 2 docs -> below series_min_documents

    entry = by_sender[eligible]
    assert entry["status"] == "ok"
    assert entry["kind"] == "utility-bill"
    assert len(entry["points"]) == 3
    assert entry["sender_id"] is not None and entry["kind_id"] is not None
    # Every returned series is fully summarised.
    assert all(e["status"] == "ok" for e in series)


def test_charts_surfaces_near_threshold_candidates(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    charted = f"ChartsFull-{tag}"
    candidate = f"ChartsCand-{tag}"
    # Three docs -> a real chart; two docs -> a candidate (one short of min=3).
    seed(
        api_database_url,
        charted,
        "utility-bill",
        [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")],
    )
    seed(
        api_database_url,
        candidate,
        "invoice",
        [("2025-01-01", "20.00"), ("2025-02-01", "22.00")],
    )

    body = api_client.get("/api/charts").json()
    candidates = {c["sender"]: c for c in body["candidates"]}

    assert candidate in candidates
    entry = candidates[candidate]
    assert entry["count"] == 2
    assert entry["needed"] == 3
    assert entry["kind"] == "invoice"
    assert entry["currency"] == "EUR"
    assert len(entry["document_ids"]) == 2
    assert entry["sender_id"] is not None and entry["kind_id"] is not None

    # A fully-charted series is a chart, never a candidate...
    assert charted not in candidates
    # ...and a candidate never leaks into the charted-series list.
    assert candidate not in {s["sender"] for s in body["series"]}


def test_promoting_a_candidate_removes_it_from_the_list(
    api_client: TestClient, api_database_url: str
) -> None:
    """Once a candidate is promoted into an authored series, its bucket must stop
    being offered — otherwise a reload would invite a duplicate authored series."""
    tag = uuid.uuid4().hex[:8]
    candidate = f"ChartsPromote-{tag}"
    seed(
        api_database_url,
        candidate,
        "invoice",
        [("2025-01-01", "20.00"), ("2025-02-01", "22.00")],
    )

    before = api_client.get("/api/charts").json()
    entry = next(c for c in before["candidates"] if c["sender"] == candidate)

    # Promote: create an authored series seeded with the bucket's documents,
    # exactly as the /charts "Create chart" button does.
    created = api_client.post(
        "/api/charts/authored",
        json={
            "name": f"{entry['sender']} · {entry['kind']}",
            "currency": entry["currency"],
            "document_ids": entry["document_ids"],
        },
    )
    assert created.status_code == 201, created.text

    after = api_client.get("/api/charts").json()
    # The bucket is no longer offered as a candidate (its signature now backs an
    # authored series)...
    assert candidate not in {c["sender"] for c in after["candidates"]}
    # ...and the authored series it became is present in the charted list
    # (its title is the name we promoted it under).
    assert any(
        s.get("authored_id") is not None and s.get("title") == f"{candidate} · invoice"
        for s in after["series"]
    )


def test_charts_requires_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/charts").status_code == 401


def _seed_one(api_client: TestClient, api_database_url: str) -> tuple[str, dict[str, object]]:
    """Seed one eligible series and return (series_id, the /api/charts entry)."""
    tag = uuid.uuid4().hex[:8]
    sender = f"ChartsOne-{tag}"
    seed(
        api_database_url,
        sender,
        "utility-bill",
        [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")],
    )
    body = api_client.get("/api/charts").json()
    entry = next(e for e in body["series"] if e["sender"] == sender)
    series_id = f"{entry['sender_id']}-{entry['kind_id']}-{entry['currency']}"
    return series_id, entry


def test_single_chart_fetch_returns_the_series(
    api_client: TestClient, api_database_url: str
) -> None:
    series_id, entry = _seed_one(api_client, api_database_url)

    response = api_client.get(f"/api/charts/{series_id}")
    assert response.status_code == 200, response.text
    single = response.json()
    assert single["status"] == "ok"
    assert single["sender_id"] == entry["sender_id"]
    assert single["kind_id"] == entry["kind_id"]
    assert single["currency"] == entry["currency"]
    assert len(single["points"]) == 3


def test_single_chart_404_for_unresolvable_id(api_client: TestClient) -> None:
    # Well-formed but no such (sender, kind).
    assert api_client.get("/api/charts/999999-999999-EUR").status_code == 404
    # Malformed id.
    assert api_client.get("/api/charts/not-a-series").status_code == 404


def test_meta_override_persists_and_appears_everywhere(
    api_client: TestClient, api_database_url: str
) -> None:
    series_id, _ = _seed_one(api_client, api_database_url)

    put = api_client.put(
        f"/api/charts/{series_id}/meta",
        json={"title": "Main flat — energy", "description": "Switched tariff in March."},
    )
    assert put.status_code == 200, put.text
    updated = put.json()
    assert updated["title"] == "Main flat — energy"
    assert updated["description"] == "Switched tariff in March."

    # Visible on the single fetch …
    single = api_client.get(f"/api/charts/{series_id}").json()
    assert single["title"] == "Main flat — energy"
    assert single["description"] == "Switched tariff in March."

    # … and on the aggregate list.
    listed = api_client.get("/api/charts").json()["series"]
    entry = next(
        e for e in listed if f"{e['sender_id']}-{e['kind_id']}-{e['currency']}" == series_id
    )
    assert entry["title"] == "Main flat — energy"
    assert entry["description"] == "Switched tariff in March."


def test_meta_override_partial_update_leaves_other_field(
    api_client: TestClient, api_database_url: str
) -> None:
    series_id, _ = _seed_one(api_client, api_database_url)

    api_client.put(f"/api/charts/{series_id}/meta", json={"title": "Keep me"})
    # A later PUT that only sets description must not wipe the stored title.
    api_client.put(f"/api/charts/{series_id}/meta", json={"description": "Just the desc"})

    single = api_client.get(f"/api/charts/{series_id}").json()
    assert single["title"] == "Keep me"
    assert single["description"] == "Just the desc"


def test_meta_override_unknown_series_404(api_client: TestClient) -> None:
    response = api_client.put("/api/charts/999999-999999-EUR/meta", json={"title": "x"})
    assert response.status_code == 404


def test_single_chart_requires_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/charts/1-2-EUR").status_code == 401
    assert anon_client.put("/api/charts/1-2-EUR/meta", json={"title": "x"}).status_code == 401
