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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
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
    Recipient,
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
    recipient_name: str | None = None,
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
            if recipient_name is not None:
                recipient = (
                    await session.execute(select(Recipient).where(Recipient.name == recipient_name))
                ).scalar_one_or_none()
                if recipient is None:
                    recipient = Recipient(name=recipient_name)
                    session.add(recipient)
                    await session.flush()
                document.recipient_id = recipient.id
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


def test_sort_control_orders_by_field_and_direction(
    api_client: TestClient, api_database_url: str
) -> None:
    # Seeded in ascending document_date order so insertion order (created_at/id)
    # runs opposite to document_date, letting us tell the two sort fields apart.
    older = seed_document(
        api_database_url, "w2-sort-1", tag_slugs=["w2-sort"], document_date=date(2026, 1, 1)
    )
    newer = seed_document(
        api_database_url, "w2-sort-2", tag_slugs=["w2-sort"], document_date=date(2026, 3, 1)
    )
    dateless = seed_document(api_database_url, "w2-sort-3", tag_slugs=["w2-sort"])

    def ids(**params: Any) -> list[int]:
        return [item["id"] for item in list_docs(api_client, tag="w2-sort", **params)["items"]]

    # document_date desc (the default): newest first, unknown date last.
    assert ids() == [newer, older, dateless]
    assert ids(sort="document_date", direction="desc") == [newer, older, dateless]
    # document_date asc: oldest first, unknown date STILL last (NULLS LAST both ways).
    assert ids(sort="document_date", direction="asc") == [older, newer, dateless]
    # added_date (created_at) desc: most recently added first (reverse insertion).
    assert ids(sort="added_date", direction="desc") == [dateless, newer, older]
    # added_date asc: earliest added first.
    assert ids(sort="added_date", direction="asc") == [older, newer, dateless]


def test_sort_is_ignored_during_search_rank_wins(
    api_client: TestClient, api_database_url: str
) -> None:
    heavy = seed_document(
        api_database_url,
        "w2-sort-q-heavy",
        tag_slugs=["w2-sortq"],
        ocr_text="factuur factuur factuur",
    )
    light = seed_document(
        api_database_url, "w2-sort-q-light", tag_slugs=["w2-sortq"], ocr_text="factuur eenmalig"
    )

    def ids(**params: Any) -> list[int]:
        body = list_docs(api_client, tag="w2-sortq", q="factuur", **params)
        return [item["id"] for item in body["items"]]

    # Relevance rank orders heavier-match first regardless of the sort params.
    assert ids() == [heavy, light]
    assert ids(sort="added_date", direction="asc") == [heavy, light]
    assert ids(sort="added_date", direction="desc") == [heavy, light]


def test_sort_rejects_unknown_values(api_client: TestClient) -> None:
    assert api_client.get("/api/documents", params={"sort": "bogus"}).status_code == 422
    assert api_client.get("/api/documents", params={"direction": "sideways"}).status_code == 422


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


# --- Recently Deleted: list + restore ----------------------------------------


def deleted_docs(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/documents/deleted", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_list_deleted_returns_only_soft_deleted(
    api_client: TestClient, api_database_url: str
) -> None:
    live_id = seed_document(api_database_url, "w1-deleted-live", tag_slugs=["w1-del-live"])
    # deleted_at = now → sorts to the top of the newest-first deleted list.
    deleted_id = seed_document(api_database_url, "w1-deleted-gone", deleted_at=datetime.now(UTC))

    body = deleted_docs(api_client, limit=100)
    ids = {item["id"] for item in body["items"]}
    assert deleted_id in ids
    assert live_id not in ids  # a live document never appears in Recently Deleted
    assert body["retention_days"] >= 1

    item = next(item for item in body["items"] if item["id"] == deleted_id)
    assert item["deleted_at"] is not None
    assert item["purge_at"] is not None
    assert item["days_remaining"] >= 0

    # The live document is still in the normal list; the deleted one is gone.
    assert list_docs(api_client, tag="w1-del-live")["total"] == 1


def test_list_deleted_days_remaining_reflects_retention(
    api_client: TestClient, api_database_url: str
) -> None:
    # Deleted 10 days ago, default 30-day retention → ~20 days left. Allow a
    # 1-day slack for the sub-second gap between seeding and the endpoint's now().
    deleted_id = seed_document(
        api_database_url,
        "w1-deleted-aged",
        deleted_at=datetime.now(UTC) - timedelta(days=10),
    )
    body = deleted_docs(api_client, limit=100)
    item = next(item for item in body["items"] if item["id"] == deleted_id)
    assert item["days_remaining"] in (19, 20)


def test_restore_clears_deleted_at_and_reappears(
    api_client: TestClient, api_database_url: str
) -> None:
    document_id = seed_document(api_database_url, "w1-restore", tag_slugs=["w1-restore"])
    assert api_client.delete(f"/api/documents/{document_id}").status_code == 204
    assert list_docs(api_client, tag="w1-restore")["total"] == 0

    response = api_client.post(f"/api/documents/{document_id}/restore")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == document_id

    # Reappears in the normal list and on the detail endpoint.
    assert list_docs(api_client, tag="w1-restore")["total"] == 1
    assert api_client.get(f"/api/documents/{document_id}").status_code == 200

    # A restored event is recorded and deleted_at is cleared.
    events = fetch_all(
        api_database_url,
        "SELECT event FROM ingestion_events WHERE document_id = :id",
        id=document_id,
    )
    assert ("restored",) in events
    rows = fetch_all(
        api_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=document_id
    )
    assert rows[0][0] is None

    # No longer in the Recently-Deleted list.
    body = deleted_docs(api_client, limit=100)
    assert document_id not in {item["id"] for item in body["items"]}


def test_restore_unknown_or_live_document_404(
    api_client: TestClient, api_database_url: str
) -> None:
    assert api_client.post("/api/documents/987654321/restore").status_code == 404
    live_id = seed_document(api_database_url, "w1-restore-live")
    assert api_client.post(f"/api/documents/{live_id}/restore").status_code == 404


def test_deleted_note_lists_and_restores(api_client: TestClient, api_database_url: str) -> None:
    # A note is a Document with source=NOTE, so it flows through the same
    # soft-delete/restore path (the "documents + notes" scope decision).
    note_id = seed_document(
        api_database_url,
        "w1-deleted-note",
        source=DocumentSource.NOTE,
        deleted_at=datetime.now(UTC),
    )
    body = deleted_docs(api_client, limit=100)
    assert note_id in {item["id"] for item in body["items"]}
    assert api_client.post(f"/api/documents/{note_id}/restore").status_code == 200


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


def test_download_original_missing_blob_404(api_client: TestClient, api_database_url: str) -> None:
    """A document row can exist while its original blob was never stored (or was
    pruned). The endpoint must 404 with a clear message, not 500 on a missing file."""
    document_id = seed_document(
        api_database_url, "w-original-missing", original_filename="lost.pdf"
    )
    # Deliberately no store() — the blob for this sha256 does not exist on disk.

    response = api_client.get(f"/api/documents/{document_id}/original")
    assert response.status_code == 404
    assert response.json()["detail"] == "original file missing from storage"


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


def test_thumbnail_missing_file_on_live_document_404(
    api_client: TestClient, api_database_url: str
) -> None:
    """A live (non-deleted) document whose thumbnail hasn't been rendered yet must
    404 at the missing-file check with a clear message, not 500. (The soft-deleted
    404 path exits earlier in _get_document_or_404, so it never reaches this branch.)"""
    document_id = seed_document(api_database_url, "w-thumbnail-missing")
    # No thumbnail file written for this sha256's derived dir.

    response = api_client.get(f"/api/documents/{document_id}/thumbnail")
    assert response.status_code == 404
    assert response.json()["detail"] == "no thumbnail for this document"


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
    # Scope the list by this test's unique tag (per the module docstring): the
    # shared session DB + default page (limit 25, newest-first) can otherwise
    # omit this row once other test files have seeded enough documents.
    listing = api_client.get("/api/documents", params={"tag": "w7-list-amount"}).json()
    item = next(d for d in listing["items"] if d["id"] == doc_id)
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


def test_list_exposes_review_findings_for_flagged_rows(
    api_client: TestClient, api_database_url: str
) -> None:
    """needs_review rows carry compact review_findings; clean rows carry []."""
    flagged_id = seed_document(
        api_database_url,
        "w1-list-findings-flagged",
        tag_slugs=["w1-list-findings"],
        review_status=ReviewStatus.NEEDS_REVIEW,
        extra={
            "validation": {
                "findings": [
                    {
                        "rule": "date_plausibility",
                        "field": "document_date",
                        "severity": "warn",
                        "message": "document_date is in the future",
                    }
                ]
            }
        },
    )
    seed_document(
        api_database_url,
        "w1-list-findings-clean",
        tag_slugs=["w1-list-findings"],
        review_status=ReviewStatus.UNREVIEWED,
    )

    body = list_docs(api_client, tag="w1-list-findings")
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[flagged_id]["review_findings"] == [
        {
            "rule": "date_plausibility",
            "field": "document_date",
            "message": "document_date is in the future",
        }
    ]
    clean = next(item for item in body["items"] if item["id"] != flagged_id)
    assert clean["review_findings"] == []


# --- Save-time revalidation (W1) ---------------------------------------------


def test_update_recomputes_validation_and_clears_resolved_finding(
    api_client: TestClient, api_database_url: str
) -> None:
    """Correcting a flagged field re-runs validation in the same PATCH: the
    resolved finding disappears and review_status drops off needs_review."""
    document_id = seed_document(
        api_database_url,
        "reval-clear",
        kind_slug="invoice",
        title="Invoice",
        document_date=date(2041, 3, 12),  # future -> date_plausibility fires
        review_status=ReviewStatus.NEEDS_REVIEW,
        extra={
            "validation": {
                "prompt_version": "seed",
                "findings": [
                    {
                        "rule": "date_plausibility",
                        "field": "document_date",
                        "severity": "warn",
                        "message": "document_date is in the future",
                    }
                ],
                "validated_at": "2026-07-01T00:00:00+00:00",
            }
        },
    )

    response = api_client.patch(
        f"/api/documents/{document_id}", json={"document_date": "2024-03-12"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_date"] == "2024-03-12"
    assert body["review_status"] != "needs_review"
    rules = [finding["rule"] for finding in (body["validation"]["findings"] or [])]
    assert "date_plausibility" not in rules


def test_update_keeps_unfixable_finding(api_client: TestClient, api_database_url: str) -> None:
    """An edit that does not address a low-OCR finding leaves it — and the
    needs_review flag — in place after revalidation."""
    document_id = seed_document(
        api_database_url,
        "reval-keep",
        kind_slug="invoice",
        title="Invoice",
        ocr_confidence=10.0,  # below the 50.0 floor -> ocr_confidence_gate fires
        review_status=ReviewStatus.NEEDS_REVIEW,
    )

    response = api_client.patch(
        f"/api/documents/{document_id}", json={"title": "Invoice (checked)"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_status"] == "needs_review"
    rules = [finding["rule"] for finding in body["validation"]["findings"]]
    assert "ocr_confidence_gate" in rules


def test_update_preserves_verified_status(api_client: TestClient, api_database_url: str) -> None:
    """Editing a user-verified document that has no findings must not demote it
    back to unreviewed."""
    document_id = seed_document(
        api_database_url,
        "reval-verified",
        kind_slug="invoice",
        title="Invoice",
        document_date=date(2024, 1, 1),
        review_status=ReviewStatus.VERIFIED,
    )

    response = api_client.patch(f"/api/documents/{document_id}", json={"title": "Invoice (edited)"})
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "verified"


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


def test_project_filter_or_composes(api_client: TestClient, api_database_url: str) -> None:
    """Repeating ?project= returns the UNION (documents in any of the projects)."""
    in_alpha = seed_document(
        api_database_url,
        "w-multiproj-a",
        tag_slugs=["w-multiproj"],
        project_slugs=["w-multiproj-alpha"],
    )
    in_beta = seed_document(
        api_database_url,
        "w-multiproj-b",
        tag_slugs=["w-multiproj"],
        project_slugs=["w-multiproj-beta"],
    )
    seed_document(api_database_url, "w-multiproj-c", tag_slugs=["w-multiproj"])  # in neither

    body = list_docs(
        api_client, tag="w-multiproj", project=["w-multiproj-alpha", "w-multiproj-beta"]
    )
    # Union: both members appear, the non-member does not.
    assert sorted(item["id"] for item in body["items"]) == sorted([in_alpha, in_beta])


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


# --- Timestamps: ingestion date / last edited --------------------------------


def test_detail_exposes_created_and_updated_at(
    api_client: TestClient, api_database_url: str
) -> None:
    """Detail surfaces both created_at (ingestion) and updated_at (last edited)."""
    document_id = seed_document(api_database_url, "w-dates-detail")
    body = api_client.get(f"/api/documents/{document_id}").json()
    assert "created_at" in body and "updated_at" in body
    # Freshly ingested and never edited: updated_at is not before ingestion.
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(body["created_at"])


def test_patch_projects_only_bumps_last_edited(
    api_client: TestClient, api_database_url: str
) -> None:
    """A projects-only edit advances updated_at past created_at.

    "Last edited" must reflect *any* change, including membership-only edits
    that touch the join table rather than a mapped document column.
    """
    document_id = seed_document(api_database_url, "w-dates-bump")
    before = api_client.get(f"/api/documents/{document_id}").json()
    created_at = datetime.fromisoformat(before["created_at"])
    updated_before = datetime.fromisoformat(before["updated_at"])

    response = api_client.patch(
        f"/api/documents/{document_id}", json={"projects": ["w-dates-bump-proj"]}
    )
    assert response.status_code == 200, response.text
    updated_after = datetime.fromisoformat(response.json()["updated_at"])

    # The membership edit happened in a later transaction than the insert.
    assert updated_after > created_at
    assert updated_after > updated_before


# --- Topics ------------------------------------------------------------------


def test_patch_topics_is_read_only(api_client: TestClient, api_database_url: str) -> None:
    """topics is auto-extracted, never user-editable: a PATCH body containing
    topics is ignored (the field was removed from DocumentUpdate, so pydantic
    drops it) and the stored topics are left untouched."""
    document_id = seed_document(
        api_database_url,
        "topics-readonly",
        tag_slugs=["topics-readonly"],
        topics=["installation", "error codes"],
    )

    # A PATCH with ONLY topics is a no-op: nothing changes, no edit recorded.
    response = api_client.patch(
        f"/api/documents/{document_id}",
        json={"topics": ["hacked", "tampered"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["topics"] == ["installation", "error codes"]
    assert "topics" not in body["user_edited_fields"]

    # Even alongside a real edit, topics stays auto-extracted (ignored).
    mixed = api_client.patch(
        f"/api/documents/{document_id}",
        json={"title": "Nieuwe titel", "topics": []},
    )
    assert mixed.status_code == 200, mixed.text
    mixed_body = mixed.json()
    assert mixed_body["title"] == "Nieuwe titel"
    assert mixed_body["topics"] == ["installation", "error codes"]
    assert "topics" not in mixed_body["user_edited_fields"]

    # Confirm via a fresh GET that the stored value is unchanged.
    fetched = api_client.get(f"/api/documents/{document_id}").json()
    assert fetched["topics"] == ["installation", "error codes"]


def test_search_matches_topics(api_client: TestClient, api_database_url: str) -> None:
    """FTS now indexes topics: a document whose only occurrence of a distinctive
    term is in its topics list is returned by ?q= (migration 0012)."""
    document_id = seed_document(
        api_database_url,
        "topics-search",
        tag_slugs=["topics-search"],
        title="Onderwerpen test",
        summary="Geen bijzondere woorden hier.",
        ocr_text="Deze tekst bevat het zoekwoord niet.",
        topics=["zzxytopicterm"],
        language=DocumentLanguage.NLD,
    )

    body = list_docs(api_client, q="zzxytopicterm", tag="topics-search")
    assert [item["id"] for item in body["items"]] == [document_id]


# --- Recipient (W2) ----------------------------------------------------------


def test_recipient_in_list_and_detail(api_client: TestClient, api_database_url: str) -> None:
    """Seeded recipient is expanded (id + name) on both list and detail."""
    document_id = seed_document(
        api_database_url,
        "w2-recipient-shape",
        tag_slugs=["w2-recipient-shape"],
        recipient_name="W2 John",
    )

    (item,) = list_docs(api_client, tag="w2-recipient-shape")["items"]
    assert item["recipient"]["name"] == "W2 John"
    assert isinstance(item["recipient"]["id"], int)

    detail = api_client.get(f"/api/documents/{document_id}").json()
    assert detail["recipient"]["name"] == "W2 John"


def test_patch_recipient_upserts_and_returns(api_client: TestClient, api_database_url: str) -> None:
    """PATCH {recipient: 'Wife'} upserts and returns recipient:{id,name}."""
    document_id = seed_document(api_database_url, "w2-recipient-patch")

    response = api_client.patch(f"/api/documents/{document_id}", json={"recipient": "Wife"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipient"]["name"] == "Wife"
    assert isinstance(body["recipient"]["id"], int)
    # Storage-level marker recorded so re-extraction won't overwrite it.
    assert "recipient_id" in body["user_edited_fields"]

    # Clearing it with null removes the recipient.
    cleared = api_client.patch(f"/api/documents/{document_id}", json={"recipient": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["recipient"] is None


def test_patch_recipient_creates_brand_new_recipient(
    api_client: TestClient, api_database_url: str
) -> None:
    """The MANUAL edit path still CREATES a recipient for a brand-new name.

    Unlike the extraction/inference path (which never invents a recipient), a
    user deliberately assigning an unknown recipient via PATCH must create it.
    """
    import uuid

    brand_new = f"Brand New Recipient {uuid.uuid4().hex[:8]}"

    async def count_named() -> int:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    select(func.count()).select_from(Recipient).where(Recipient.name == brand_new)
                )
                return result.scalar_one()
        finally:
            await engine.dispose()

    assert asyncio.run(count_named()) == 0  # does not exist yet

    document_id = seed_document(api_database_url, "w2-recipient-create-new")
    response = api_client.patch(f"/api/documents/{document_id}", json={"recipient": brand_new})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipient"]["name"] == brand_new
    assert isinstance(body["recipient"]["id"], int)
    assert asyncio.run(count_named()) == 1  # created by the manual edit

    # Clearing it with null removes the recipient.
    cleared = api_client.patch(f"/api/documents/{document_id}", json={"recipient": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["recipient"] is None


def test_list_filters_by_recipient_id(api_client: TestClient, api_database_url: str) -> None:
    """?recipient_id=N returns only documents addressed to that recipient."""
    mine = seed_document(
        api_database_url,
        "w2-recipient-filter-mine",
        tag_slugs=["w2-recipient-filter"],
        recipient_name="W2 Filter John",
    )
    seed_document(
        api_database_url,
        "w2-recipient-filter-other",
        tag_slugs=["w2-recipient-filter"],
        recipient_name="W2 Filter Wife",
    )

    recipient_id = next(
        item["recipient"]["id"]
        for item in list_docs(api_client, tag="w2-recipient-filter")["items"]
        if item["id"] == mine
    )
    ids = [
        item["id"]
        for item in list_docs(api_client, tag="w2-recipient-filter", recipient_id=recipient_id)[
            "items"
        ]
    ]
    assert ids == [mine]
