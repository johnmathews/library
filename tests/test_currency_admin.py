"""Currency normalisation: unit + series-aware admin integration (W5).

Currency is part of series identity, so a rename must rewrite documents,
authored series/suggestions, the series-insight cache, and the two override
tables — while refusing on a user-override collision and leaving fx_rates
untouched. These tests seed real rows across those tables and assert the policy.
"""

import asyncio
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.currencies import normalize_currency_code
from library.models import (
    AuthoredSeries,
    Document,
    DocumentSource,
    DocumentStatus,
    FxRate,
    Kind,
    Sender,
    SeriesInsight,
    SeriesMetaOverride,
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


# ------------------------------------------------ series-aware rename


def test_normalize_series_aware_success(admin_client: TestClient, api_database_url: str) -> None:
    codes = {}

    async def seed(session: AsyncSession) -> None:
        s, k = await _new_sender_kind(session, "ok")
        s2, _ = await _new_sender_kind(session, "ok2")
        codes["s"], codes["k"], codes["s2"] = s, k, s2
        d1 = await _add_document(session, "w5-ok-1", sender_id=s, kind_id=k, currency="XAA")
        codes["doc"] = d1
        await _add_document(session, "w5-ok-2", sender_id=s, kind_id=k, currency="XAA")
        # Colliding insight pair on (s, k): XAA gets merged into the surviving XBB.
        session.add_all(
            [
                SeriesInsight(sender_id=s, kind_id=k, currency="XAA", description="old", model="m"),
                SeriesInsight(
                    sender_id=s, kind_id=k, currency="XBB", description="keep", model="m"
                ),
                # Non-colliding insight on a different sender -> plain update to XBB.
                SeriesInsight(
                    sender_id=s2, kind_id=k, currency="XAA", description="move", model="m"
                ),
                AuthoredSeries(name="W5 Authored", currency="XAA"),
            ]
        )

    _run(api_database_url, seed)

    # Seed an authored-series suggestion carrying the signature currency.
    async def seed_suggestion(session: AsyncSession) -> None:
        series = (
            await session.execute(
                select(AuthoredSeries).where(AuthoredSeries.name == "W5 Authored")
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO authored_series_suggestions "
                "(authored_series_id, document_id, state, signature_currency) "
                "VALUES (:sid, :did, 'pending', 'XAA')"
            ),
            {"sid": series.id, "did": codes["doc"]},
        )

    _run(api_database_url, seed_suggestion)

    resp = admin_client.post(
        "/api/admin/currencies/normalize", json={"from_code": "xaa", "to_code": "xbb"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_code"] == "XAA" and body["to_code"] == "XBB"
    assert body["counts"]["documents"] == 2
    assert body["counts"]["authored_series"] == 1
    assert body["counts"]["authored_series_suggestions"] == 1
    assert body["counts"]["series_insights_merged"] == 1  # (s,k,XAA) dropped
    assert body["counts"]["series_insights"] == 1  # (s2,k,XAA) moved to XBB
    # XBB has no fx rate seeded -> the operation warns.
    assert body["fx_rate_missing"] is True

    # Documents renamed; XAA no longer present.
    listing = {
        row["code"]: row["document_count"]
        for row in admin_client.get("/api/admin/currencies").json()
    }
    assert "XAA" not in listing
    assert listing.get("XBB", 0) >= 2

    async def check(session: AsyncSession) -> dict[str, Any]:
        insights = (
            (
                await session.execute(
                    select(SeriesInsight).where(
                        SeriesInsight.sender_id.in_([codes["s"], codes["s2"]])
                    )
                )
            )
            .scalars()
            .all()
        )
        return {(i.sender_id, i.currency): i.description for i in insights}

    insights = _run(api_database_url, check)
    # Survivor kept, XAA bucket gone, other sender moved to XBB.
    assert insights.get((codes["s"], "XBB")) == "keep"
    assert (codes["s"], "XAA") not in insights
    assert insights.get((codes["s2"], "XBB")) == "move"


def test_normalize_refuses_on_override_collision(
    admin_client: TestClient, api_database_url: str
) -> None:
    ids = {}

    async def seed(session: AsyncSession) -> None:
        s, k = await _new_sender_kind(session, "conf")
        ids["doc"] = await _add_document(session, "w5-conf", sender_id=s, kind_id=k, currency="XCA")
        # A user meta-override on both the source and the target series identity:
        # renaming XCA -> XCB would collide, so the whole op must be refused.
        session.add_all(
            [
                SeriesMetaOverride(sender_id=s, kind_id=k, currency="XCA", title="mine-src"),
                SeriesMetaOverride(sender_id=s, kind_id=k, currency="XCB", title="mine-tgt"),
            ]
        )

    _run(api_database_url, seed)

    resp = admin_client.post(
        "/api/admin/currencies/normalize", json={"from_code": "XCA", "to_code": "XCB"}
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["conflicts"], body
    assert any(c["table"] == "series_meta_overrides" for c in body["conflicts"])

    # Nothing was mutated: the document still carries XCA, both overrides survive.
    async def check(session: AsyncSession) -> tuple[str | None, int]:
        doc = await session.get(Document, ids["doc"])
        overrides = (
            (
                await session.execute(
                    select(SeriesMetaOverride).where(
                        SeriesMetaOverride.currency.in_(["XCA", "XCB"])
                    )
                )
            )
            .scalars()
            .all()
        )
        return (doc.currency if doc else None), len(overrides)

    currency, override_count = _run(api_database_url, check)
    assert currency == "XCA"
    assert override_count == 2


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
