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


def test_charts_requires_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/charts").status_code == 401
