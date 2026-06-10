"""Integration tests for POST /api/documents and GET /api/jobs."""

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import fetch_all
from tests.test_images import make_heic

pytestmark = pytest.mark.integration

PDF_CONTENT: bytes = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)
PDF_SHA: str = hashlib.sha256(PDF_CONTENT).hexdigest()


def upload(
    client: TestClient,
    content: bytes,
    filename: str = "invoice.pdf",
    content_type: str = "application/pdf",
) -> tuple[int, dict[str, object]]:
    response = client.post("/api/documents", files={"file": (filename, content, content_type)})
    return response.status_code, response.json()


def test_upload_pdf_full_flow(
    api_client: TestClient, api_database_url: str, tmp_path: Path
) -> None:
    status_code, body = upload(api_client, PDF_CONTENT)
    assert status_code == 201
    assert body["sha256"] == PDF_SHA
    assert body["status"] == "received"
    assert body["duplicate"] is False
    document_id = body["id"]

    # File exists content-addressed under the overridden data_dir.
    stored = tmp_path / "originals" / PDF_SHA[0:2] / PDF_SHA[2:4] / PDF_SHA
    assert stored.read_bytes() == PDF_CONTENT

    # Document row with status=received and the original filename.
    rows = fetch_all(
        api_database_url,
        "SELECT status, original_filename, mime_type FROM documents WHERE id = :id",
        id=document_id,
    )
    assert rows == [("received", "invoice.pdf", "application/pdf")]

    # A "received" ingestion event was written.
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id",
        id=document_id,
    )
    assert ("received",) in events

    # A real procrastinate job row exists for this document.
    jobs = fetch_all(
        api_database_url,
        "SELECT task_name FROM procrastinate_jobs WHERE (args->>'document_id')::bigint = :id",
        id=document_id,
    )
    assert jobs == [("library.jobs.process_document",)]


def test_duplicate_upload_returns_existing(
    api_client: TestClient, api_database_url: str, tmp_path: Path
) -> None:
    content = b"%PDF-1.4 duplicate-test " + b"x" * 32
    sha = hashlib.sha256(content).hexdigest()

    first_status, first_body = upload(api_client, content, filename="a.pdf")
    assert first_status == 201

    second_status, second_body = upload(api_client, content, filename="b.pdf")
    assert second_status == 200
    assert second_body["duplicate"] is True
    assert second_body["id"] == first_body["id"]
    assert second_body["sha256"] == sha

    # Only one document row and one file.
    count = fetch_all(
        api_database_url,
        "SELECT count(*) FROM documents WHERE sha256 = :sha",
        sha=sha,
    )
    assert count == [(1,)]
    files = [p for p in (tmp_path / "originals").rglob("*") if p.is_file() and p.name == sha]
    assert len(files) == 1

    # A duplicate_upload event was logged against the existing document.
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id",
        id=first_body["id"],
    )
    assert ("duplicate_upload",) in events


def test_heic_upload_stores_original_and_derived_jpeg(
    api_client: TestClient, tmp_path: Path
) -> None:
    heic = make_heic(size=(8, 4))
    sha = hashlib.sha256(heic).hexdigest()

    status_code, body = upload(api_client, heic, filename="photo.heic", content_type="image/heic")
    assert status_code == 201
    assert body["sha256"] == sha

    # Original HEIC bytes are what got content-addressed.
    stored = tmp_path / "originals" / sha[0:2] / sha[2:4] / sha
    assert stored.read_bytes() == heic

    # The JPEG conversion is a derived artifact.
    converted = tmp_path / "derived" / sha[0:2] / sha[2:4] / sha / "converted.jpg"
    image = Image.open(io.BytesIO(converted.read_bytes()))
    assert image.format == "JPEG"
    assert image.size == (8, 4)


def test_unsupported_mime_rejected(api_client: TestClient) -> None:
    # A ZIP archive: sniffable, but not in the allowed set.
    zip_bytes = b"PK\x03\x04" + b"\x00" * 32
    status_code, body = upload(
        api_client, zip_bytes, filename="archive.zip", content_type="application/zip"
    )
    assert status_code == 415
    assert "detail" in body


def test_undetectable_content_rejected(api_client: TestClient) -> None:
    # Random bytes that neither sniff nor decode as UTF-8, with a lying client type.
    status_code, _ = upload(
        api_client, b"\xff\xfe\xfd\xfc" * 8, filename="blob.bin", content_type=""
    )
    assert status_code == 415


def test_txt_upload_accepted(api_client: TestClient) -> None:
    status_code, body = upload(
        api_client, b"notitie: rekeningen mei", filename="note.txt", content_type=""
    )
    assert status_code == 201
    assert body["status"] == "received"


def test_upload_too_large_rejected(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from library.config import get_settings

    monkeypatch.setenv("LIBRARY_MAX_UPLOAD_BYTES", "16")
    get_settings.cache_clear()
    status_code, _ = upload(api_client, b"%PDF-1.4 " + b"y" * 64)
    assert status_code == 413


def test_jobs_endpoint_lists_jobs(api_client: TestClient) -> None:
    content = b"%PDF-1.4 jobs-endpoint-test " + b"z" * 32
    status_code, body = upload(api_client, content, filename="jobs.pdf")
    assert status_code == 201

    response = api_client.get("/api/jobs")
    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)
    matching = [job for job in jobs if job["document_id"] == body["id"]]
    assert len(matching) == 1
    job = matching[0]
    assert job["task_name"] == "library.jobs.process_document"
    assert job["status"] in {"todo", "doing", "succeeded", "failed"}
    assert job["attempts"] == 0
    assert set(job) == {"id", "status", "task_name", "attempts", "scheduled_at", "document_id"}

    limited = api_client.get("/api/jobs", params={"limit": 1}).json()
    assert len(limited) == 1
