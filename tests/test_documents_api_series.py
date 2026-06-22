"""Integration tests for GET /api/documents/{id}/series."""

import asyncio
import hashlib
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import Document, DocumentSource, DocumentStatus, Kind, Sender

pytestmark = pytest.mark.integration


async def _seed_series(
    database_url: str,
    sender_name: str,
    kind_slug: str,
    rows: list[tuple[str, str]],
) -> list[int]:
    """Upsert sender, look up kind by slug, insert documents; return ids in row order."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # Upsert sender.
            existing_sender = (
                await session.execute(select(Sender).where(Sender.name == sender_name))
            ).scalar_one_or_none()
            if existing_sender is None:
                sender = Sender(name=sender_name)
                session.add(sender)
                await session.flush()
            else:
                sender = existing_sender

            # Look up Kind by slug (seeded by migrations).
            kind = (
                await session.execute(select(Kind).where(Kind.slug == kind_slug))
            ).scalar_one_or_none()
            if kind is None:
                raise ValueError(f"Kind slug not found: {kind_slug!r}")

            ids: list[int] = []
            for doc_date, amount_str in rows:
                from datetime import date

                marker = f"{sender_name}:{kind_slug}:{doc_date}:{amount_str}:{id(rows)}"
                document = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    sender_id=sender.id,
                    kind_id=kind.id,
                    document_date=date.fromisoformat(doc_date),
                    amount_total=Decimal(amount_str),
                    currency="EUR",
                )
                session.add(document)
                await session.flush()
                ids.append(document.id)

            await session.commit()
            return ids
    finally:
        await engine.dispose()


def seed_series(
    database_url: str,
    sender: str,
    kind_slug: str,
    rows: list[tuple[str, str]],
) -> list[int]:
    """Sync wrapper around _seed_series."""
    return asyncio.run(_seed_series(database_url, sender, kind_slug, rows))


async def _seed_bare_document(database_url: str, marker: str) -> int:
    """Insert a Document with no sender/kind; return its id."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
            )
            session.add(document)
            await session.flush()
            await session.commit()
            return document.id
    finally:
        await engine.dispose()


def seed_bare_document(database_url: str, marker: str) -> int:
    """Sync wrapper around _seed_bare_document."""
    return asyncio.run(_seed_bare_document(database_url, marker))


def test_series_endpoint_ok(api_client: TestClient, api_database_url: str) -> None:
    ids = seed_series(
        api_database_url,
        "Vattenfall",
        "utility-bill",
        [("2025-01-03", "100.00"), ("2025-02-02", "100.00"), ("2025-03-04", "130.00")],
    )
    response = api_client.get(f"/api/documents/{ids[-1]}/series")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["reference"]["verdict"] == "higher"
    assert len(body["points"]) >= 3


def test_series_endpoint_insufficient(api_client: TestClient, api_database_url: str) -> None:
    ids = seed_series(api_database_url, "Eneco", "utility-bill", [("2025-01-01", "50.00")])
    response = api_client.get(f"/api/documents/{ids[0]}/series")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient"


def test_series_endpoint_no_sender_or_kind(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_bare_document(api_database_url, "bare")  # no sender/kind
    response = api_client.get(f"/api/documents/{doc_id}/series")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient"


def test_series_endpoint_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/999999999/series").status_code == 404
