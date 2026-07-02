"""FX-rate seeding: direct service unit tests + admin API integration (W1).

Seeding upserts a single ``fx_rates`` row so cross-currency conversion resolves.
Direct-session unit tests run on the main thread (traced for coverage); the API
tests exercise routing, validation, gating, and the live/manual source split
(the live provider is mocked — no network in tests).
"""

import asyncio
import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import Settings
from library.fx_admin import list_fx_status, seed_fx_rate, seed_fx_rate_live
from library.models import Document, DocumentSource, DocumentStatus, FxRate, Kind, Sender

pytestmark = pytest.mark.integration


def _run(url: str, work: Any) -> Any:
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


async def _add_document(session: AsyncSession, marker: str, currency: str) -> int:
    sender = Sender(name=f"FX Sender {marker}")
    kind = Kind(slug=f"fx-kind-{marker}", name=f"FX Kind {marker}")
    session.add_all([sender, kind])
    await session.flush()
    doc = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        status=DocumentStatus.INDEXED,
        sender_id=sender.id,
        kind_id=kind.id,
        currency=currency,
        amount_total=10,
    )
    session.add(doc)
    await session.flush()
    return doc.id


def _mock_client(code: str, rate: float) -> httpx.AsyncClient:
    """A provider that lists ``code`` at ``rate`` (units of code per 1 USD)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "success", "rates": {"USD": 1.0, code: rate}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------- direct service units


def test_seed_fx_rate_inserts_then_upserts(api_database_url: str) -> None:
    async def work(session: AsyncSession) -> None:
        first = await seed_fx_rate(session, "xea", Decimal("1.25"), date(2026, 1, 1))
        assert first.status == "done"
        assert first.currency == "XEA"
        assert first.rate_to_base == Decimal("1.25")
        # Re-seeding the same (currency, as_of) updates in place, not a duplicate.
        second = await seed_fx_rate(session, "XEA", Decimal("1.30"), date(2026, 1, 1))
        assert second.status == "done"

    _run(api_database_url, work)

    async def check(session: AsyncSession) -> list[FxRate]:
        return list(
            (await session.execute(select(FxRate).where(FxRate.currency == "XEA"))).scalars().all()
        )

    rows = _run(api_database_url, check)
    assert len(rows) == 1
    assert rows[0].rate_to_base == Decimal("1.30")


def test_seed_fx_rate_defaults_as_of_to_today(api_database_url: str) -> None:
    async def work(session: AsyncSession) -> Any:
        return await seed_fx_rate(session, "XEB", Decimal("2"))

    result = _run(api_database_url, work)
    assert result.as_of == date.today()


def test_seed_fx_rate_rejects_usd_and_bad_code(api_database_url: str) -> None:
    async def work(session: AsyncSession) -> tuple[str, str]:
        base = await seed_fx_rate(session, "usd", Decimal("1"))
        bad = await seed_fx_rate(session, "EURO", Decimal("1"))
        return base.status, bad.status

    base_status, bad_status = _run(api_database_url, work)
    assert base_status == "is_base"
    assert bad_status == "invalid_code"


def test_list_fx_status_reports_base_seeded_and_missing(api_database_url: str) -> None:
    async def seed(session: AsyncSession) -> None:
        await _add_document(session, "fx-usd", "USD")
        await _add_document(session, "fx-seeded", "XEC")
        await _add_document(session, "fx-missing", "XED")
        session.add(FxRate(currency="XEC", as_of=date(2025, 1, 1), rate_to_base=Decimal("1.1")))
        session.add(FxRate(currency="XEC", as_of=date(2026, 1, 1), rate_to_base=Decimal("1.4")))

    _run(api_database_url, seed)

    async def work(session: AsyncSession) -> Any:
        return await list_fx_status(session)

    statuses = {s.code: s for s in _run(api_database_url, work)}
    assert statuses["USD"].is_base is True
    assert statuses["USD"].has_rate is True
    assert statuses["XEC"].has_rate is True
    assert statuses["XEC"].as_of == date(2026, 1, 1)  # latest row wins
    assert statuses["XEC"].rate_to_base == Decimal("1.4")
    assert statuses["XED"].has_rate is False
    assert statuses["XED"].rate_to_base is None


def test_seed_fx_rate_live_success_and_unsupported(api_database_url: str) -> None:
    async def work(session: AsyncSession) -> tuple[Any, Any]:
        ok = await seed_fx_rate_live(
            session, "XEE", settings=Settings(), client=_mock_client("XEE", 0.5)
        )

        def missing(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": "success", "rates": {"USD": 1.0}})

        unsupported = await seed_fx_rate_live(
            session,
            "XEF",
            settings=Settings(),
            client=httpx.AsyncClient(transport=httpx.MockTransport(missing)),
        )
        return ok, unsupported

    ok, unsupported = _run(api_database_url, work)
    assert ok.status == "done"
    assert ok.rate_to_base == Decimal("2.00000000")  # 1/0.5
    assert unsupported.status == "unsupported"


# ------------------------------------------------------------ admin API routes


def test_list_fx_rates_route(admin_client: TestClient, api_database_url: str) -> None:
    async def seed(session: AsyncSession) -> None:
        await _add_document(session, "fx-api-seeded", "XGA")
        await _add_document(session, "fx-api-missing", "XGB")
        session.add(FxRate(currency="XGA", as_of=date(2026, 1, 1), rate_to_base=Decimal("1.2")))

    _run(api_database_url, seed)

    body = {row["code"]: row for row in admin_client.get("/api/admin/fx-rates").json()}
    assert body["XGA"]["has_rate"] is True
    assert body["XGB"]["has_rate"] is False


def test_seed_fx_manual(admin_client: TestClient, api_database_url: str) -> None:
    resp = admin_client.post(
        "/api/admin/fx-rates",
        json={"currency": "xha", "source": "manual", "rate_to_base": "1.5"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] == "XHA"
    assert Decimal(body["rate_to_base"]) == Decimal("1.5")
    assert body["as_of"] == date.today().isoformat()


def test_seed_fx_manual_requires_rate(admin_client: TestClient) -> None:
    resp = admin_client.post("/api/admin/fx-rates", json={"currency": "XHB", "source": "manual"})
    assert resp.status_code == 422, resp.text


def test_seed_fx_rejects_usd_and_bad_code(admin_client: TestClient) -> None:
    assert (
        admin_client.post(
            "/api/admin/fx-rates",
            json={"currency": "USD", "source": "manual", "rate_to_base": "1"},
        ).status_code
        == 422
    )
    assert (
        admin_client.post(
            "/api/admin/fx-rates",
            json={"currency": "EURO", "source": "manual", "rate_to_base": "1"},
        ).status_code
        == 422
    )


def test_seed_fx_live(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(currency: str, *, settings: Any, client: Any = None) -> Decimal:
        return Decimal("0.90000000")

    monkeypatch.setattr("library.fx_admin.fetch_rate_to_base", fake_fetch)
    resp = admin_client.post("/api/admin/fx-rates", json={"currency": "XHC", "source": "live"})
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["rate_to_base"]) == Decimal("0.9")


def test_seed_fx_live_unsupported_returns_502(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(currency: str, *, settings: Any, client: Any = None) -> None:
        return None

    monkeypatch.setattr("library.fx_admin.fetch_rate_to_base", fake_fetch)
    resp = admin_client.post("/api/admin/fx-rates", json={"currency": "XHD", "source": "live"})
    assert resp.status_code == 502, resp.text


def test_seed_fx_live_provider_error_returns_502(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from library.fx_api import FxApiError

    async def fake_fetch(currency: str, *, settings: Any, client: Any = None) -> Decimal:
        raise FxApiError("boom")

    monkeypatch.setattr("library.fx_admin.fetch_rate_to_base", fake_fetch)
    resp = admin_client.post("/api/admin/fx-rates", json={"currency": "XHE", "source": "live"})
    assert resp.status_code == 502, resp.text


def test_fx_routes_reject_non_admin_and_anon(
    api_client: TestClient, anon_client: TestClient
) -> None:
    assert api_client.get("/api/admin/fx-rates").status_code == 403
    assert (
        api_client.post(
            "/api/admin/fx-rates",
            json={"currency": "EUR", "source": "manual", "rate_to_base": "1"},
        ).status_code
        == 403
    )
    assert anon_client.get("/api/admin/fx-rates").status_code == 401
