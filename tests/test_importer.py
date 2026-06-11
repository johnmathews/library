"""Tests for the paperless-ngx importer (library.importer, W15).

Strategy: a ``FakePaperless`` in-memory API behind ``httpx.MockTransport``
(real pagination, ``metadata/`` checksums, byte downloads with injectable
corruption), driven through the real ``run_import`` against the
testcontainers database. The CLI command is smoke-tested with the runner
mocked out.
"""

import hashlib
import itertools
import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from typer.testing import CliRunner

from library.cli import app as cli_app
from library.config import Settings, get_settings
from library.importer.client import ChecksumMismatchError, PaperlessClient
from library.importer.mapper import (
    TagSpec,
    Taxonomies,
    map_document,
    parse_created,
    parse_monetary,
)
from library.importer.runner import ImportFailure, ImportReport, format_report, run_import
from library.ingest import ingest_file
from library.jobs import job_app
from library.models import Document, DocumentSource, DocumentStatus, IngestionEvent

# paperless ids must be unique across the whole test session: the test
# database (and its documents.paperless_id unique constraint) is shared.
_IDS = itertools.count(1_000)


def make_pdf(marker: str | None = None) -> bytes:
    """Unique, sniffable-as-PDF content (the test database is shared)."""
    return b"%PDF-1.4\n% " + (marker or uuid.uuid4().hex).encode() + b"\n%%EOF\n"


class FakePaperless:
    """In-memory paperless-ngx API served through httpx.MockTransport."""

    base = "http://paperless.test"

    def __init__(self) -> None:
        self.tags: list[dict[str, Any]] = []
        self.correspondents: list[dict[str, Any]] = []
        self.document_types: list[dict[str, Any]] = []
        self.custom_fields: list[dict[str, Any]] = []
        self.storage_paths: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.originals: dict[int, bytes] = {}
        # id -> how many further downloads should return corrupted bytes
        self.corrupt_remaining: dict[int, int] = {}
        self.download_counts: Counter[int] = Counter()
        self.requests: list[httpx.Request] = []

    def add_document(self, original: bytes, **fields: Any) -> dict[str, Any]:
        """Register a document; ``original`` is the downloadable file's bytes."""
        doc_id = int(fields.pop("id", next(_IDS)))
        doc: dict[str, Any] = {
            "id": doc_id,
            "title": None,
            "created": None,
            "content": "",
            "mime_type": "application/pdf",
            "original_file_name": f"doc-{doc_id}.pdf",
            "correspondent": None,
            "document_type": None,
            "storage_path": None,
            "tags": [],
            "custom_fields": [],
            "notes": [],
            "added": None,
            "archive_serial_number": None,
            "deleted_at": None,
        }
        doc.update(fields)
        self.documents.append(doc)
        self.originals[doc_id] = original
        return doc

    def client(self) -> PaperlessClient:
        return PaperlessClient(self.base, "test-token", transport=httpx.MockTransport(self.handler))

    def _page(self, request: httpx.Request, items: list[dict[str, Any]]) -> httpx.Response:
        params = request.url.params
        page_size = int(params.get("page_size", 100))
        page = int(params.get("page", 1))
        start = (page - 1) * page_size
        next_url = None
        if start + page_size < len(items):
            next_url = f"{self.base}{request.url.path}?page={page + 1}&page_size={page_size}"
        return httpx.Response(
            200,
            json={
                "count": len(items),
                "next": next_url,
                "previous": None,
                "results": items[start : start + page_size],
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        listings = {
            "/api/tags/": self.tags,
            "/api/correspondents/": self.correspondents,
            "/api/document_types/": self.document_types,
            "/api/custom_fields/": self.custom_fields,
            "/api/storage_paths/": self.storage_paths,
            "/api/documents/": self.documents,
        }
        if path in listings:
            return self._page(request, listings[path])
        if match := re.fullmatch(r"/api/documents/(\d+)/metadata/", path):
            doc_id = int(match.group(1))
            return httpx.Response(
                200,
                json={
                    "original_checksum": hashlib.md5(self.originals[doc_id]).hexdigest(),
                    "has_archive_version": False,
                },
            )
        if match := re.fullmatch(r"/api/documents/(\d+)/download/", path):
            doc_id = int(match.group(1))
            assert request.url.params.get("original") == "true"
            self.download_counts[doc_id] += 1
            content = self.originals[doc_id]
            if self.corrupt_remaining.get(doc_id, 0) > 0:
                self.corrupt_remaining[doc_id] -= 1
                content = content + b"\nCORRUPTED"
            return httpx.Response(200, content=content, headers={"Content-Type": "application/pdf"})
        return httpx.Response(404)


# ---------------------------------------------------------------------------
# Settings


def test_paperless_settings_defaults() -> None:
    settings = Settings()
    assert settings.paperless_url is None  # feature unused by default
    assert settings.paperless_token is None


def test_paperless_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_PAPERLESS_URL", "http://paperless.lan:8000")
    monkeypatch.setenv("LIBRARY_PAPERLESS_TOKEN", "secret-token")
    settings = Settings()
    assert settings.paperless_url == "http://paperless.lan:8000"
    assert settings.paperless_token is not None
    assert settings.paperless_token.get_secret_value() == "secret-token"


# ---------------------------------------------------------------------------
# Mapper (pure functions, no HTTP / no database)


def test_parse_monetary_variants() -> None:
    assert parse_monetary("EUR123.45") == (Decimal("123.45"), "EUR")
    assert parse_monetary("eur 99,50") == (Decimal("99.50"), "EUR")
    assert parse_monetary("123.45") == (Decimal("123.45"), None)
    assert parse_monetary("USD-12.30") == (Decimal("-12.30"), "USD")
    assert parse_monetary(15) == (Decimal("15"), None)
    assert parse_monetary("not money") is None
    assert parse_monetary(None) is None
    assert parse_monetary(True) is None


def test_parse_created_accepts_date_and_datetime() -> None:
    assert parse_created("2024-03-15") == date(2024, 3, 15)  # API v9: plain date
    assert parse_created("2024-03-15T10:30:00+01:00") == date(2024, 3, 15)
    assert parse_created(None) is None
    assert parse_created("garbage") is None


def test_map_document_mapping_rules() -> None:
    taxonomies = Taxonomies.from_lists(
        tags=[
            {"id": 1, "name": "Taxes 2024", "is_inbox_tag": False},
            {"id": 2, "name": "Inbox", "is_inbox_tag": True},
        ],
        correspondents=[{"id": 7, "name": "Eneco"}],
        document_types=[{"id": 3, "name": "Weird Type"}],
        custom_fields=[
            {
                "id": 1,
                "name": "Amount",
                "data_type": "monetary",
                "extra_data": {"default_currency": "eur"},
            },
            {
                "id": 2,
                "name": "Status",
                "data_type": "select",
                "extra_data": {"select_options": [{"id": "opt-paid", "label": "Paid"}]},
            },
            {"id": 3, "name": "Related", "data_type": "documentlink", "extra_data": {}},
        ],
    )
    mapped = map_document(
        {
            "id": 41,
            "title": "  Energy invoice ",
            "created": "2024-03-15",
            "content": "factuurtekst",
            "mime_type": "application/pdf",
            "original_file_name": "invoice.pdf",
            "correspondent": 7,
            "document_type": 3,
            "tags": [1, 2],
            "custom_fields": [
                {"field": 1, "value": "123.45"},  # bare number -> default currency
                {"field": 2, "value": "opt-paid"},
                {"field": 3, "value": [40]},
            ],
            "archive_serial_number": 42,
        },
        taxonomies,
    )
    assert mapped.title == "Energy invoice"
    assert mapped.document_date == date(2024, 3, 15)
    assert mapped.sender_name == "Eneco"
    # Unmapped document type: falls back to "other" + provenance tag.
    assert mapped.kind_slug == "other"
    slugs = [tag.slug for tag in mapped.tags]
    assert slugs == ["taxes-2024", "inbox", "needs-review", "paperless-weird-type"]
    assert mapped.amount_total == Decimal("123.45")
    assert mapped.currency == "EUR"  # from the field's default_currency
    assert mapped.extra["custom_fields"]["Status"] == "Paid"
    assert mapped.extra["custom_fields"]["Related"] == [40]
    assert mapped.linked_document_ids == {"Related": [40]}
    assert mapped.extra["asn"] == 42


def test_map_document_known_dutch_type_maps_to_kind() -> None:
    taxonomies = Taxonomies.from_lists(
        tags=[], correspondents=[], document_types=[{"id": 3, "name": "Factuur"}], custom_fields=[]
    )
    mapped = map_document({"id": 1, "document_type": 3}, taxonomies)
    assert mapped.kind_slug == "invoice"
    assert mapped.tags == []  # no provenance tag for a mapped type


def _storage_path_taxonomies() -> Taxonomies:
    return Taxonomies.from_lists(
        tags=[],
        correspondents=[],
        document_types=[],
        custom_fields=[],
        storage_paths=[{"id": 5, "name": "Atlas Consulting Expenses"}],
    )


def test_map_document_storage_path_becomes_plain_tag_and_extra() -> None:
    mapped = map_document({"id": 1, "storage_path": 5}, _storage_path_taxonomies())
    # Plain slug, no `paperless:` prefix (matches the manual backfill).
    assert mapped.tags == [
        TagSpec(slug="atlas-consulting-expenses", name="Atlas Consulting Expenses")
    ]
    assert mapped.storage_path_name == "Atlas Consulting Expenses"
    assert mapped.extra["storage_path"] == "Atlas Consulting Expenses"


def test_map_document_without_storage_path_adds_nothing() -> None:
    for doc in ({"id": 1}, {"id": 1, "storage_path": None}):
        mapped = map_document(doc, _storage_path_taxonomies())
        assert mapped.tags == []
        assert mapped.storage_path_name is None
        assert "storage_path" not in mapped.extra  # no null written


def test_map_document_unknown_storage_path_id_is_skipped() -> None:
    # Stale foreign key (storage path deleted between fetches): no crash,
    # no tag, no extra entry.
    mapped = map_document({"id": 1, "storage_path": 999}, _storage_path_taxonomies())
    assert mapped.tags == []
    assert mapped.storage_path_name is None
    assert "storage_path" not in mapped.extra


# ---------------------------------------------------------------------------
# Client (MockTransport, no database)


async def test_client_pagination_headers_and_auth() -> None:
    fake = FakePaperless()
    ids = [fake.add_document(make_pdf())["id"] for _ in range(5)]
    async with fake.client() as client:
        seen = [doc["id"] async for doc in client.iter_documents(page_size=2)]
    assert seen == ids
    list_requests = [r for r in fake.requests if r.url.path == "/api/documents/"]
    assert len(list_requests) == 3  # 5 documents / page_size 2
    for request in list_requests:
        assert request.headers["Authorization"] == "Token test-token"
        assert request.headers["Accept"] == "application/json; version=9"


async def test_client_md5_mismatch_retried_once_then_raises() -> None:
    fake = FakePaperless()
    doc = fake.add_document(make_pdf())
    fake.corrupt_remaining[doc["id"]] = 1  # transient: first download corrupted
    async with fake.client() as client:
        content = await client.download_original_verified(doc["id"])
    assert content == fake.originals[doc["id"]]
    assert fake.download_counts[doc["id"]] == 2

    bad = fake.add_document(make_pdf())
    fake.corrupt_remaining[bad["id"]] = 99  # persistent corruption
    async with fake.client() as client:
        with pytest.raises(ChecksumMismatchError):
            await client.download_original_verified(bad["id"])
    assert fake.download_counts[bad["id"]] == 2  # exactly one retry


# ---------------------------------------------------------------------------
# Runner (real testcontainers database)


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point LIBRARY_DATA_DIR at tmp_path so stored originals stay local."""
    target = tmp_path / "data"
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(target))
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


async def do_import(
    session_factory: async_sessionmaker[AsyncSession], fake: FakePaperless, **kwargs: Any
) -> ImportReport:
    async with fake.client() as client, session_factory() as session:
        return await run_import(session, client, **kwargs)


async def document_by_paperless_id(
    session_factory: async_sessionmaker[AsyncSession], paperless_id: int
) -> Document | None:
    async with session_factory() as session:
        return (
            await session.execute(select(Document).where(Document.paperless_id == paperless_id))
        ).scalar_one_or_none()


async def events_for(
    session_factory: async_sessionmaker[AsyncSession], document_id: int, event: str
) -> list[IngestionEvent]:
    async with session_factory() as session:
        result = await session.execute(
            select(IngestionEvent).where(
                IngestionEvent.document_id == document_id, IngestionEvent.event == event
            )
        )
        return list(result.scalars().all())


def jobs_named(connector: InMemoryConnector, task_name: str, document_id: int) -> list[Any]:
    return [
        job
        for job in connector.jobs.values()
        if job["task_name"] == task_name and job["args"] == {"document_id": document_id}
    ]


@pytest.mark.integration
async def test_import_maps_metadata_reuses_content_and_remaps_links(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    fake.tags = [
        {"id": 1, "name": "Taxes 2024", "is_inbox_tag": False},
        {"id": 2, "name": "Inbox", "is_inbox_tag": True},
    ]
    fake.correspondents = [{"id": 7, "name": "Eneco"}]
    fake.document_types = [{"id": 3, "name": "Factuur"}]
    fake.storage_paths = [{"id": 4, "name": "Family"}]
    fake.custom_fields = [
        {"id": 1, "name": "Amount", "data_type": "monetary", "extra_data": {}},
        {
            "id": 2,
            "name": "Status",
            "data_type": "select",
            "extra_data": {"select_options": [{"id": "opt-paid", "label": "Paid"}]},
        },
        {"id": 3, "name": "Related", "data_type": "documentlink", "extra_data": {}},
    ]
    target = fake.add_document(make_pdf(), title="Warranty card")
    main = fake.add_document(
        make_pdf(),
        title="Energy invoice",
        created="2024-03-15",
        content="Geachte heer, uw factuur voor maart.",
        correspondent=7,
        document_type=3,
        storage_path=4,
        tags=[1, 2],
        custom_fields=[
            {"field": 1, "value": "EUR123.45"},
            {"field": 2, "value": "opt-paid"},
            {"field": 3, "value": [target["id"]]},
        ],
        added="2024-03-16T08:00:00Z",
        archive_serial_number=42,
        notes=[{"note": "paid in april", "created": "2024-04-01T00:00:00Z"}],
    )

    report = await do_import(session_factory, fake)

    assert report.imported == 2
    assert report.failed == []
    document = await document_by_paperless_id(session_factory, main["id"])
    assert document is not None
    assert document.source is DocumentSource.IMPORT
    assert document.title == "Energy invoice"
    assert document.document_date == date(2024, 3, 15)
    assert document.kind is not None and document.kind.slug == "invoice"
    assert document.sender is not None and document.sender.name == "Eneco"
    # Storage path -> plain (unprefixed) tag, alongside the paperless tags.
    assert {tag.slug for tag in document.tags} == {"taxes-2024", "inbox", "needs-review", "family"}
    family_tag = next(tag for tag in document.tags if tag.slug == "family")
    assert family_tag.name == "Family"
    assert document.amount_total == Decimal("123.45")
    assert document.currency == "EUR"
    # paperless OCR text is reused: immediately indexed, no OCR job.
    assert document.ocr_text == "Geachte heer, uw factuur voor maart."
    assert document.status is DocumentStatus.INDEXED
    ocr_events = await events_for(session_factory, document.id, "ocr_completed")
    assert [event.detail["engine"] for event in ocr_events] == ["paperless-import"]
    imported_events = await events_for(session_factory, document.id, "paperless_imported")
    assert len(imported_events) == 1
    assert imported_events[0].detail["batch_id"] == report.batch_id

    paperless_extra = document.extra["paperless"]
    assert paperless_extra["batch_id"] == report.batch_id
    assert paperless_extra["asn"] == 42
    assert paperless_extra["storage_path"] == "Family"
    assert report.storage_path_counts == Counter({"Family": 1, "(none)": 1})
    assert paperless_extra["custom_fields"]["Status"] == "Paid"
    assert paperless_extra["custom_fields"]["Amount"] == "EUR123.45"
    assert paperless_extra["notes"] == [
        {"note": "paid in april", "created": "2024-04-01T00:00:00Z"}
    ]
    # documentlink remapped to Library ids in the second pass.
    linked_doc = await document_by_paperless_id(session_factory, target["id"])
    assert linked_doc is not None
    assert paperless_extra["linked_documents"] == {
        "Related": [{"paperless_id": target["id"], "document_id": linked_doc.id}]
    }
    # Migrated values are protected from later extraction overwrites.
    assert set(document.extra["user_edited_fields"]) >= {
        "title",
        "document_date",
        "kind_id",
        "sender_id",
        "amount_total",
        "currency",
    }
    # Jobs: extraction deferred as enrichment + thumbnail; no pipeline job.
    assert len(jobs_named(job_connector, "library.jobs.extract_document", document.id)) == 1
    assert len(jobs_named(job_connector, "library.jobs.generate_thumbnail", document.id)) == 1
    assert jobs_named(job_connector, "library.jobs.process_document", document.id) == []
    # The link target has no paperless content: full pipeline instead.
    assert len(jobs_named(job_connector, "library.jobs.process_document", linked_doc.id)) == 1
    assert linked_doc.status is DocumentStatus.RECEIVED
    assert linked_doc.ocr_text is None


@pytest.mark.integration
async def test_null_correspondent_and_type_and_no_archive_imports_cleanly(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    # No taxonomies at all; metadata/ already reports has_archive_version False.
    doc = fake.add_document(make_pdf(), title="Mystery scan", content="some text")

    report = await do_import(session_factory, fake)

    assert report.imported == 1
    assert report.failed == []
    document = await document_by_paperless_id(session_factory, doc["id"])
    assert document is not None
    assert document.kind is None  # left for extraction to classify
    assert document.sender is None
    assert document.tags == []
    assert document.status is DocumentStatus.INDEXED


@pytest.mark.integration
async def test_trashed_documents_are_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    kept = fake.add_document(make_pdf(), content="keep me")
    trashed = fake.add_document(make_pdf(), content="bin me", deleted_at="2024-05-01T00:00:00Z")

    report = await do_import(session_factory, fake)

    assert report.total_seen == 2
    assert report.imported == 1
    assert report.skipped_trashed == 1
    assert await document_by_paperless_id(session_factory, kept["id"]) is not None
    assert await document_by_paperless_id(session_factory, trashed["id"]) is None
    assert fake.download_counts[trashed["id"]] == 0


@pytest.mark.integration
async def test_checksum_failure_is_recorded_and_run_continues(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    bad = fake.add_document(make_pdf(), content="corrupted forever")
    good = fake.add_document(make_pdf(), content="fine")
    fake.corrupt_remaining[bad["id"]] = 99

    report = await do_import(session_factory, fake)

    assert report.imported == 1
    assert len(report.failed) == 1
    assert report.failed[0].paperless_id == bad["id"]
    assert report.failed[0].reason.startswith("checksum_mismatch")
    assert fake.download_counts[bad["id"]] == 2  # one retry, then recorded
    assert await document_by_paperless_id(session_factory, bad["id"]) is None
    assert await document_by_paperless_id(session_factory, good["id"]) is not None
    # The failure is visible in the human-readable report too.
    assert f"paperless #{bad['id']}" in format_report(report)


@pytest.mark.integration
async def test_double_run_is_idempotent_by_paperless_id(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    docs = [fake.add_document(make_pdf(), content=f"text {i}") for i in range(2)]

    first = await do_import(session_factory, fake)
    second = await do_import(session_factory, fake)

    assert first.imported == 2
    assert second.imported == 0
    assert second.skipped_duplicate == 2
    # The second run skipped before downloading anything.
    for doc in docs:
        assert fake.download_counts[doc["id"]] == 1


@pytest.mark.integration
async def test_content_duplicate_is_linked_and_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    content = make_pdf()
    async with session_factory() as session:
        result = await ingest_file(
            session,
            content=content,
            filename="already-uploaded.pdf",
            source=DocumentSource.UPLOAD,
        )
    existing_id = result.document.id

    fake = FakePaperless()
    doc = fake.add_document(content, title="Same bytes")

    first = await do_import(session_factory, fake)
    assert first.imported == 0
    assert first.skipped_duplicate == 1
    # The existing upload is linked to the paperless id (sha256 key) ...
    document = await document_by_paperless_id(session_factory, doc["id"])
    assert document is not None
    assert document.id == existing_id
    assert document.title is None  # untouched: it was not imported
    assert fake.download_counts[doc["id"]] == 1

    # ... so the next run skips it by paperless id, before downloading.
    second = await do_import(session_factory, fake)
    assert second.skipped_duplicate == 1
    assert fake.download_counts[doc["id"]] == 1


@pytest.mark.integration
async def test_interrupted_import_is_resumed_on_rerun(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    """A doc whose first import died between ingest and metadata is finished."""
    content = make_pdf()
    async with session_factory() as session:
        result = await ingest_file(
            session,
            content=content,
            filename="partial.pdf",
            source=DocumentSource.IMPORT,
            defer_processing=False,
        )
    partial_id = result.document.id

    fake = FakePaperless()
    doc = fake.add_document(content, title="Now complete", content="resumed text")

    report = await do_import(session_factory, fake)

    assert report.imported == 1
    document = await document_by_paperless_id(session_factory, doc["id"])
    assert document is not None
    assert document.id == partial_id  # no second row for the same bytes
    assert document.title == "Now complete"
    assert document.ocr_text == "resumed text"
    assert document.status is DocumentStatus.INDEXED
    assert document.extra["paperless"]["batch_id"] == report.batch_id


@pytest.mark.integration
async def test_no_extract_suppresses_extraction_job(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    doc = fake.add_document(make_pdf(), content="text from paperless")

    report = await do_import(session_factory, fake, no_extract=True)

    assert report.imported == 1
    document = await document_by_paperless_id(session_factory, doc["id"])
    assert document is not None
    assert document.status is DocumentStatus.INDEXED
    assert jobs_named(job_connector, "library.jobs.extract_document", document.id) == []
    assert len(jobs_named(job_connector, "library.jobs.generate_thumbnail", document.id)) == 1


@pytest.mark.integration
async def test_dry_run_writes_nothing_and_reports_mapping(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    job_connector: InMemoryConnector,
) -> None:
    fake = FakePaperless()
    fake.document_types = [{"id": 1, "name": "Receipt"}]
    fake.storage_paths = [{"id": 2, "name": "Atlas Consulting Expenses"}]
    live = fake.add_document(make_pdf(), title="Live", document_type=1, storage_path=2, content="x")
    fake.add_document(make_pdf(), deleted_at="2024-05-01T00:00:00Z")

    report = await do_import(session_factory, fake, dry_run=True)

    assert report.dry_run is True
    assert report.total_seen == 2
    assert report.skipped_trashed == 1
    assert report.imported == 0
    assert report.kind_counts["receipt"] == 1
    assert report.storage_path_counts == Counter({"Atlas Consulting Expenses": 1})
    # Zero writes: no documents, no downloads, no jobs.
    assert await document_by_paperless_id(session_factory, live["id"]) is None
    assert sum(fake.download_counts.values()) == 0
    assert job_connector.jobs == {}
    summary = format_report(report)
    assert "dry run" in summary
    assert "would import:       1" in summary
    assert "receipt: 1" in summary
    assert "storage paths:" in summary
    assert "Atlas Consulting Expenses: 1" in summary

    # After a real import, a dry run detects the existing documents.
    await do_import(session_factory, fake)
    recheck = await do_import(session_factory, fake, dry_run=True)
    assert recheck.skipped_duplicate == 1


# ---------------------------------------------------------------------------
# CLI (runner mocked; no paperless, no database writes)

runner = CliRunner()


def _invoke_with_mocked_runner(
    monkeypatch: pytest.MonkeyPatch, args: list[str], report: ImportReport
) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def fake_run_import(
        session: AsyncSession, client: PaperlessClient, **kwargs: Any
    ) -> ImportReport:
        captured.update(kwargs)
        return report

    monkeypatch.setattr("library.cli.run_import", fake_run_import)
    with job_app.replace_connector(InMemoryConnector()):
        result = runner.invoke(cli_app, args)
    return result, captured


def test_cli_import_paperless_passes_flags_and_prints_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = ImportReport(batch_id="batch-1", dry_run=True, total_seen=3)
    result, captured = _invoke_with_mocked_runner(
        monkeypatch,
        [
            "import",
            "paperless",
            "--url",
            "http://paperless.test",
            "--token",
            "tok",
            "--dry-run",
            "--limit",
            "3",
        ],
        report,
    )
    assert result.exit_code == 0, result.output
    assert captured == {"dry_run": True, "no_extract": False, "limit": 3}
    assert "dry run" in result.output
    assert "documents seen:     3" in result.output


def test_cli_import_paperless_uses_env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBRARY_PAPERLESS_URL", "http://paperless.env")
    monkeypatch.setenv("LIBRARY_PAPERLESS_TOKEN", "env-token")
    get_settings.cache_clear()
    try:
        report = ImportReport(batch_id="batch-2", dry_run=False)
        result, captured = _invoke_with_mocked_runner(
            monkeypatch, ["import", "paperless", "--no-extract"], report
        )
        assert result.exit_code == 0, result.output
        assert captured == {"dry_run": False, "no_extract": True, "limit": None}
    finally:
        get_settings.cache_clear()


def test_cli_import_paperless_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIBRARY_PAPERLESS_URL", raising=False)
    monkeypatch.delenv("LIBRARY_PAPERLESS_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        result = runner.invoke(cli_app, ["import", "paperless"])
        assert result.exit_code == 1
        assert "paperless URL and token required" in result.output
    finally:
        get_settings.cache_clear()


def test_cli_import_paperless_exits_nonzero_on_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = ImportReport(
        batch_id="batch-3",
        dry_run=False,
        total_seen=1,
        failed=[ImportFailure(9, "checksum_mismatch: boom")],
    )
    result, _ = _invoke_with_mocked_runner(
        monkeypatch,
        ["import", "paperless", "--url", "http://paperless.test", "--token", "tok"],
        report,
    )
    assert result.exit_code == 1
    assert "paperless #9: checksum_mismatch: boom" in result.output
