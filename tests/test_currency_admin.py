"""Currency normalisation: unit + admin integration (W5).

Currency is a free-text field on ``documents``, so a rename is a plain
document rewrite that leaves ``fx_rates`` untouched. These tests seed real
document rows and assert the policy.
"""

import asyncio
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.currencies import normalize_currency_code
from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    FxRate,
    Kind,
    Sender,
)

pytestmark = pytest.mark.integration


def _run(url: str, work: Any) -> Any:
    """Open a session against ``url`` and run ``work(session)`` to completion."""

    async def _main() -> Any:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(_main())


async def _new_sender_kind(session: AsyncSession, marker: str) -> tuple[int, int]:
    sender = Sender(name=f"W5 Sender {marker}")
    kind = Kind(slug=f"w5-kind-{marker}", name=f"W5 Kind {marker}")
    session.add_all([sender, kind])
    await session.flush()
    return sender.id, kind.id


async def _add_document(
    session: AsyncSession, marker: str, *, sender_id: int, kind_id: int, currency: str
) -> int:
    import hashlib

    doc = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        status=DocumentStatus.INDEXED,
        sender_id=sender_id,
        kind_id=kind_id,
        currency=currency,
        amount_total=10,
    )
    session.add(doc)
    await session.flush()
    return doc.id


# --------------------------------------------------------------- unit


def test_normalize_currency_code_accepts_and_rejects() -> None:
    assert normalize_currency_code("eur") == "EUR"
    assert normalize_currency_code("  Usd ") == "USD"
    assert normalize_currency_code("EURO") is None  # 4 letters
    assert normalize_currency_code("E1R") is None  # digit
    assert normalize_currency_code("") is None


# ---------------------------------------------------- list currencies


def test_list_currencies_in_use(admin_client: TestClient, api_database_url: str) -> None:
    async def seed(session: AsyncSession) -> None:
        s, k = await _new_sender_kind(session, "list")
        await _add_document(session, "w5-list-1", sender_id=s, kind_id=k, currency="AUD")
        await _add_document(session, "w5-list-2", sender_id=s, kind_id=k, currency="AUD")
        await _add_document(session, "w5-list-3", sender_id=s, kind_id=k, currency="NZD")

    _run(api_database_url, seed)

    body = admin_client.get("/api/admin/currencies").json()
    counts = {row["code"]: row["document_count"] for row in body}
    assert counts.get("AUD") == 2
    assert counts.get("NZD") == 1


# --------------------------------------------------------- rename


def test_normalize_success(admin_client: TestClient, api_database_url: str) -> None:
    async def seed(session: AsyncSession) -> None:
        s, k = await _new_sender_kind(session, "ok")
        await _add_document(session, "w5-ok-1", sender_id=s, kind_id=k, currency="XAA")
        await _add_document(session, "w5-ok-2", sender_id=s, kind_id=k, currency="XAA")

    _run(api_database_url, seed)

    resp = admin_client.post(
        "/api/admin/currencies/normalize", json={"from_code": "xaa", "to_code": "xbb"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_code"] == "XAA" and body["to_code"] == "XBB"
    assert body["counts"]["documents"] == 2
    # XBB has no fx rate seeded -> the operation warns.
    assert body["fx_rate_missing"] is True

    # Documents renamed; XAA no longer present.
    listing = {
        row["code"]: row["document_count"]
        for row in admin_client.get("/api/admin/currencies").json()
    }
    assert "XAA" not in listing
    assert listing.get("XBB", 0) >= 2


def test_normalize_fx_rate_present_no_warning(
    admin_client: TestClient, api_database_url: str
) -> None:
    async def seed(session: AsyncSession) -> None:
        s, k = await _new_sender_kind(session, "fx")
        await _add_document(session, "w5-fx", sender_id=s, kind_id=k, currency="XFA")
        session.add(FxRate(currency="XFB", as_of=date(2026, 1, 1), rate_to_base=1))

    _run(api_database_url, seed)

    resp = admin_client.post(
        "/api/admin/currencies/normalize", json={"from_code": "XFA", "to_code": "XFB"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fx_rate_missing"] is False


# --------------------------------------------------- validation + gating


def test_normalize_validation_and_same_code(admin_client: TestClient) -> None:
    assert (
        admin_client.post(
            "/api/admin/currencies/normalize", json={"from_code": "aa", "to_code": "EUR"}
        ).status_code
        == 422
    )
    assert (
        admin_client.post(
            "/api/admin/currencies/normalize", json={"from_code": "EUR", "to_code": "12x"}
        ).status_code
        == 422
    )
    # Same code after normalising (case-insensitive) is a 400.
    assert (
        admin_client.post(
            "/api/admin/currencies/normalize", json={"from_code": "eur", "to_code": "EUR"}
        ).status_code
        == 400
    )


def test_currency_routes_reject_non_admin_and_anon(
    api_client: TestClient, anon_client: TestClient
) -> None:
    assert api_client.get("/api/admin/currencies").status_code == 403
    assert (
        api_client.post(
            "/api/admin/currencies/normalize", json={"from_code": "EUR", "to_code": "USD"}
        ).status_code
        == 403
    )
    assert anon_client.get("/api/admin/currencies").status_code == 401
