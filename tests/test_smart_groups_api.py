"""Integration tests for Smart Groups (semantic authored series) create-with-backfill —
``POST /api/charts/authored`` with ``mode=semantic`` (W14 follow-on)."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import Settings
from library.models import (
    EMBEDDING_DIM,
    AuthoredSeries,
    AuthoredSeriesExclusion,
    AuthoredSeriesMember,
    Document,
    DocumentChunk,
    DocumentSource,
    MemberOrigin,
    SeriesMode,
)
from library.semantic_membership import auto_add_document

pytestmark = pytest.mark.integration


async def _make_document_with_chunk(
    database_url: str,
    title: str,
    vec: list[float],
    *,
    amount_total: str | None = None,
    currency: str | None = None,
) -> int:
    """A document with one chunk embedding (padded to EMBEDDING_DIM); returns its id."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            marker = f"smart-group:{title}:{uuid.uuid4()}"
            document = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                original_filename=title,
                title=title,
                amount_total=Decimal(amount_total) if amount_total is not None else None,
                currency=currency,
            )
            session.add(document)
            await session.flush()
            padded = list(vec) + [0.0] * (EMBEDDING_DIM - len(vec))
            session.add(
                DocumentChunk(document_id=document.id, chunk_index=1, text=title, embedding=padded)
            )
            await session.commit()
            return document.id
    finally:
        await engine.dispose()


def make_document_with_chunk(
    database_url: str,
    title: str,
    vec: list[float],
    *,
    amount_total: str | None = None,
    currency: str | None = None,
) -> int:
    return asyncio.run(
        _make_document_with_chunk(
            database_url, title, vec, amount_total=amount_total, currency=currency
        )
    )


def test_create_semantic_group_stages_backfill(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    seed_id = make_document_with_chunk(api_database_url, f"acc-seed-{tag}", [0.9, 0.1])
    match_id = make_document_with_chunk(
        api_database_url, f"acc-match-{tag}", [0.88, 0.12], amount_total="120.00", currency="EUR"
    )
    noise_id = make_document_with_chunk(
        api_database_url, f"acc-noise-{tag}", [0.0, 1.0], amount_total="5.00", currency="EUR"
    )

    resp = api_client.post(
        "/api/charts/authored",
        json={
            "name": f"my accountant {tag}",
            "currency": "EUR",
            "mode": "semantic",
            "seed_document_ids": [seed_id],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    backfill_ids = {row["document_id"] for row in body["backfill"]}
    assert match_id in backfill_ids
    assert noise_id not in backfill_ids


def test_create_manual_group_has_no_backfill_key(
    api_client: TestClient, api_database_url: str
) -> None:
    """Default (manual) mode never runs the semantic sweep."""
    resp = api_client.post("/api/charts/authored", json={"name": f"manual-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    assert "backfill" not in resp.json()


def test_create_semantic_group_seed_members_are_manual_origin(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    seed_id = make_document_with_chunk(api_database_url, f"origin-seed-{tag}", [0.9, 0.1])

    resp = api_client.post(
        "/api/charts/authored",
        json={
            "name": f"origin group {tag}",
            "currency": "EUR",
            "mode": "semantic",
            "seed_document_ids": [seed_id],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["count"] == 0  # seed doc has no amount_total, so it doesn't chart as a point
    assert body["authored_id"] is not None


async def _make_semantic_group_with_auto_member(
    database_url: str, name: str, *, amount_total: str, currency: str
) -> tuple[int, int]:
    """A ``mode=semantic`` authored series with one amount-bearing, AUTO-origin
    member; returns ``(group_id, document_id)``."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            group = AuthoredSeries(name=name, currency=currency, mode=SeriesMode.SEMANTIC)
            session.add(group)
            await session.flush()
            marker = f"smart-group-auto:{name}:{uuid.uuid4()}"
            document = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                original_filename=name,
                title=name,
                amount_total=Decimal(amount_total),
                currency=currency,
            )
            session.add(document)
            await session.flush()
            session.add(
                AuthoredSeriesMember(
                    authored_series_id=group.id,
                    document_id=document.id,
                    origin=MemberOrigin.AUTO,
                )
            )
            await session.commit()
            return group.id, document.id
    finally:
        await engine.dispose()


def test_authored_body_exposes_mode_and_auto_count(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    group_id, _document_id = asyncio.run(
        _make_semantic_group_with_auto_member(
            api_database_url, f"ev-body-uniqtag-{tag}", amount_total="12.00", currency="EUR"
        )
    )

    resp = api_client.get(f"/api/charts/a-{group_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["auto_added_count"] == 1


async def _add_soft_deleted_auto_member(
    database_url: str, group_id: int, *, amount_total: str, currency: str
) -> int:
    """A second AUTO-origin member on ``group_id`` whose document is soft-deleted
    (``deleted_at`` set, row not yet purged); returns the document id."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            marker = f"smart-group-auto-deleted:{group_id}:{uuid.uuid4()}"
            document = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                original_filename="deleted-auto-member",
                title="deleted-auto-member",
                amount_total=Decimal(amount_total),
                currency=currency,
                deleted_at=datetime.now(UTC),
            )
            session.add(document)
            await session.flush()
            session.add(
                AuthoredSeriesMember(
                    authored_series_id=group_id,
                    document_id=document.id,
                    origin=MemberOrigin.AUTO,
                )
            )
            await session.commit()
            return document.id
    finally:
        await engine.dispose()


def test_soft_deleted_auto_member_not_counted(
    api_client: TestClient, api_database_url: str
) -> None:
    """A soft-deleted (not-yet-purged) AUTO member must not inflate
    ``auto_added_count`` — it disagrees with the chart, which already filters
    ``deleted_at`` via ``_load_authored_members``."""
    tag = uuid.uuid4().hex[:8]
    group_id, _document_id = asyncio.run(
        _make_semantic_group_with_auto_member(
            api_database_url, f"ev-soft-delete-{tag}", amount_total="12.00", currency="EUR"
        )
    )
    asyncio.run(
        _add_soft_deleted_auto_member(
            api_database_url, group_id, amount_total="9.00", currency="EUR"
        )
    )

    resp = api_client.get(f"/api/charts/a-{group_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["auto_added_count"] == 1  # only the non-deleted AUTO member counts


def test_pruned_member_is_not_re_added(api_client: TestClient, api_database_url: str) -> None:
    """Removing a member writes an exclusion (a veto), so a later semantic sweep
    does not silently re-add the pruned document."""
    tag = uuid.uuid4().hex[:8]
    seed_id = make_document_with_chunk(api_database_url, f"ev-prune-seed-{tag}", [0.9, 0.1])
    wrong_id = make_document_with_chunk(
        api_database_url,
        f"ev-prune-wrong-{tag}",
        [0.88, 0.12],
        amount_total="9.00",
        currency="EUR",
    )

    created = api_client.post(
        "/api/charts/authored",
        json={
            "name": f"ev-prune-group-{tag}",
            "currency": "EUR",
            "mode": "semantic",
            "seed_document_ids": [seed_id],
        },
    ).json()
    authored_id = created["authored_id"]
    api_client.post(f"/api/charts/authored/{authored_id}/members", json={"document_id": wrong_id})

    resp = api_client.delete(f"/api/charts/authored/{authored_id}/members/{wrong_id}")
    assert resp.status_code == 200, resp.text

    async def _check() -> None:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                excl = (
                    await session.execute(
                        select(AuthoredSeriesExclusion).where(
                            AuthoredSeriesExclusion.authored_series_id == authored_id,
                            AuthoredSeriesExclusion.document_id == wrong_id,
                        )
                    )
                ).scalar_one_or_none()
                assert excl is not None
                # A fresh auto-add attempt must respect the veto:
                joined = await auto_add_document(session, Settings(), wrong_id)
                assert authored_id not in joined
        finally:
            await engine.dispose()

    asyncio.run(_check())


def test_dismiss_clears_existing_membership_and_blocks_reautoadd(
    api_client: TestClient, api_database_url: str
) -> None:
    """Dismiss must be authoritative: a document that became an AUTO member
    (e.g. via a background auto-add pass) between staging and review, then
    dismissed via the suggestion-review UI, must end up NOT a member, WITH an
    exclusion recorded — never both a member and excluded. A later auto-add
    attempt must also respect the veto."""
    tag = uuid.uuid4().hex[:8]
    seed_id = make_document_with_chunk(api_database_url, f"ev-dismiss-seed-{tag}", [0.9, 0.1])
    auto_id = make_document_with_chunk(
        api_database_url,
        f"ev-dismiss-auto-{tag}",
        [0.88, 0.12],
        amount_total="9.00",
        currency="EUR",
    )

    created = api_client.post(
        "/api/charts/authored",
        json={
            "name": f"ev-dismiss-group-{tag}",
            "currency": "EUR",
            "mode": "semantic",
            "seed_document_ids": [seed_id],
        },
    ).json()
    authored_id = created["authored_id"]

    async def _make_auto_member() -> None:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                session.add(
                    AuthoredSeriesMember(
                        authored_series_id=authored_id,
                        document_id=auto_id,
                        origin=MemberOrigin.AUTO,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_make_auto_member())

    resp = api_client.post(f"/api/charts/authored/{authored_id}/suggestions/{auto_id}/dismiss")
    assert resp.status_code == 200, resp.text

    async def _check() -> None:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                member = (
                    await session.execute(
                        select(AuthoredSeriesMember).where(
                            AuthoredSeriesMember.authored_series_id == authored_id,
                            AuthoredSeriesMember.document_id == auto_id,
                        )
                    )
                ).scalar_one_or_none()
                assert member is None  # dismiss must clear the AUTO member row

                excl = (
                    await session.execute(
                        select(AuthoredSeriesExclusion).where(
                            AuthoredSeriesExclusion.authored_series_id == authored_id,
                            AuthoredSeriesExclusion.document_id == auto_id,
                        )
                    )
                ).scalar_one_or_none()
                assert excl is not None

                joined = await auto_add_document(session, Settings(), auto_id)
                assert authored_id not in joined
        finally:
            await engine.dispose()

    asyncio.run(_check())
