"""summarize_series applies pin/exclude overrides (with FX) — via the series API."""

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

from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    Kind,
    OverrideAction,
    Sender,
    SeriesMembershipOverride,
)

pytestmark = pytest.mark.integration


async def _seed(database_url: str, tag: str) -> dict[str, object]:
    """Seed a 3-doc EUR utility-bill series plus two unrelated docs to pin.

    Returns sender_id, kind_id, the series doc ids, and ids of the pinnable
    same-currency (EUR) and cross-currency (GBP) outsiders.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            sender = Sender(name=f"OverrideEnergy-{tag}")
            session.add(sender)
            await session.flush()
            kind = (
                await session.execute(select(Kind).where(Kind.slug == "utility-bill"))
            ).scalar_one()
            other_kind = (
                await session.execute(select(Kind).where(Kind.slug == "invoice"))
            ).scalar_one()

            def make(kind_id: int, ddate: str, amount: str, currency: str) -> Document:
                marker = f"{tag}:{kind_id}:{ddate}:{amount}:{currency}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    sender_id=sender.id,
                    kind_id=kind_id,
                    document_date=date.fromisoformat(ddate),
                    amount_total=Decimal(amount),
                    currency=currency,
                )
                session.add(doc)
                return doc

            series = [
                make(kind.id, "2022-01-03", "100.00", "EUR"),
                make(kind.id, "2022-02-02", "100.00", "EUR"),
                make(kind.id, "2022-03-04", "100.00", "EUR"),
                make(kind.id, "2022-04-04", "100.00", "EUR"),
            ]
            eur_outsider = make(other_kind.id, "2022-04-01", "200.00", "EUR")
            gbp_outsider = make(other_kind.id, "2022-05-01", "100.00", "GBP")
            await session.flush()
            result = {
                "sender_id": sender.id,
                "kind_id": kind.id,
                "series_ids": [d.id for d in series],
                "eur_outsider": eur_outsider.id,
                "gbp_outsider": gbp_outsider.id,
            }
            await session.commit()
            return result
    finally:
        await engine.dispose()


async def _add_override(
    database_url: str,
    sender_id: int,
    kind_id: int,
    currency: str | None,
    document_id: int,
    action: OverrideAction,
) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            session.add(
                SeriesMembershipOverride(
                    sender_id=sender_id,
                    kind_id=kind_id,
                    currency=currency,
                    document_id=document_id,
                    action=action,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def seed(database_url: str, tag: str) -> dict[str, object]:
    return asyncio.run(_seed(database_url, tag))


def add_override(database_url: str, **kwargs: object) -> None:
    asyncio.run(_add_override(database_url, **kwargs))  # type: ignore[arg-type]


def test_exclude_removes_document_from_series(
    api_client: TestClient, api_database_url: str
) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    series_ids = data["series_ids"]
    add_override(
        api_database_url,
        sender_id=data["sender_id"],
        kind_id=data["kind_id"],
        currency="EUR",
        document_id=series_ids[0],
        action=OverrideAction.EXCLUDE,
    )
    body = api_client.get(f"/api/documents/{series_ids[1]}/series").json()
    assert body["status"] == "ok"
    assert body["count"] == 3
    assert series_ids[0] not in body["document_ids"]
    assert all(p["document_id"] != series_ids[0] for p in body["points"])


def test_pin_same_currency_adds_outsider(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    add_override(
        api_database_url,
        sender_id=data["sender_id"],
        kind_id=data["kind_id"],
        currency="EUR",
        document_id=data["eur_outsider"],
        action=OverrideAction.PIN,
    )
    body = api_client.get(f"/api/documents/{data['series_ids'][0]}/series").json()
    assert body["count"] == 5
    assert data["eur_outsider"] in body["document_ids"]
    pinned_point = next(p for p in body["points"] if p["document_id"] == data["eur_outsider"])
    assert pinned_point["amount"] == "200.00"


def test_pin_cross_currency_is_fx_converted(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    add_override(
        api_database_url,
        sender_id=data["sender_id"],
        kind_id=data["kind_id"],
        currency="EUR",
        document_id=data["gbp_outsider"],
        action=OverrideAction.PIN,
    )
    body = api_client.get(f"/api/documents/{data['series_ids'][0]}/series").json()
    assert body["count"] == 5
    assert data["gbp_outsider"] in body["document_ids"]
    pinned_point = next(p for p in body["points"] if p["document_id"] == data["gbp_outsider"])
    # 100 GBP @2022 (1.237) -> USD 123.70 -> EUR (1.053) = 117.47.
    assert pinned_point["amount"] == "117.47"


def test_no_overrides_leaves_series_unchanged(
    api_client: TestClient, api_database_url: str
) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    body = api_client.get(f"/api/documents/{data['series_ids'][0]}/series").json()
    assert body["count"] == 4
