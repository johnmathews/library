"""POST/DELETE /api/series/{sender_id}/{kind_id}/members — toggle pin/exclude."""

import asyncio
import hashlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    Kind,
    Sender,
    SeriesMembershipOverride,
)

pytestmark = pytest.mark.integration


async def _seed(database_url: str, tag: str) -> dict[str, int]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            sender = Sender(name=f"MembershipCo-{tag}")
            session.add(sender)
            await session.flush()
            kind = (
                await session.execute(select(Kind).where(Kind.slug == "utility-bill"))
            ).scalar_one()

            def make(ddate: str) -> Document:
                marker = f"{tag}:{ddate}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    sender_id=sender.id,
                    kind_id=kind.id,
                    document_date=date.fromisoformat(ddate),
                    amount_total=Decimal("100.00"),
                    currency="EUR",
                )
                session.add(doc)
                return doc

            member = make("2022-01-03")
            outsider = make("2022-02-03")
            await session.flush()
            out = {
                "sender_id": sender.id,
                "kind_id": kind.id,
                "member": member.id,
                "outsider": outsider.id,
            }
            await session.commit()
            return out
    finally:
        await engine.dispose()


async def _count_overrides(database_url: str, document_id: int) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(SeriesMembershipOverride)
                    .where(SeriesMembershipOverride.document_id == document_id)
                )
            ).scalar_one()
    finally:
        await engine.dispose()


def seed(database_url: str, tag: str) -> dict[str, int]:
    return asyncio.run(_seed(database_url, tag))


def count_overrides(database_url: str, document_id: int) -> int:
    return asyncio.run(_count_overrides(database_url, document_id))


def _url(data: dict[str, int]) -> str:
    return f"/api/series/{data['sender_id']}/{data['kind_id']}/members"


def test_post_creates_pin(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    response = api_client.post(
        _url(data), params={"currency": "EUR"}, json={"document_id": data["outsider"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pinned"
    assert count_overrides(api_database_url, data["outsider"]) == 1


def test_delete_member_creates_exclude(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    response = api_client.delete(f"{_url(data)}/{data['member']}", params={"currency": "EUR"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "excluded"
    assert count_overrides(api_database_url, data["member"]) == 1


def test_post_then_delete_toggles_pin_off(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    api_client.post(_url(data), params={"currency": "EUR"}, json={"document_id": data["outsider"]})
    # Removing a pinned doc clears the pin rather than adding an exclude.
    response = api_client.delete(f"{_url(data)}/{data['outsider']}", params={"currency": "EUR"})
    assert response.json()["state"] == "cleared"
    assert count_overrides(api_database_url, data["outsider"]) == 0


def test_delete_then_post_toggles_exclude_off(
    api_client: TestClient, api_database_url: str
) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    api_client.delete(f"{_url(data)}/{data['member']}", params={"currency": "EUR"})
    # Re-adding an excluded doc clears the exclude rather than adding a pin.
    response = api_client.post(
        _url(data), params={"currency": "EUR"}, json={"document_id": data["member"]}
    )
    assert response.json()["state"] == "cleared"
    assert count_overrides(api_database_url, data["member"]) == 0


def test_post_is_idempotent(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    body = {"document_id": data["outsider"]}
    api_client.post(_url(data), params={"currency": "EUR"}, json=body)
    api_client.post(_url(data), params={"currency": "EUR"}, json=body)
    assert count_overrides(api_database_url, data["outsider"]) == 1


def test_post_unknown_document_404(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    response = api_client.post(
        _url(data), params={"currency": "EUR"}, json={"document_id": 999999999}
    )
    assert response.status_code == 404


def test_post_unknown_series_404(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    response = api_client.post(
        f"/api/series/999999/{data['kind_id']}/members",
        params={"currency": "EUR"},
        json={"document_id": data["member"]},
    )
    assert response.status_code == 404


def test_membership_requires_authentication(anon_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    assert (
        anon_client.post(
            _url(data), params={"currency": "EUR"}, json={"document_id": data["member"]}
        ).status_code
        == 401
    )


def test_null_currency_series(api_client: TestClient, api_database_url: str) -> None:
    data = seed(api_database_url, uuid.uuid4().hex[:8])
    response = api_client.post(_url(data), json={"document_id": data["outsider"]})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pinned"
    assert count_overrides(api_database_url, data["outsider"]) == 1
