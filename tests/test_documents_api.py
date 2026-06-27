"""Integration tests for the documents REST API (W7): list/search/detail/
PATCH/DELETE/downloads/thumbnail.

Documents are seeded directly into the shared test database; every test
scopes its list queries with a unique tag (the database is shared across
API tests, so unscoped queries would see other tests' documents).
"""

import asyncio
import hashlib
import io
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.config import Settings
from library.extraction.apply import apply_extraction
from library.models import (
    Document,
    DocumentLanguage,
    DocumentSource,
    DocumentStatus,
    Kind,
    Project,
    ReviewStatus,
    Sender,
    Tag,
)
from library.storage import derived_dir, store
from library.thumbnails import THUMBNAIL_NAME
from tests.conftest import fetch_all
from tests.test_extraction_apply import make_metadata, make_outcome, patch_extract

pytestmark = pytest.mark.integration


async def _seed_document(
    database_url: str,
    marker: str,
    *,
    kind_slug: str | None = None,
    sender_name: str | None = None,
    tag_slugs: Iterable[str] = (),
    project_slugs: Iterable[str] = (),
    **fields: Any,
) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = Document(
                sha256=fields.pop("sha256", hashlib.sha256(marker.encode()).hexdigest()),
                mime_type=fields.pop("mime_type", "application/pdf"),
                source=fields.pop("source", DocumentSource.UPLOAD),
                status=fields.pop("status", DocumentStatus.INDEXED),
                **fields,
            )
            if kind_slug is not None:
                kind = (
                    await session.execute(select(Kind).where(Kind.slug == kind_slug))
                ).scalar_one()
                document.kind_id = kind.id
            if sender_name is not None:
                sender = (
                    await session.execute(select(Sender).where(Sender.name == sender_name))
                ).scalar_one_or_none()
                if sender is None:
                    sender = Sender(name=sender_name)
                    session.add(sender)
                    await session.flush()
                document.sender_id = sender.id
            for slug in tag_slugs:
                tag = (
                    await session.execute(select(Tag).where(Tag.slug == slug))
                ).scalar_one_or_none()
                if tag is None:
                    tag = Tag(slug=slug, name=slug)
                    session.add(tag)
                    await session.flush()
                document.tags.append(tag)
            for slug in project_slugs:
                project = (
                    await session.execute(select(Project).where(Project.slug == slug))
                ).scalar_one_or_none()
                if project is None:
                    project = Project(slug=slug, name=slug)
                    session.add(project)
                    await session.flush()
                document.projects.append(project)
            session.add(document)
            await session.commit()
            return document.id
    finally:
        await engine.dispose()


def seed_document(database_url: str, marker: str, **kwargs: Any) -> int:
    """Insert a document row (sync wrapper); the marker makes sha256 unique."""
    return asyncio.run(_seed_document(database_url, marker, **kwargs))


def list_docs(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/documents", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- List, filters, pagination ----------------------------------------------


def test_list_item_shape_and_expansions(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(
        api_database_url,
        "w7-shape",
        kind_slug="invoice",
        sender_name="W7 Shape BV",
        tag_slugs=["w7-shape-b", "w7-shape-a"],
        topics=["installation", "error codes"],
        title="Vormtest",
        summary="Een document.",
        document_date=date(2026, 4, 1),
        page_count=3,
        searchable_pdf=True,
    )

    body = list_docs(api_client, tag="w7-shape-a")
    assert body["total"] == 1
    assert body["limit"] == 25 and body["offset"] == 0
    (item,) = body["items"]
    assert item["id"] == document_id
    assert item["title"] == "Vormtest"
    assert item["kind"] == {"slug": "invoice", "name": "Invoice"}
    assert item["sender"]["name"] == "W7 Shape BV"
    # Tags expanded and sorted by slug.
    assert [tag["slug"] for tag in item["tags"]] == ["w7-shape-a", "w7-shape-b"]
    # Topics surfaced verbatim, insertion order preserved.
    assert item["topics"] == ["installation", "error codes"]
    assert item["document_date"] == "2026-04-01"
    assert item["language"] == "unknown"
    assert item["status"] == "indexed"
    assert item["mime_type"] == "application/pdf"
    assert item["page_count"] == 3
    assert item["has_searchable_pdf"] is True
    assert item["has_thumbnail"] is False
    assert item["snippet"] is None and item["rank"] is None
    # List items stay lean: no OCR text or audit trail.
    assert "ocr_text" not in item and "events" not in item


def test_list_pagination_and_default_ordering(
    api_client: TestClient, api_database_url: str
) -> None:
    newest = seed_document(
        api_database_url, "w7-page-1", tag_slugs=["w7-page"], document_date=date(2026, 3, 1)
    )
    oldest = seed_document(
        api_database_url, "w7-page-2", tag_slugs=["w7-page"], document_date=date(2026, 1, 1)
    )
    dateless = seed_document(api_database_url, "w7-page-3", tag_slugs=["w7-page"])

    body = list_docs(api_client, tag="w7-page", limit=2)
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [newest, oldest]

    page2 = list_docs(api_client, tag="w7-page", limit=2, offset=2)
    assert page2["total"] == 3
    assert [item["id"] for item in page2["items"]] == [dateless]  # NULL dates sort last

    response = api_client.get("/api/documents", params={"limit": 101})
    assert response.status_code == 422


def test_filters_compose(api_client: TestClient, api_database_url: str) -> None:
    invoice = seed_document(
        api_database_url,
        "w7-filter-invoice",
        kind_slug="invoice",
        sender_name="W7 Energie NV",
        tag_slugs=["w7-filter", "w7-filter-energie"],
        language=DocumentLanguage.NLD,
        document_date=date(2026, 2, 10),
        source=DocumentSource.UPLOAD,
    )
    receipt = seed_document(
        api_database_url,
        "w7-filter-receipt",
        kind_slug="receipt",
        tag_slugs=["w7-filter"],
        language=DocumentLanguage.ENG,
        status=DocumentStatus.RECEIVED,
        document_date=date(2025, 1, 1),
        source=DocumentSource.API,
    )

    def ids(**params: Any) -> list[int]:
        return [item["id"] for item in list_docs(api_client, **params)["items"]]

    assert ids(tag="w7-filter") == [invoice, receipt]
    assert ids(tag="w7-filter", kind="invoice") == [invoice]
    assert ids(tag="w7-filter", language="eng") == [receipt]
    assert ids(tag="w7-filter", status="received") == [receipt]
    assert ids(tag="w7-filter", source="api") == [receipt]
    assert ids(tag="w7-filter", date_from="2026-01-01") == [invoice]
    assert ids(tag="w7-filter", date_to="2025-06-01") == [receipt]
    assert ids(tag="w7-filter", date_from="2024-01-01", date_to="2026-12-31", kind="receipt") == [
        receipt
    ]

    # Repeatable tag is AND: only the invoice carries both tags.
    response = api_client.get(
        "/api/documents", params=[("tag", "w7-filter"), ("tag", "w7-filter-energie")]
    )
    assert [item["id"] for item in response.json()["items"]] == [invoice]

    # sender_id filter, resolved via the expanded sender on the list item.
    sender_id = list_docs(api_client, tag="w7-filter-energie")["items"][0]["sender"]["id"]
    assert ids(tag="w7-filter", sender_id=sender_id) == [invoice]
    assert ids(tag="w7-filter", sender_id=sender_id, language="eng") == []


# --- Full-text search --------------------------------------------------------


def test_search_dutch_stemming_with_snippet_and_rank(
    api_client: TestClient, api_database_url: str
) -> None:
    document_id = seed_document(
        api_database_url,
        "w7-zoek-nl",
        tag_slugs=["w7-zoek"],
        title="Overzicht rekeningen",
        ocr_text="Hierbij ontvangt u de rekeningen voor de maand mei.",
        language=DocumentLanguage.NLD,
    )

    body = list_docs(api_client, q="rekening", tag="w7-zoek")
    assert body["total"] == 1
    (item,) = body["items"]
    assert item["id"] == document_id
    assert item["rank"] > 0
    assert "<b>rekeningen</b>" in item["snippet"]


def test_search_english_stemming(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(
        api_database_url,
        "w7-zoek-en",
        tag_slugs=["w7-zoek-eng"],
        title="Household insurance",
        ocr_text="These insurance policies cover the household contents.",
        language=DocumentLanguage.ENG,
    )

    body = list_docs(api_client, q="policy", tag="w7-zoek-eng")
    assert [item["id"] for item in body["items"]] == [document_id]
    assert "<b>policies</b>" in body["items"][0]["snippet"]


def test_search_ranks_heavier_match_first(api_client: TestClient, api_database_url: str) -> None:
    heavy = seed_document(
        api_database_url,
        "w7-rank-heavy",
        tag_slugs=["w7-rank"],
        title="Hypotheek hypotheek hypotheek",
        ocr_text="hypotheek " * 20,
    )
    light = seed_document(
        api_database_url,
        "w7-rank-light",
        tag_slugs=["w7-rank"],
        title="Brief",
        ocr_text="een brief over de hypotheek en verder veel andere onderwerpen "
        "zoals tuinonderhoud en parkeren",
    )

    body = list_docs(api_client, q="hypotheek", tag="w7-rank")
    assert [item["id"] for item in body["items"]] == [heavy, light]
    ranks = [item["rank"] for item in body["items"]]
    assert ranks[0] > ranks[1] > 0


def test_search_long_doc_not_inflated_by_repetition(
    api_client: TestClient, api_database_url: str
) -> None:
    """FTS length normalization keeps a short on-topic invoice ahead of a long,
    multi-topic doc that merely repeats the matched term among lots of filler."""
    short = seed_document(
        api_database_url,
        "w7-norm-short",
        tag_slugs=["w7-norm"],
        title="Energie factuur",
        ocr_text="Energie factuur 2026.",
    )
    filler = " ".join(f"onderwerp{i}" for i in range(400))
    seed_document(
        api_database_url,
        "w7-norm-long",
        tag_slugs=["w7-norm"],
        title="Lang dossier",
        ocr_text=f"{filler} energie {filler} energie {filler} energie {filler}",
    )

    body = list_docs(api_client, q="energie", tag="w7-norm")
    assert body["items"][0]["id"] == short


def test_search_snippet_is_capped_and_excludes_distant_html(
    api_client: TestClient, api_database_url: str
) -> None:
    filler = " ".join(f"woord{i}" for i in range(300))
    seed_document(
        api_database_url,
        "w7-snippet",
        tag_slugs=["w7-snippet"],
        ocr_text=f"<script>alert('xss')</script> {filler} het grachtenpand staat te koop",
    )

    body = list_docs(api_client, q="grachtenpand", tag="w7-snippet")
    (item,) = body["items"]
    snippet = item["snippet"]
    assert "<b>grachtenpand</b>" in snippet
    # MaxFragments=2 x MaxWords=12 keeps it short; the distant script tag
    # never makes it into the fragment window.
    assert "<script>" not in snippet
    assert len(snippet.split()) <= 30


def test_search_no_match_returns_empty(api_client: TestClient, api_database_url: str) -> None:
    seed_document(api_database_url, "w7-nomatch", tag_slugs=["w7-nomatch"], ocr_text="factuur")
    body = list_docs(api_client, q="xyzzyplugh", tag="w7-nomatch")
    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


# --- Detail ------------------------------------------------------------------


def test_detail_exposes_content_and_audit_trail_not_raw_extra(
    api_client: TestClient, api_database_url: str
) -> None:
    extraction = {"prompt_version": 1, "model": "claude-haiku-4-5", "fields_set": ["title"]}
    document_id = seed_document(
        api_database_url,
        "w7-detail",
        kind_slug="contract",
        sender_name="W7 Detail BV",
        tag_slugs=["w7-detail"],
        topics=["lease term", "rent"],
        title="Contract",
        ocr_text="De volledige tekst.",
        ocr_confidence=91.5,
        amount_total=Decimal("123.45"),
        currency="EUR",
        due_date=date(2026, 7, 1),
        expiry_date=date(2027, 1, 1),
        original_filename="contract.pdf",
        extra={"extraction": extraction, "user_edited_fields": ["title"], "private": "hidden"},
    )

    response = api_client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["ocr_text"] == "De volledige tekst."
    assert body["topics"] == ["lease term", "rent"]  # surfaced on detail too
    assert body["ocr_confidence"] == 91.5
    assert Decimal(body["amount_total"]) == Decimal("123.45")
    assert body["currency"] == "EUR"
    assert body["due_date"] == "2026-07-01"
    assert body["expiry_date"] == "2027-01-01"
    assert body["source"] == "upload"
    assert body["original_filename"] == "contract.pdf"
    assert body["sha256"] == hashlib.sha256(b"w7-detail").hexdigest()
    assert body["extraction"] == extraction
    assert body["user_edited_fields"] == ["title"]
    assert body["events"] == []  # seeded directly, no pipeline events
    # Deliberate subset: the raw extra JSONB is not exposed.
    assert "extra" not in body
    assert "private" not in body


def test_detail_unknown_document_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/987654321").status_code == 404


# --- PATCH -------------------------------------------------------------------


def test_patch_edits_metadata_and_records_contract(
    api_client: TestClient, api_database_url: str
) -> None:
    document_id = seed_document(
        api_database_url,
        "w7-patch",
        kind_slug="invoice",
        tag_slugs=["w7-patch-old"],
        title="Oude titel",
    )

    response = api_client.patch(
        f"/api/documents/{document_id}",
        json={
            "title": "Nieuwe titel",
            "summary": "Bijgewerkt.",
            "document_date": "2026-05-20",
            "kind_slug": "receipt",
            "sender": "W7 Patch Afzender",
            "tags": ["w7-patch-new", "w7-patch-extra"],
            "language": "nld",
            "amount_total": "250.00",
            "currency": "eur",
            "due_date": "2026-08-01",
            "expiry_date": None,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Nieuwe titel"
    assert body["summary"] == "Bijgewerkt."
    assert body["document_date"] == "2026-05-20"
    assert body["kind"]["slug"] == "receipt"
    assert body["sender"]["name"] == "W7 Patch Afzender"
    assert sorted(tag["slug"] for tag in body["tags"]) == ["w7-patch-extra", "w7-patch-new"]
    assert body["language"] == "nld"
    assert Decimal(body["amount_total"]) == Decimal("250.00")
    assert body["currency"] == "EUR"  # normalized to upper case
    assert body["due_date"] == "2026-08-01"
    assert body["expiry_date"] is None
    # The W6 contract marker uses storage-level names.
    assert set(body["user_edited_fields"]) == {
        "kind_id",
        "sender_id",
        "tags",
        "title",
        "summary",
        "document_date",
        "language",
        "amount_total",
        "currency",
        "due_date",
        "expiry_date",
    }
    edited_events = [event for event in body["events"] if event["event"] == "user_edited"]
    assert len(edited_events) == 1
    assert set(edited_events[0]["detail"]["fields"]) == set(body["user_edited_fields"])

    # Old tag association is fully replaced.
    body = list_docs(api_client, tag="w7-patch-old")
    assert body["total"] == 0


def test_patch_unknown_kind_rejected(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(api_database_url, "w7-patch-badkind")
    response = api_client.patch(f"/api/documents/{document_id}", json={"kind_slug": "not-a-kind"})
    assert response.status_code == 422
    assert "not-a-kind" in response.json()["detail"]


def test_patch_null_tags_and_language_rejected(
    api_client: TestClient, api_database_url: str
) -> None:
    document_id = seed_document(api_database_url, "w7-patch-nulls")
    assert api_client.patch(f"/api/documents/{document_id}", json={"tags": None}).status_code == 422
    assert (
        api_client.patch(f"/api/documents/{document_id}", json={"language": None}).status_code
        == 422
    )


def test_patch_empty_body_changes_nothing(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(api_database_url, "w7-patch-empty", title="Blijft")
    response = api_client.patch(f"/api/documents/{document_id}", json={})
    assert response.status_code == 200
    assert response.json()["title"] == "Blijft"
    assert response.json()["user_edited_fields"] == []
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id",
        id=document_id,
    )
    assert ("user_edited",) not in events


def test_patch_protects_edits_from_reextraction(
    api_client: TestClient, api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The W6 contract end-to-end: PATCH, then a direct apply_extraction run
    with a mocked outcome must not overwrite the user-edited fields."""
    document_id = seed_document(api_database_url, "w7-patch-w6", ocr_text="Factuur Eneco")
    response = api_client.patch(
        f"/api/documents/{document_id}",
        json={"title": "Mijn eigen titel", "tags": ["w7-w6-handmatig"]},
    )
    assert response.status_code == 200

    outcome = make_outcome(
        make_metadata(title="Geextraheerde titel", tags=["w7-w6-auto"], summary="Auto-samenvatting")
    )
    patch_extract(monkeypatch, outcome)
    settings = Settings(anthropic_api_key="test-key", extraction_daily_budget_usd=1_000.0)

    async def run_extraction() -> None:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = await session.get(Document, document_id)
                assert document is not None
                await apply_extraction(session, document, settings)
        finally:
            await engine.dispose()

    asyncio.run(run_extraction())

    body = api_client.get(f"/api/documents/{document_id}").json()
    assert body["title"] == "Mijn eigen titel"  # user edit wins
    assert [tag["slug"] for tag in body["tags"]] == ["w7-w6-handmatig"]  # tags untouched
    assert body["summary"] == "Auto-samenvatting"  # non-edited fields still extracted
    fields_set = body["extraction"]["fields_set"]
    assert "title" not in fields_set and "tags" not in fields_set


# --- DELETE ------------------------------------------------------------------


def test_delete_soft_deletes_and_404s_everywhere(
    api_client: TestClient, api_database_url: str
) -> None:
    document_id = seed_document(api_database_url, "w7-delete", tag_slugs=["w7-delete"])

    response = api_client.delete(f"/api/documents/{document_id}")
    assert response.status_code == 204

    rows = fetch_all(
        api_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=document_id
    )
    assert rows[0][0] is not None
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id",
        id=document_id,
    )
    assert ("deleted",) in events

    assert list_docs(api_client, tag="w7-delete")["total"] == 0
    for request in (
        lambda: api_client.get(f"/api/documents/{document_id}"),
        lambda: api_client.patch(f"/api/documents/{document_id}", json={"title": "x"}),
        lambda: api_client.delete(f"/api/documents/{document_id}"),
        lambda: api_client.get(f"/api/documents/{document_id}/original"),
        lambda: api_client.get(f"/api/documents/{document_id}/searchable.pdf"),
        lambda: api_client.get(f"/api/documents/{document_id}/thumbnail"),
    ):
        assert request().status_code == 404


# --- Re-extraction -------------------------------------------------------------


def test_extract_queues_job_and_returns_202(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(api_database_url, "w11-extract", title="Te verversen")

    response = api_client.post(f"/api/documents/{document_id}/extract")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] is True
    assert isinstance(body["job_id"], int)

    rows = fetch_all(
        api_database_url,
        "SELECT task_name, (args ->> 'document_id')::bigint FROM procrastinate_jobs "
        "WHERE id = :job_id",
        job_id=body["job_id"],
    )
    assert rows == [("library.jobs.extract_document", document_id)]


def test_extract_unknown_or_deleted_document_404(
    api_client: TestClient, api_database_url: str
) -> None:
    assert api_client.post("/api/documents/987654321/extract").status_code == 404

    document_id = seed_document(api_database_url, "w11-extract-deleted")
    assert api_client.delete(f"/api/documents/{document_id}").status_code == 204
    assert api_client.post(f"/api/documents/{document_id}/extract").status_code == 404


# --- Downloads and thumbnail --------------------------------------------------


def test_download_original_content_type_and_disposition(
    api_client: TestClient, api_database_url: str
) -> None:
    content = b"%PDF-1.4 w7-download-original"
    stored = store(content)  # data_dir points at the test tmp_path via api_app
    document_id = seed_document(
        api_database_url,
        content.decode(),  # marker yielding sha256(content)... see below
        original_filename="factuur-mei.pdf",
    )
    # seed_document hashes the marker, which here IS the content bytes.
    assert stored.sha256 == hashlib.sha256(content).hexdigest()

    response = api_client.get(f"/api/documents/{document_id}/original")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition and "factuur-mei.pdf" in disposition
    assert response.content == content


def test_download_searchable_pdf(api_client: TestClient, api_database_url: str) -> None:
    pdf = b"%PDF-1.4 w7-searchable"
    with_pdf = seed_document(
        api_database_url, "w7-searchable-yes", original_filename="scan.pdf", searchable_pdf=True
    )
    sha = hashlib.sha256(b"w7-searchable-yes").hexdigest()
    (derived_dir(sha) / "searchable.pdf").write_bytes(pdf)
    without_pdf = seed_document(api_database_url, "w7-searchable-no")

    response = api_client.get(f"/api/documents/{with_pdf}/searchable.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "scan-searchable.pdf" in response.headers["content-disposition"]
    assert response.content == pdf

    assert api_client.get(f"/api/documents/{without_pdf}/searchable.pdf").status_code == 404


def test_download_original_inline_disposition(
    api_client: TestClient, api_database_url: str
) -> None:
    """`?disposition=inline` serves inline (for the detail-page preview) but
    keeps the filename so a user-initiated save still gets the right name."""
    content = b"%PDF-1.4 w-disposition-original"
    store(content)
    document_id = seed_document(
        api_database_url, content.decode(), original_filename="factuur-juni.pdf"
    )

    response = api_client.get(f"/api/documents/{document_id}/original?disposition=inline")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline") and "factuur-juni.pdf" in disposition
    assert "attachment" not in disposition

    # Explicit attachment and the default behave identically.
    for url in (
        f"/api/documents/{document_id}/original?disposition=attachment",
        f"/api/documents/{document_id}/original",
    ):
        response = api_client.get(url)
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("attachment")

    assert (
        api_client.get(f"/api/documents/{document_id}/original?disposition=bogus").status_code
        == 422
    )


def test_download_searchable_pdf_inline_disposition(
    api_client: TestClient, api_database_url: str
) -> None:
    pdf = b"%PDF-1.4 w-disposition-searchable"
    document_id = seed_document(
        api_database_url,
        "w-disposition-searchable",
        original_filename="scan.pdf",
        searchable_pdf=True,
    )
    sha = hashlib.sha256(b"w-disposition-searchable").hexdigest()
    (derived_dir(sha) / "searchable.pdf").write_bytes(pdf)

    response = api_client.get(f"/api/documents/{document_id}/searchable.pdf?disposition=inline")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline") and "scan-searchable.pdf" in disposition

    response = api_client.get(f"/api/documents/{document_id}/searchable.pdf")
    assert response.headers["content-disposition"].startswith("attachment")

    assert (
        api_client.get(
            f"/api/documents/{document_id}/searchable.pdf?disposition=download"
        ).status_code
        == 422
    )


def test_thumbnail_endpoint_serves_webp_and_marks_presence(
    api_client: TestClient, api_database_url: str
) -> None:
    with_thumb = seed_document(api_database_url, "w7-thumb-yes", tag_slugs=["w7-thumb"])
    sha = hashlib.sha256(b"w7-thumb-yes").hexdigest()
    Image.new("RGB", (480, 640), "white").save(derived_dir(sha) / THUMBNAIL_NAME, format="WEBP")
    without_thumb = seed_document(api_database_url, "w7-thumb-no", tag_slugs=["w7-thumb"])

    response = api_client.get(f"/api/documents/{with_thumb}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(response.content)).format == "WEBP"

    assert api_client.get(f"/api/documents/{without_thumb}/thumbnail").status_code == 404

    by_id = {item["id"]: item for item in list_docs(api_client, tag="w7-thumb")["items"]}
    assert by_id[with_thumb]["has_thumbnail"] is True
    assert by_id[without_thumb]["has_thumbnail"] is False


# --- Corrections flywheel ----------------------------------------------------


def test_patch_records_correction(api_client: TestClient, api_database_url: str) -> None:
    """PATCH appends a mining-ready correction record to extra['corrections']."""
    extraction = {
        "prompt_version": "v3",
        "model": "claude-haiku-4-5",
        "fields_set": ["amount_total"],
    }
    document_id = seed_document(
        api_database_url,
        "w7-correction",
        amount_total=Decimal("99.99"),
        extra={"extraction": extraction},
    )

    response = api_client.patch(
        f"/api/documents/{document_id}",
        json={"amount_total": "150.00"},
    )
    assert response.status_code == 200, response.text

    # The API detail response does not expose raw extra; read via DB.
    rows = fetch_all(
        api_database_url,
        "SELECT extra FROM documents WHERE id = :id",
        id=document_id,
    )
    extra = rows[0][0]
    corrections = extra.get("corrections", [])

    assert len(corrections) == 1, corrections
    rec = corrections[0]
    assert rec["field"] == "amount_total"
    assert rec["original_value"] == "99.99"
    assert rec["corrected_value"] == "150.00"
    assert rec["prompt_version"] == "v3"
    assert rec["model"] == "claude-haiku-4-5"
    assert rec["corrected_at"]  # ISO timestamp, non-empty
    assert isinstance(rec["source_excerpt"], str)  # present; may be empty if field not in ocr_text


# --- List item amount_total/currency -----------------------------------------


def test_list_item_includes_amount(api_client: TestClient, api_database_url: str) -> None:
    doc_id = seed_document(api_database_url, "w7-list-amount", tag_slugs=["w7-list-amount"])
    resp = api_client.patch(
        f"/api/documents/{doc_id}", json={"amount_total": "92.50", "currency": "EUR"}
    )
    assert resp.status_code == 200, resp.text
    item = next(d for d in api_client.get("/api/documents").json()["items"] if d["id"] == doc_id)
    assert Decimal(item["amount_total"]) == Decimal("92.50")
    assert item["currency"] == "EUR"


# --- OpenAPI surface ----------------------------------------------------------


def test_openapi_is_curated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    tags = {tag["name"]: tag for tag in schema["tags"]}
    assert "documents" in tags and tags["documents"]["description"]
    assert "jobs" in tags and tags["jobs"]["description"]
    assert schema["info"]["description"]

    list_op = schema["paths"]["/api/documents"]["get"]
    q_param = next(p for p in list_op["parameters"] if p["name"] == "q")
    assert q_param["examples"]  # curated search examples
    assert "200" in list_op["responses"]
    expected_paths = {
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/original",
        "/api/documents/{document_id}/searchable.pdf",
        "/api/documents/{document_id}/thumbnail",
    }
    assert expected_paths <= set(schema["paths"])


def test_inline_disposition_restricted_to_render_safe_types(
    api_client: TestClient, api_database_url: str
) -> None:
    """Defense in depth: ``?disposition=inline`` is honoured only for MIME
    types a browser can render without executing anything (PDF + raster
    images). Anything else — even though the ingest allowlist already
    blocks active content — is downgraded to attachment, and every file
    response carries nosniff + a sandboxing CSP."""
    content = b"plain text but imagine it were html"
    store(content)
    document_id = seed_document(
        api_database_url,
        content.decode(),
        mime_type="text/plain",
        original_filename="notes.txt",
    )

    response = api_client.get(f"/api/documents/{document_id}/original?disposition=inline")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"

    # PDF stays inline-capable and carries the same hardening headers.
    pdf = b"%PDF-1.4 inline-allowlist-check"
    store(pdf)
    pdf_id = seed_document(api_database_url, pdf.decode(), original_filename="ok.pdf")
    response = api_client.get(f"/api/documents/{pdf_id}/original?disposition=inline")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


# --- Review status -----------------------------------------------------------


def test_list_filters_by_review_status(api_client: TestClient, api_database_url: str) -> None:
    """?review_status=needs_review returns only docs with that status."""
    needs_review_id = seed_document(
        api_database_url,
        "w6-review-needs",
        tag_slugs=["w6-review-filter"],
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    seed_document(
        api_database_url,
        "w6-review-unreviewed",
        tag_slugs=["w6-review-filter"],
        review_status=ReviewStatus.UNREVIEWED,
    )

    body = list_docs(api_client, tag="w6-review-filter", review_status="needs_review")
    assert body["total"] == 1
    (item,) = body["items"]
    assert item["id"] == needs_review_id
    assert item["review_status"] == "needs_review"


def test_verify_endpoint_marks_verified(api_client: TestClient, api_database_url: str) -> None:
    """POST /api/documents/{id}/verify sets review_status to 'verified'."""
    document_id = seed_document(
        api_database_url,
        "w6-verify",
        review_status=ReviewStatus.NEEDS_REVIEW,
    )

    response = api_client.post(f"/api/documents/{document_id}/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_status"] == "verified"


def test_detail_exposes_validation(api_client: TestClient, api_database_url: str) -> None:
    """GET /api/documents/{id} exposes extra['validation'] under `validation`."""
    validation_blob = {"score": 0.92, "flags": ["amount_mismatch"], "checked_at": "2026-06-21"}
    document_id = seed_document(
        api_database_url,
        "w6-detail-validation",
        extra={"validation": validation_blob},
    )

    response = api_client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validation"] == validation_blob


# --- Projects (W6) -----------------------------------------------------------


def test_list_item_includes_projects_and_project_filter(
    api_client: TestClient, api_database_url: str
) -> None:
    """List items expose `projects` (sorted by slug) and ?project= filters."""
    in_project = seed_document(
        api_database_url,
        "w6-proj-in",
        tag_slugs=["w6-proj"],
        project_slugs=["w6-proj-beta", "w6-proj-alpha"],
    )
    seed_document(api_database_url, "w6-proj-out", tag_slugs=["w6-proj"])

    body = list_docs(api_client, tag="w6-proj")
    by_id = {item["id"]: item for item in body["items"]}
    # Projects expanded and sorted by slug.
    assert [p["slug"] for p in by_id[in_project]["projects"]] == ["w6-proj-alpha", "w6-proj-beta"]
    assert by_id[in_project]["projects"][0] == {"slug": "w6-proj-alpha", "name": "w6-proj-alpha"}

    # ?project= narrows to members of that project only.
    filtered = list_docs(api_client, tag="w6-proj", project="w6-proj-alpha")
    assert [item["id"] for item in filtered["items"]] == [in_project]


def test_patch_sets_and_clears_projects(api_client: TestClient, api_database_url: str) -> None:
    """PATCH projects upserts unknown projects by name, then [] clears them."""
    document_id = seed_document(api_database_url, "w6-proj-patch", tag_slugs=["w6-proj-patch"])

    # Setting a name that does not exist yet creates the project (slugified).
    response = api_client.patch(
        f"/api/documents/{document_id}", json={"projects": ["House purchase"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projects"] == [{"slug": "house-purchase", "name": "House purchase"}]
    assert "projects" in body["user_edited_fields"]
    project_events = [e for e in body["events"] if e["event"] == "project_changed"]
    assert len(project_events) == 1
    assert project_events[0]["detail"]["projects"] == ["house-purchase"]

    # The project is now discoverable and filters the document.
    assert list_docs(api_client, project="house-purchase")["total"] == 1
    listing = api_client.get("/api/projects").json()
    assert any(p["slug"] == "house-purchase" for p in listing)

    # Re-PATCH with [] clears membership; the project row survives (count 0).
    cleared = api_client.patch(f"/api/documents/{document_id}", json={"projects": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["projects"] == []
    assert list_docs(api_client, project="house-purchase")["total"] == 0


def test_patch_null_projects_rejected(api_client: TestClient, api_database_url: str) -> None:
    document_id = seed_document(api_database_url, "w6-proj-null")
    response = api_client.patch(f"/api/documents/{document_id}", json={"projects": None})
    assert response.status_code == 422


# --- Topics ------------------------------------------------------------------


def test_patch_sets_and_clears_topics(api_client: TestClient, api_database_url: str) -> None:
    """PATCH topics is a full-replace list: `[]` clears, `null` leaves unchanged."""
    document_id = seed_document(api_database_url, "topics-patch", tag_slugs=["topics-patch"])

    response = api_client.patch(
        f"/api/documents/{document_id}",
        json={"topics": ["installation", "error codes"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["topics"] == ["installation", "error codes"]
    assert "topics" in body["user_edited_fields"]

    # null leaves topics unchanged.
    unchanged = api_client.patch(f"/api/documents/{document_id}", json={"topics": None})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["topics"] == ["installation", "error codes"]

    # Re-PATCH with [] clears them.
    cleared = api_client.patch(f"/api/documents/{document_id}", json={"topics": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["topics"] == []
