"""Integration tests for the held-emails REST API (W13).

Real app + real test database (``api_client``/``anon_client`` from conftest).
The database is session-scoped and shared, so every test seeds rows with
unique per-test subjects/Message-IDs, scopes its list assertions to those,
and never asserts absolute totals. Job deferrals land in the real
``procrastinate_jobs`` table (PsycopgConnector), inspected via ``fetch_all``.
"""

import asyncio
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import HeldEmail, HeldEmailStatus
from tests.conftest import AuthUser, fetch_all

pytestmark = pytest.mark.integration


def _trace(subject: str) -> dict[str, Any]:
    return {
        "email_subject": subject,
        "items": [
            {"kind": "body", "filename": None, "stage": "body_substance", "verdict": "filtered"},
            {"kind": "email", "stage": "email_verdict", "verdict": "held"},
        ],
    }


async def _seed_held(database_url: str, **overrides: Any) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            fields: dict[str, Any] = {
                "message_id": f"<{uuid.uuid4().hex}@example.com>",
                "sender": "jane@example.org",
                "subject": "Held email",
                "verdict": "below_substance",
                "reason": "below_substance:3w",
                "imap_folder": "Library/Held",
            }
            fields.update(overrides)
            fields.setdefault("trace", _trace(str(fields["subject"])))
            row = HeldEmail(**fields)
            session.add(row)
            await session.commit()
            return row.id
    finally:
        await engine.dispose()


def seed_held(database_url: str, **overrides: Any) -> int:
    """Insert a held_emails row (sync wrapper), returning its id."""
    return asyncio.run(_seed_held(database_url, **overrides))


def _list(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/held-emails", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _subjects(body: dict[str, Any], tag: str) -> list[str]:
    """This test's rows in a list response (scoped by the unique tag)."""
    return [item["subject"] for item in body["items"] if tag in (item["subject"] or "")]


def test_list_defaults_to_open_held_rows(api_client: TestClient, api_database_url: str) -> None:
    tag = uuid.uuid4().hex[:8]
    seed_held(api_database_url, subject=f"open {tag}")
    seed_held(
        api_database_url,
        subject=f"dismissed {tag}",
        status=HeldEmailStatus.DISMISSED,
    )
    seed_held(
        api_database_url,
        subject=f"ingested {tag}",
        status=HeldEmailStatus.INGESTED,
        document_ids=[123],
    )

    body = _list(api_client, limit=100)
    assert body["limit"] == 100 and body["offset"] == 0
    assert _subjects(body, tag) == [f"open {tag}"]  # resolved rows filtered out

    # Explicit status filters select exactly one lifecycle each.
    assert _subjects(_list(api_client, status="dismissed", limit=100), tag) == [f"dismissed {tag}"]
    assert _subjects(_list(api_client, status="ingested", limit=100), tag) == [f"ingested {tag}"]
    # status=all sees every row, newest-held first.
    all_subjects = _subjects(_list(api_client, status="all", limit=100), tag)
    assert sorted(all_subjects) == sorted([f"open {tag}", f"dismissed {tag}", f"ingested {tag}"])


def test_list_rejects_limit_over_100_and_bad_status(api_client: TestClient) -> None:
    assert api_client.get("/api/held-emails", params={"limit": 101}).status_code == 422
    assert api_client.get("/api/held-emails", params={"status": "bogus"}).status_code == 422


def test_detail_includes_trace_and_owner(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    subject = f"detail {tag}"
    held_id = seed_held(
        api_database_url,
        subject=subject,
        verdict="llm_hold",
        reason="newsletter blast",
        owner_id=auth_user.id,
    )

    response = api_client.get(f"/api/held-emails/{held_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == held_id
    assert body["subject"] == subject
    assert body["verdict"] == "llm_hold"
    assert body["reason"] == "newsletter blast"
    assert body["status"] == "held"
    assert body["owner_id"] == auth_user.id
    assert body["owner"] == auth_user.username  # display_name empty → username
    assert body["document_ids"] == []
    assert body["resolved_at"] is None
    assert body["last_error"] is None
    # The full decision trace rides on the detail (not the list rows).
    assert body["trace"] == _trace(subject)
    listed = _list(api_client, limit=100)
    for item in listed["items"]:
        assert "trace" not in item


def test_detail_unknown_404(api_client: TestClient) -> None:
    assert api_client.get("/api/held-emails/987654321").status_code == 404


def test_ingest_queues_override_job(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    held_id = seed_held(api_database_url, subject=f"queue {tag}")

    response = api_client.post(f"/api/held-emails/{held_id}/ingest")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] is True
    assert isinstance(body["job_id"], int)

    rows = fetch_all(
        api_database_url,
        "SELECT task_name, (args ->> 'held_email_id')::bigint,"
        " (args ->> 'resolved_by_id')::bigint"
        " FROM procrastinate_jobs WHERE id = :job_id",
        job_id=body["job_id"],
    )
    assert rows == [("library.jobs.ingest_held_email", held_id, auth_user.id)]


def test_ingest_conflicts_when_already_resolved(
    api_client: TestClient, api_database_url: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    resolved_id = seed_held(
        api_database_url, subject=f"resolved {tag}", status=HeldEmailStatus.INGESTED
    )
    response = api_client.post(f"/api/held-emails/{resolved_id}/ingest")
    assert response.status_code == 409
    assert "ingested" in response.json()["detail"]
    assert api_client.post("/api/held-emails/987654321/ingest").status_code == 404


def test_dismiss_returns_updated_detail_then_409(
    api_client: TestClient, api_database_url: str, auth_user: AuthUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    held_id = seed_held(api_database_url, subject=f"dismiss {tag}")

    response = api_client.post(f"/api/held-emails/{held_id}/dismiss")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == held_id
    assert body["status"] == "dismissed"
    assert body["resolved_at"] is not None
    assert body["trace"] == _trace(f"dismiss {tag}")

    # Already resolved: a second dismiss is a 409 (from the service ValueError).
    second = api_client.post(f"/api/held-emails/{held_id}/dismiss")
    assert second.status_code == 409
    assert "already dismissed" in second.json()["detail"]
    assert api_client.post("/api/held-emails/987654321/dismiss").status_code == 404

    # The dismissed row moved out of the default (held) listing.
    assert _subjects(_list(api_client, limit=100), tag) == []
    assert _subjects(_list(api_client, status="dismissed", limit=100), tag) == [f"dismiss {tag}"]


def test_held_emails_require_authentication(anon_client: TestClient) -> None:
    assert anon_client.get("/api/held-emails").status_code == 401
    assert anon_client.get("/api/held-emails/1").status_code == 401
    assert anon_client.post("/api/held-emails/1/ingest").status_code == 401
    assert anon_client.post("/api/held-emails/1/dismiss").status_code == 401
