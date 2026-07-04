"""Coverage-bearing tests for the charts write paths (W4).

The endpoints in ``library.api.charts`` are already exercised end-to-end by
``test_charts_api``/``test_authored_series_api``/``test_charts_suggestions_api``,
but those requests run inside ``TestClient``'s own event-loop thread, which the
coverage tracer does not follow — so the inline handler bodies (meta upsert,
authored-series create/update/delete, membership add/remove, suggestion
accept/dismiss, odd-ones-out) show as uncovered even though the HTTP tests pass.

These tests call the same handler functions **directly** in the main thread (the
``asyncio.run`` + NullPool ``AsyncSession`` pattern conftest and the W1 admin
service tests use), so the write logic is genuinely traced, and assert the
observable outcome: the override/series/membership/suggestion row that actually
landed in the database, not merely a 2xx.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.api import charts
from library.config import get_settings
from library.models import (
    AuthoredSeries,
    AuthoredSeriesMember,
    AuthoredSeriesSuggestion,
    Document,
    DocumentSource,
    DocumentStatus,
    Kind,
    Sender,
    SeriesMetaOverride,
    SuggestionState,
    User,
)
from tests.conftest import AuthUser

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- helpers


def run_op[T](database_url: str, op: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``op`` against the API test DB in the main thread on a NullPool session.

    Mirrors the app's per-request session (and conftest's seeding helpers) so a
    handler called here commits against the same database the other fixtures
    read from, and — crucially — runs in the test's own event loop where the
    coverage tracer can follow it.
    """

    async def _body() -> T:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await op(session)
        finally:
            await engine.dispose()

    return asyncio.run(_body())


async def _seed(
    session: AsyncSession, sender_name: str, kind_slug: str, count: int, *, currency: str = "EUR"
) -> tuple[int, int, list[int]]:
    """Seed ``count`` indexed, amount-bearing docs; return (sender_id, kind_id, ids)."""
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
        marker = f"charts-write:{sender_name}:{kind_slug}:{n}:{uuid.uuid4()}"
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
    return sender.id, kind.id, ids


def seed(
    url: str, sender: str, kind_slug: str, count: int, *, currency: str = "EUR"
) -> tuple[int, int, list[int]]:
    return run_op(url, lambda s: _seed(s, sender, kind_slug, count, currency=currency))


async def _member_ids(session: AsyncSession, authored_id: int) -> set[int]:
    rows = (
        await session.execute(
            select(AuthoredSeriesMember.document_id).where(
                AuthoredSeriesMember.authored_series_id == authored_id
            )
        )
    ).scalars()
    return set(rows)


def member_ids(url: str, authored_id: int) -> set[int]:
    return run_op(url, lambda s: _member_ids(s, authored_id))


async def _create_series(
    session: AsyncSession, owner_id: int, name: str, currency: str | None, ids: list[int]
) -> int:
    """Create an authored series via the handler and return its id."""
    user = await session.get(User, owner_id)
    assert user is not None
    payload = charts.AuthoredSeriesCreate(name=name, currency=currency, document_ids=ids)
    body = await charts.create_authored_series(payload, user, session, get_settings())
    return int(body["authored_id"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- meta upsert


def test_update_chart_meta_persists_override_and_returns_summary(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    sender_id, kind_id, _ = seed(api_database_url, f"MetaCo-{tag}", "utility-bill", 3)
    series_id = f"{sender_id}-{kind_id}-EUR"

    payload = charts.SeriesMetaRequest(title="Flat energy", description="Switched tariff.")
    body = run_op(
        api_database_url,
        lambda s: charts.update_chart_meta(series_id, payload, s, get_settings()),
    )
    # Chartable series -> full summary body reflecting the override.
    assert body["title"] == "Flat energy"
    assert body["description"] == "Switched tariff."

    # The override row actually persisted, keyed by (sender, kind, currency).
    stored = run_op(
        api_database_url,
        lambda s: s.execute(
            select(SeriesMetaOverride).where(
                SeriesMetaOverride.sender_id == sender_id,
                SeriesMetaOverride.kind_id == kind_id,
                SeriesMetaOverride.currency == "EUR",
            )
        ),
    ).scalar_one()
    assert stored.title == "Flat energy"
    assert stored.description == "Switched tariff."


def test_update_chart_meta_partial_update_keeps_other_field(
    api_app: object, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    sender_id, kind_id, _ = seed(api_database_url, f"MetaPart-{tag}", "utility-bill", 3)
    series_id = f"{sender_id}-{kind_id}-EUR"

    run_op(
        api_database_url,
        lambda s: charts.update_chart_meta(
            series_id, charts.SeriesMetaRequest(title="Keep me"), s, get_settings()
        ),
    )
    # A later request that only sets description must not wipe the stored title.
    body = run_op(
        api_database_url,
        lambda s: charts.update_chart_meta(
            series_id, charts.SeriesMetaRequest(description="Only desc"), s, get_settings()
        ),
    )
    assert body["title"] == "Keep me"
    assert body["description"] == "Only desc"


def test_update_chart_meta_sparse_series_echoes_stored_meta(
    api_app: object, api_database_url: str
) -> None:
    """The sender/kind exist but the series is too sparse to chart: the override
    still persists and the handler echoes the stored meta (the non-summary path)."""
    tag = uuid.uuid4().hex[:8]
    # One doc: the (sender, kind) pair is real, but below series_min_documents.
    sender_id, kind_id, _ = seed(api_database_url, f"MetaSparse-{tag}", "invoice", 1)
    series_id = f"{sender_id}-{kind_id}-EUR"

    payload = charts.SeriesMetaRequest(title="Sparse title", description="Sparse desc")
    body = run_op(
        api_database_url,
        lambda s: charts.update_chart_meta(series_id, payload, s, get_settings()),
    )
    # Echo shape (no summary points), proving the too-sparse branch ran.
    assert body == {
        "sender_id": sender_id,
        "kind_id": kind_id,
        "currency": "EUR",
        "title": "Sparse title",
        "description": "Sparse desc",
    }
    assert member_ids  # sanity: helper imported


def test_update_chart_meta_unknown_series_raises_404(
    api_app: object, api_database_url: str
) -> None:
    # Well-formed id but no such sender/kind.
    with pytest.raises(HTTPException) as unknown:
        run_op(
            api_database_url,
            lambda s: charts.update_chart_meta(
                "999999-999999-EUR", charts.SeriesMetaRequest(title="x"), s, get_settings()
            ),
        )
    assert unknown.value.status_code == 404
    # Malformed id -> _decode_or_404.
    with pytest.raises(HTTPException) as malformed:
        run_op(
            api_database_url,
            lambda s: charts.update_chart_meta(
                "not-a-series", charts.SeriesMetaRequest(title="x"), s, get_settings()
            ),
        )
    assert malformed.value.status_code == 404


# ----------------------------------------------------- authored series CRUD


def test_create_authored_series_persists_members_and_filters_bogus_ids(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"CreateCo-{tag}", "utility-bill", 3)

    # A bogus id in the seed list must be silently dropped by _existing_document_ids.
    async def _create(session: AsyncSession) -> dict[str, object]:
        user = await session.get(User, auth_user.id)
        assert user is not None
        payload = charts.AuthoredSeriesCreate(
            name="  My energy  ", currency="EUR", document_ids=[*ids, 999999999]
        )
        return await charts.create_authored_series(payload, user, session, get_settings())

    body = run_op(api_database_url, _create)
    aid = int(body["authored_id"])  # type: ignore[arg-type]
    assert body["title"] == "My energy"  # name stripped
    assert body["count"] == 3

    # Exactly the three real documents became members; the bogus id did not.
    assert member_ids(api_database_url, aid) == set(ids)
    # And the AuthoredSeries row records the creating user as owner (provenance).
    row = run_op(api_database_url, lambda s: s.get(AuthoredSeries, aid))
    assert row is not None and row.owner_id == auth_user.id and row.currency == "EUR"


def test_update_authored_series_persists_name_and_description(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "Old name", None, []))

    body = run_op(
        api_database_url,
        lambda s: charts.update_authored_series(
            aid,
            charts.AuthoredSeriesUpdate(name="  New name  ", description="new desc"),
            s,
            get_settings(),
        ),
    )
    assert body["title"] == "New name"  # stripped
    assert body["description"] == "new desc"

    # Partial update leaves name intact while changing description.
    run_op(
        api_database_url,
        lambda s: charts.update_authored_series(
            aid, charts.AuthoredSeriesUpdate(description="only desc"), s, get_settings()
        ),
    )
    row = run_op(api_database_url, lambda s: s.get(AuthoredSeries, aid))
    assert row is not None
    assert row.name == "New name"
    assert row.description == "only desc"


def test_update_authored_series_unknown_raises_404(api_app: object, api_database_url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        run_op(
            api_database_url,
            lambda s: charts.update_authored_series(
                999999999, charts.AuthoredSeriesUpdate(name="x"), s, get_settings()
            ),
        )
    assert exc.value.status_code == 404


def test_delete_authored_series_removes_row_and_members(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"DeleteCo-{tag}", "utility-bill", 3)
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "Doomed", "EUR", ids))
    assert member_ids(api_database_url, aid) == set(ids)

    run_op(api_database_url, lambda s: charts.delete_authored_series(aid, s))

    # The series row is gone and its membership cascaded away.
    assert run_op(api_database_url, lambda s: s.get(AuthoredSeries, aid)) is None
    assert member_ids(api_database_url, aid) == set()

    # Deleting again 404s (series no longer exists).
    with pytest.raises(HTTPException) as exc:
        run_op(api_database_url, lambda s: charts.delete_authored_series(aid, s))
    assert exc.value.status_code == 404


# --------------------------------------------------- authored membership


def test_add_authored_member_persists_and_is_idempotent(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"AddCo-{tag}", "utility-bill", 2)
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "Add", "EUR", []))

    run_op(
        api_database_url,
        lambda s: charts.add_authored_member(
            aid, charts.AuthoredMemberRequest(document_id=ids[0]), s, get_settings()
        ),
    )
    run_op(
        api_database_url,
        lambda s: charts.add_authored_member(
            aid, charts.AuthoredMemberRequest(document_id=ids[1]), s, get_settings()
        ),
    )
    # Re-adding an existing member is a no-op (idempotent).
    run_op(
        api_database_url,
        lambda s: charts.add_authored_member(
            aid, charts.AuthoredMemberRequest(document_id=ids[1]), s, get_settings()
        ),
    )
    assert member_ids(api_database_url, aid) == {ids[0], ids[1]}


def test_add_authored_member_unknown_document_raises_404(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    aid = run_op(
        api_database_url, lambda s: _create_series(s, auth_user.id, "AddUnknown", "EUR", [])
    )
    with pytest.raises(HTTPException) as exc:
        run_op(
            api_database_url,
            lambda s: charts.add_authored_member(
                aid, charts.AuthoredMemberRequest(document_id=999999999), s, get_settings()
            ),
        )
    assert exc.value.status_code == 404


def test_remove_authored_member_persists_and_is_idempotent(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"RemoveCo-{tag}", "utility-bill", 2)
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "Remove", "EUR", ids))
    assert member_ids(api_database_url, aid) == set(ids)

    run_op(
        api_database_url,
        lambda s: charts.remove_authored_member(aid, ids[0], s, get_settings()),
    )
    assert member_ids(api_database_url, aid) == {ids[1]}

    # Removing a non-member is a no-op (idempotent, still returns the body).
    run_op(
        api_database_url,
        lambda s: charts.remove_authored_member(aid, ids[0], s, get_settings()),
    )
    assert member_ids(api_database_url, aid) == {ids[1]}


# ----------------------------------------------- signature / suggestions


def test_get_authored_signature_null_then_populated(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "Sig", "EUR", []))
    # Memberless series -> null-signature dict.
    empty = run_op(api_database_url, lambda s: charts.get_authored_signature(aid, s))
    assert empty == {
        "sender_id": None,
        "kind_id": None,
        "currency": None,
        "member_count": 0,
        "dominant_count": 0,
        "dominance": 0.0,
    }

    tag = uuid.uuid4().hex[:8]
    sender_id, kind_id, ids = seed(api_database_url, f"SigCo-{tag}", "utility-bill", 3)
    run_op(
        api_database_url,
        lambda s: charts.add_authored_member(
            aid, charts.AuthoredMemberRequest(document_id=ids[0]), s, get_settings()
        ),
    )
    run_op(
        api_database_url,
        lambda s: charts.add_authored_member(
            aid, charts.AuthoredMemberRequest(document_id=ids[1]), s, get_settings()
        ),
    )
    sig = run_op(api_database_url, lambda s: charts.get_authored_signature(aid, s))
    assert sig["sender_id"] == sender_id
    assert sig["kind_id"] == kind_id
    assert sig["currency"] == "EUR"
    assert sig["member_count"] == 2
    assert sig["dominance"] == 1.0


def test_list_authored_suggestions_returns_signature_matches(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"SuggestCo-{tag}", "utility-bill", 4)
    members, candidate = ids[:3], ids[3]
    aid = run_op(
        api_database_url, lambda s: _create_series(s, auth_user.id, "Suggest", "EUR", members)
    )

    body = run_op(
        api_database_url, lambda s: charts.list_authored_suggestions(aid, s, get_settings())
    )
    assert body["count"] == 1
    assert body["suggestions"][0]["id"] == candidate  # type: ignore[index]


def test_accept_authored_suggestion_promotes_and_clears(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"AcceptCo-{tag}", "utility-bill", 4)
    members, candidate = ids[:3], ids[3]
    aid = run_op(
        api_database_url, lambda s: _create_series(s, auth_user.id, "Accept", "EUR", members)
    )
    # Seed a stale pending suggestion row so we can prove accept clears it.
    run_op(
        api_database_url,
        lambda s: s.execute(
            AuthoredSeriesSuggestion.__table__.insert().values(
                authored_series_id=aid,
                document_id=candidate,
                state=SuggestionState.PENDING.value,
            )
        ),
    )

    body = run_op(
        api_database_url,
        lambda s: charts.accept_authored_suggestion(aid, candidate, s, get_settings()),
    )
    assert body["count"] == 4  # candidate is now a real member

    # Membership persisted and the suggestion row was removed.
    assert candidate in member_ids(api_database_url, aid)
    remaining = (
        run_op(
            api_database_url,
            lambda s: s.execute(
                select(AuthoredSeriesSuggestion.document_id).where(
                    AuthoredSeriesSuggestion.authored_series_id == aid,
                    AuthoredSeriesSuggestion.document_id == candidate,
                )
            ),
        )
        .scalars()
        .all()
    )
    assert remaining == []

    # Accepting an unknown document 404s.
    with pytest.raises(HTTPException) as exc:
        run_op(
            api_database_url,
            lambda s: charts.accept_authored_suggestion(aid, 999999999, s, get_settings()),
        )
    assert exc.value.status_code == 404


def test_dismiss_authored_suggestion_tombstones(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"DismissCo-{tag}", "utility-bill", 4)
    members, candidate = ids[:3], ids[3]
    aid = run_op(
        api_database_url, lambda s: _create_series(s, auth_user.id, "Dismiss", "EUR", members)
    )

    body = run_op(
        api_database_url,
        lambda s: charts.dismiss_authored_suggestion(aid, candidate, s, get_settings()),
    )
    assert body["count"] == 0  # nothing left to review after the dismissal

    # A dismissed tombstone row persisted for the (series, document) pair.
    state = run_op(
        api_database_url,
        lambda s: s.execute(
            select(AuthoredSeriesSuggestion.state).where(
                AuthoredSeriesSuggestion.authored_series_id == aid,
                AuthoredSeriesSuggestion.document_id == candidate,
            )
        ),
    ).scalar_one()
    assert state == SuggestionState.DISMISSED

    # Dismissing again upserts the same tombstone (on-conflict update), still 0.
    again = run_op(
        api_database_url,
        lambda s: charts.dismiss_authored_suggestion(aid, candidate, s, get_settings()),
    )
    assert again["count"] == 0

    # Dismissing an unknown document 404s.
    with pytest.raises(HTTPException) as exc:
        run_op(
            api_database_url,
            lambda s: charts.dismiss_authored_suggestion(aid, 999999999, s, get_settings()),
        )
    assert exc.value.status_code == 404


# ------------------------------------------------------- odd-ones-out


def test_get_authored_odd_ones_out_flags_stray_member(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    main_sender = f"OddMain-{tag}"
    stray_sender = f"OddStray-{tag}"
    _, _, main = seed(api_database_url, main_sender, "utility-bill", 3)
    _, _, stray = seed(api_database_url, stray_sender, "utility-bill", 1)
    aid = run_op(
        api_database_url,
        lambda s: _create_series(s, auth_user.id, "Odd", "EUR", main + stray),
    )

    body = run_op(api_database_url, lambda s: charts.get_authored_odd_ones_out(aid, s))
    members = body["members"]
    assert len(members) == 1  # type: ignore[arg-type]
    entry = members[0]  # type: ignore[index]
    assert entry["id"] == stray[0]
    assert entry["axis"] == "sender"
    assert stray_sender in entry["reason"] and main_sender in entry["reason"]


def test_get_authored_odd_ones_out_empty_for_memberless(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    aid = run_op(api_database_url, lambda s: _create_series(s, auth_user.id, "OddEmpty", "EUR", []))
    body = run_op(api_database_url, lambda s: charts.get_authored_odd_ones_out(aid, s))
    assert body == {"members": []}


# ---------------------------------- authored read-path signature extras


def test_get_chart_authored_carries_signature_extras(
    api_app: object, auth_user: AuthUser, api_database_url: str
) -> None:
    """The single authored-chart fetch decorates the body with the signature and
    the live suggestion / odd-one-out counts (``_authored_signature_extras``)."""
    tag = uuid.uuid4().hex[:8]
    _, _, ids = seed(api_database_url, f"ExtrasCo-{tag}", "utility-bill", 4)
    aid = run_op(
        api_database_url, lambda s: _create_series(s, auth_user.id, "Extras", "EUR", ids[:3])
    )

    body = run_op(
        api_database_url,
        lambda s: charts.get_chart(f"a-{aid}", s, get_settings()),
    )
    assert body["authored_id"] == aid
    assert body["signature"] is not None
    assert body["suggestion_count"] == 1  # the 4th matching doc awaits review
    assert body["odd_one_out_count"] == 0
