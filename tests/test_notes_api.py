"""Integration tests for the notes REST API (U2): in-app note authoring with
in-place editing and version-history snapshots.

A note is a born-digital ``text/markdown`` Document (source ``note``) created
through the new notes router. It bypasses the SHA-256 content dedup (its sha is
salted), is edited in place, and snapshots its previous (title, body) into
``note_versions`` on every edit/restore.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from library.jobs import advance_pipeline
from library.models import DocumentSource
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def run_pipeline(database_url: str, document_id: int) -> None:
    """Drive a document through the processing pipeline on its own engine.

    For a born-digital note this needs no API key: OCR is a passthrough read of
    the stored body, markdown is the born-digital passthrough, and extraction /
    embedding self-skip. The thumbnail/insight defers inside ``advance_pipeline``
    are best-effort and swallowed, so they do not need a live job queue here.
    """

    async def _run() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await advance_pipeline(factory, document_id)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def create_note(client: TestClient, title: str, body_markdown: str) -> dict[str, Any]:
    response = client.post("/api/notes", json={"title": title, "body_markdown": body_markdown})
    assert response.status_code == 201, response.text
    return response.json()


# --- Create ------------------------------------------------------------------


def test_create_note_is_born_digital_markdown(
    api_client: TestClient, api_database_url: str
) -> None:
    body = create_note(api_client, "My Note", "# Hello\n\nWorld.")
    note_id = body["id"]
    assert body["source"] == "note"
    assert body["title"] == "My Note"
    assert body["mime_type"] == "text/markdown"
    # The author's title is locked against re-extraction.
    assert "title" in body["user_edited_fields"]

    # Pipeline turns the stored body into ocr_text and one markdown page, free.
    run_pipeline(api_database_url, note_id)
    detail = api_client.get(f"/api/documents/{note_id}").json()
    assert detail["ocr_text"].strip() == "# Hello\n\nWorld."
    assert detail["source"] == "note"

    markdown = api_client.get(f"/api/documents/{note_id}/markdown").json()
    assert markdown["page_count"] == 1
    assert markdown["pages"][0]["markdown"].strip() == "# Hello\n\nWorld."


def test_create_materializes_reader_without_worker(api_client: TestClient) -> None:
    """The born-digital body is readable immediately — no worker run required."""
    note = create_note(api_client, "Instant", "# Title\n\nbody now.")
    note_id = note["id"]
    # No run_pipeline(): the markdown page + ocr_text must already be present.
    detail = api_client.get(f"/api/documents/{note_id}").json()
    assert detail["ocr_text"].strip() == "# Title\n\nbody now."
    markdown = api_client.get(f"/api/documents/{note_id}/markdown").json()
    assert markdown["page_count"] == 1
    assert markdown["pages"][0]["markdown"].strip() == "# Title\n\nbody now."


def test_identical_notes_both_succeed_distinct_sha(api_client: TestClient) -> None:
    """Two notes with an identical body coexist (salted sha bypasses dedup)."""
    first = create_note(api_client, "Dup A", "exactly the same body")
    second = create_note(api_client, "Dup B", "exactly the same body")
    assert first["id"] != second["id"]
    assert first["sha256"] != second["sha256"]


# --- Edit + version history ---------------------------------------------------


def test_patch_snapshots_old_and_updates_body(
    api_client: TestClient, api_database_url: str
) -> None:
    note = create_note(api_client, "T1", "body one")
    note_id = note["id"]
    run_pipeline(api_database_url, note_id)

    response = api_client.patch(
        f"/api/notes/{note_id}", json={"title": "T2", "body_markdown": "body two"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "T2"
    assert body["ocr_text"].strip() == "body two"
    assert "title" in body["user_edited_fields"]

    versions = api_client.get(f"/api/notes/{note_id}/versions").json()
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1
    assert versions[0]["title"] == "T1"
    assert versions[0]["body"].strip() == "body one"
    assert versions[0]["created_at"]


def test_patch_updates_reader_without_worker(api_client: TestClient, api_database_url: str) -> None:
    """An edit rewrites the reader page synchronously, not via the async worker."""
    note = create_note(api_client, "T1", "body one")
    note_id = note["id"]
    run_pipeline(api_database_url, note_id)

    response = api_client.patch(f"/api/notes/{note_id}", json={"body_markdown": "body two"})
    assert response.status_code == 200, response.text
    # No second run_pipeline(): the page must already reflect the new body.
    markdown = api_client.get(f"/api/documents/{note_id}/markdown").json()
    assert markdown["page_count"] == 1
    assert markdown["pages"][0]["markdown"].strip() == "body two"


def test_empty_patch_is_a_noop_no_version(api_client: TestClient, api_database_url: str) -> None:
    """A PATCH with no fields must not snapshot a phantom version."""
    note = create_note(api_client, "T1", "body one")
    note_id = note["id"]
    run_pipeline(api_database_url, note_id)

    response = api_client.patch(f"/api/notes/{note_id}", json={})
    assert response.status_code == 200, response.text
    assert api_client.get(f"/api/notes/{note_id}/versions").json() == []


def test_get_versions_newest_first(api_client: TestClient, api_database_url: str) -> None:
    note = create_note(api_client, "v0", "b0")
    note_id = note["id"]
    run_pipeline(api_database_url, note_id)

    for body_markdown in ("b1", "b2"):
        response = api_client.patch(f"/api/notes/{note_id}", json={"body_markdown": body_markdown})
        assert response.status_code == 200, response.text

    versions = api_client.get(f"/api/notes/{note_id}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]
    # Version 2 snapshotted the body before the second edit ("b1"); version 1
    # snapshotted the original ("b0").
    assert versions[0]["body"].strip() == "b1"
    assert versions[1]["body"].strip() == "b0"


def test_restore_snapshots_current_then_restores(
    api_client: TestClient, api_database_url: str
) -> None:
    note = create_note(api_client, "orig", "original body")
    note_id = note["id"]
    run_pipeline(api_database_url, note_id)

    edited = api_client.patch(
        f"/api/notes/{note_id}", json={"title": "edited", "body_markdown": "edited body"}
    )
    assert edited.status_code == 200, edited.text

    response = api_client.post(f"/api/notes/{note_id}/versions/1/restore")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "orig"
    assert body["ocr_text"].strip() == "original body"

    # Restore snapshotted the pre-restore (edited) state as a new version.
    versions = api_client.get(f"/api/notes/{note_id}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert versions[0]["title"] == "edited"
    assert versions[0]["body"].strip() == "edited body"

    # Unknown version number 404s.
    assert api_client.post(f"/api/notes/{note_id}/versions/999/restore").status_code == 404


# --- Guards ------------------------------------------------------------------


def test_note_endpoints_404_on_non_note(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "u2-not-a-note", source=DocumentSource.UPLOAD)
    assert api_client.patch(f"/api/notes/{doc_id}", json={"title": "x"}).status_code == 404
    assert api_client.get(f"/api/notes/{doc_id}/versions").status_code == 404
    assert api_client.post(f"/api/notes/{doc_id}/versions/1/restore").status_code == 404

    assert api_client.get("/api/notes/987654321/versions").status_code == 404
    assert api_client.patch("/api/notes/987654321", json={"title": "x"}).status_code == 404


def test_patch_404_on_deleted_note(api_client: TestClient, api_database_url: str) -> None:
    note = create_note(api_client, "to delete", "body")
    note_id = note["id"]
    assert api_client.delete(f"/api/documents/{note_id}").status_code == 204
    assert api_client.patch(f"/api/notes/{note_id}", json={"title": "x"}).status_code == 404
    assert api_client.get(f"/api/notes/{note_id}/versions").status_code == 404
