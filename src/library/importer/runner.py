"""Batch import from a paperless-ngx instance.

Per-document flow: skip trashed (``deleted_at``), skip documents already
imported (by ``paperless_id`` or by content sha256 — re-runs are
idempotent), download the bit-exact original with MD5 verification, then
register it through the shared ``ingest_file`` entry point with
``source=import``.

Pipeline entry: paperless documents arrive with paperless's own OCR text
(``content``). When present it is reused directly (``ocr_text``, audit
event engine ``paperless-import``), the document goes straight to
``indexed`` (immediately searchable), and Claude extraction is queued as
enrichment via the standalone ``extract_document`` task (the same one used
for manual re-extraction; ``--no-extract`` skips it). Documents without
content take the normal ``process_document`` pipeline (OCR + extract).

Fields curated in paperless (title, date, kind, sender, amount) are
recorded in ``extra["user_edited_fields"]`` so a later extraction fills
gaps rather than overwriting the migrated values.

Every imported document carries ``extra["paperless"]["batch_id"]`` (one
uuid per run) and a ``paperless_imported`` ingestion event, so a batch can
be identified — and deleted — wholesale.
"""

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.extraction.apply import upsert_sender
from library.importer.client import ChecksumMismatchError, PaperlessClient
from library.importer.mapper import MappedDocument, Taxonomies, map_document
from library.ingest import ingest_file
from library.jobs import extract_document, generate_thumbnail, process_document
from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    Kind,
    Tag,
)

logger = logging.getLogger(__name__)

DOWNLOAD_CONCURRENCY: int = 4
# Documents per download wave; > concurrency so the semaphore stays busy.
BATCH_SIZE: int = 8
PROGRESS_EVERY: int = 25


@dataclass(frozen=True)
class ImportFailure:
    """One document that could not be imported; the run continues past it."""

    paperless_id: int
    reason: str


@dataclass
class ImportReport:
    """Outcome summary of one import run (also the dry-run report)."""

    batch_id: str
    dry_run: bool
    total_seen: int = 0
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_trashed: int = 0
    failed: list[ImportFailure] = field(default_factory=list)
    kind_counts: Counter[str] = field(default_factory=Counter)
    sender_counts: Counter[str] = field(default_factory=Counter)
    storage_path_counts: Counter[str] = field(default_factory=Counter)
    tag_counts: Counter[str] = field(default_factory=Counter)


def format_report(report: ImportReport) -> str:
    """Human-readable summary for the CLI."""
    lines: list[str] = []
    mode = "dry run — nothing was written" if report.dry_run else f"batch {report.batch_id}"
    lines.append(f"paperless import ({mode})")
    lines.append(f"  documents seen:     {report.total_seen}")
    if report.dry_run:
        importable = report.total_seen - report.skipped_trashed - report.skipped_duplicate
        lines.append(f"  would import:       {importable}")
    else:
        lines.append(f"  imported:           {report.imported}")
    lines.append(f"  skipped (duplicate): {report.skipped_duplicate}")
    lines.append(f"  skipped (trashed):   {report.skipped_trashed}")
    lines.append(f"  failed:             {len(report.failed)}")
    for failure in report.failed:
        lines.append(f"    - paperless #{failure.paperless_id}: {failure.reason}")
    for label, counter in (
        ("kinds", report.kind_counts),
        ("senders", report.sender_counts),
        ("storage paths", report.storage_path_counts),
        ("tags", report.tag_counts),
    ):
        if counter:
            lines.append(f"  {label}:")
            for name, count in counter.most_common():
                lines.append(f"    {name}: {count}")
    return "\n".join(lines)


async def _get_or_create_tag(session: AsyncSession, slug: str, name: str) -> Tag:
    """Like extraction's get_or_create_tag, but keeps paperless display names."""
    existing = (await session.execute(select(Tag).where(Tag.slug == slug))).scalar_one_or_none()
    if existing is not None:
        return existing
    tag = Tag(slug=slug, name=name)
    session.add(tag)
    await session.flush()
    return tag


async def _existing_by_paperless_id(session: AsyncSession, paperless_id: int) -> int | None:
    return (
        await session.execute(select(Document.id).where(Document.paperless_id == paperless_id))
    ).scalar_one_or_none()


def _tally(report: ImportReport, mapped: MappedDocument) -> None:
    report.kind_counts[mapped.kind_slug or "(none)"] += 1
    report.sender_counts[mapped.sender_name or "(none)"] += 1
    report.storage_path_counts[mapped.storage_path_name or "(none)"] += 1
    for tag in mapped.tags:
        report.tag_counts[tag.slug] += 1


async def _apply_metadata(
    session: AsyncSession, document: Document, mapped: MappedDocument, batch_id: str
) -> None:
    """Write the mapped paperless metadata onto a freshly ingested document."""
    # Load the tags collection explicitly: it is unloaded on a freshly
    # flushed row, and implicit lazy loads raise under the async session.
    await session.refresh(document, attribute_names=["tags"])
    protected: list[str] = []
    document.paperless_id = mapped.paperless_id
    if mapped.title is not None:
        document.title = mapped.title
        protected.append("title")
    if mapped.document_date is not None:
        document.document_date = mapped.document_date
        protected.append("document_date")
    if mapped.amount_total is not None and document.amount_total is None:
        document.amount_total = mapped.amount_total
        protected.append("amount_total")
        if mapped.currency is not None:
            document.currency = mapped.currency
            protected.append("currency")
    if mapped.kind_slug is not None:
        kind = (
            await session.execute(select(Kind).where(Kind.slug == mapped.kind_slug))
        ).scalar_one_or_none()
        if kind is not None:
            document.kind_id = kind.id
            protected.append("kind_id")
    if mapped.sender_name is not None:
        document.sender_id = (await upsert_sender(session, mapped.sender_name)).id
        protected.append("sender_id")
    existing_slugs = {tag.slug for tag in document.tags}
    for spec in mapped.tags:
        if spec.slug not in existing_slugs:
            document.tags.append(await _get_or_create_tag(session, spec.slug, spec.name))
            existing_slugs.add(spec.slug)

    # `extra` has only a server-side default, so a freshly flushed row holds
    # None until refreshed (sessions run expire_on_commit=False).
    existing_extra = document.extra or {}
    user_edited = set(existing_extra.get("user_edited_fields", []))
    document.extra = {
        **existing_extra,
        "user_edited_fields": sorted(user_edited | set(protected)),
        "paperless": {**mapped.extra, "batch_id": batch_id},
    }


async def _import_one(
    session: AsyncSession,
    mapped: MappedDocument,
    content: bytes,
    report: ImportReport,
    *,
    batch_id: str,
    no_extract: bool,
    default_owner_id: int | None = None,
) -> int | None:
    """Ingest one downloaded document; returns its Library id (also for dups)."""
    result = await ingest_file(
        session,
        content=content,
        filename=mapped.original_filename,
        mime=mapped.mime_type,
        source=DocumentSource.IMPORT,
        uploader_id=default_owner_id,
        defer_processing=False,
    )
    document = result.document
    if result.duplicate:
        # A document with `source=import` but no `extra["paperless"]` is an
        # interrupted earlier import (ingest committed, metadata commit never
        # happened): fall through and re-apply the metadata to finish it.
        resumable = (
            document.source is DocumentSource.IMPORT
            and "paperless" not in (document.extra or {})
            and document.paperless_id in (None, mapped.paperless_id)
        )
        if not resumable:
            # Same bytes already in the Library (e.g. also ingested via upload).
            # Link it to the paperless id so future runs skip before downloading.
            if document.paperless_id is None:
                document.paperless_id = mapped.paperless_id
                await session.commit()
            report.skipped_duplicate += 1
            logger.info(
                "paperless #%s is a content duplicate of document %s; skipped",
                mapped.paperless_id,
                document.id,
            )
            return document.id
        logger.info(
            "paperless #%s matches partially imported document %s; resuming",
            mapped.paperless_id,
            document.id,
        )

    await _apply_metadata(session, document, mapped, batch_id)
    session.add(
        IngestionEvent(
            document_id=document.id,
            event="paperless_imported",
            detail={
                "paperless_id": mapped.paperless_id,
                "batch_id": batch_id,
                "title": mapped.title,
            },
        )
    )

    if mapped.content is not None:
        # Reuse paperless's OCR text: no re-OCR, immediately searchable.
        document.ocr_text = mapped.content
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="ocr_completed",
                detail={"engine": "paperless-import", "characters": len(mapped.content)},
            )
        )
        previous = document.status
        document.status = DocumentStatus.INDEXED
        session.add(
            IngestionEvent(
                document_id=document.id,
                event="status_changed",
                detail={"from": previous.value, "to": document.status.value},
            )
        )
        # Commit before deferring (job rows are visible immediately).
        await session.commit()
        if not no_extract:
            await extract_document.defer_async(document_id=document.id)
        await generate_thumbnail.defer_async(document_id=document.id)
    else:
        # No usable text from paperless: run the full pipeline (OCR + extract;
        # the OCR stage also defers the thumbnail job).
        await session.commit()
        await process_document.defer_async(document_id=document.id)

    report.imported += 1
    return document.id


async def _download(
    client: PaperlessClient, semaphore: asyncio.Semaphore, paperless_id: int
) -> bytes:
    async with semaphore:
        return await client.download_original_verified(paperless_id)


def _failure_reason(error: BaseException) -> str:
    if isinstance(error, ChecksumMismatchError):
        return f"checksum_mismatch: {error}"
    if isinstance(error, httpx.HTTPError):
        return f"http_error: {error}"
    return f"{type(error).__name__}: {error}"


async def _process_batch(
    session: AsyncSession,
    client: PaperlessClient,
    semaphore: asyncio.Semaphore,
    batch: list[MappedDocument],
    report: ImportReport,
    id_map: dict[int, int],
    *,
    batch_id: str,
    no_extract: bool,
    default_owner_id: int | None = None,
) -> None:
    """Import one wave: skip known ids, download concurrently, ingest serially."""
    to_download: list[MappedDocument] = []
    for mapped in batch:
        existing_id = await _existing_by_paperless_id(session, mapped.paperless_id)
        if existing_id is not None:
            report.skipped_duplicate += 1
            id_map[mapped.paperless_id] = existing_id
            continue
        to_download.append(mapped)

    results = await asyncio.gather(
        *(_download(client, semaphore, mapped.paperless_id) for mapped in to_download),
        return_exceptions=True,
    )
    # The session is not safe for concurrent use, so ingestion is sequential.
    for mapped, result in zip(to_download, results, strict=True):
        if isinstance(result, BaseException):
            report.failed.append(ImportFailure(mapped.paperless_id, _failure_reason(result)))
            logger.error("paperless #%s failed: %s", mapped.paperless_id, result)
            continue
        try:
            document_id = await _import_one(
                session,
                mapped,
                result,
                report,
                batch_id=batch_id,
                no_extract=no_extract,
                default_owner_id=default_owner_id,
            )
        except Exception as exc:
            await session.rollback()
            report.failed.append(ImportFailure(mapped.paperless_id, _failure_reason(exc)))
            logger.exception("paperless #%s failed during ingest", mapped.paperless_id)
            continue
        if document_id is not None:
            id_map[mapped.paperless_id] = document_id


async def _remap_linked_documents(
    session: AsyncSession,
    links: list[tuple[int, dict[str, list[int]]]],
    id_map: dict[int, int],
) -> None:
    """Second pass: rewrite documentlink custom fields to Library document ids."""
    for document_id, fields in links.copy():
        document = await session.get(Document, document_id)
        if document is None:  # pragma: no cover - imported moments ago
            continue
        resolved: dict[str, list[dict[str, Any]]] = {}
        for field_name, paperless_ids in fields.items():
            entries: list[dict[str, Any]] = []
            for pid in paperless_ids:
                library_id = id_map.get(pid)
                if library_id is None:
                    library_id = await _existing_by_paperless_id(session, pid)
                entries.append({"paperless_id": pid, "document_id": library_id})
            resolved[field_name] = entries
        base_extra = document.extra or {}
        paperless_extra = {**base_extra.get("paperless", {}), "linked_documents": resolved}
        document.extra = {**base_extra, "paperless": paperless_extra}
    await session.commit()


async def run_import(
    session: AsyncSession,
    client: PaperlessClient,
    *,
    dry_run: bool = False,
    no_extract: bool = False,
    limit: int | None = None,
    progress_every: int = PROGRESS_EVERY,
    default_owner_id: int | None = None,
) -> ImportReport:
    """Import every (non-trashed) paperless document; returns the run report.

    ``dry_run`` fetches and maps everything but downloads nothing and
    writes nothing (the database is only read, for duplicate detection).
    ``limit`` caps the number of documents considered (useful for a first
    careful run against a live instance). ``default_owner_id`` (resolved from
    ``settings.import_default_owner`` by the caller) attributes imported
    documents to an owner so the owner-as-recipient fallback can fire.
    """
    report = ImportReport(batch_id=uuid.uuid4().hex, dry_run=dry_run)
    taxonomies = Taxonomies.from_lists(
        await client.list_tags(),
        await client.list_correspondents(),
        await client.list_document_types(),
        await client.list_custom_fields(),
        await client.list_storage_paths(),
    )
    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    id_map: dict[int, int] = {}  # paperless id -> Library document id
    links: list[tuple[int, dict[str, list[int]]]] = []
    pending: list[MappedDocument] = []

    async def flush_pending() -> None:
        if not pending:
            return
        before = dict(id_map)
        await _process_batch(
            session,
            client,
            semaphore,
            pending,
            report,
            id_map,
            batch_id=report.batch_id,
            no_extract=no_extract,
            default_owner_id=default_owner_id,
        )
        for mapped in pending:
            document_id = id_map.get(mapped.paperless_id)
            if (
                mapped.linked_document_ids
                and document_id is not None
                and mapped.paperless_id not in before
            ):
                links.append((document_id, mapped.linked_document_ids))
        pending.clear()

    async for raw in client.iter_documents():
        if limit is not None and report.total_seen >= limit:
            break
        report.total_seen += 1
        if raw.get("deleted_at"):
            report.skipped_trashed += 1
            continue
        mapped = map_document(raw, taxonomies)
        _tally(report, mapped)
        if dry_run:
            # Read-only duplicate check so "would import" is accurate.
            if await _existing_by_paperless_id(session, mapped.paperless_id) is not None:
                report.skipped_duplicate += 1
        else:
            pending.append(mapped)
            if len(pending) >= BATCH_SIZE:
                await flush_pending()
        if report.total_seen % progress_every == 0:
            logger.info(
                "paperless import progress: %s seen, %s imported, %s skipped, %s failed",
                report.total_seen,
                report.imported,
                report.skipped_duplicate + report.skipped_trashed,
                len(report.failed),
            )

    if not dry_run:
        await flush_pending()
        await _remap_linked_documents(session, links, id_map)

    logger.info(
        "paperless import finished (batch %s): %s seen, %s imported, "
        "%s duplicate, %s trashed, %s failed",
        report.batch_id,
        report.total_seen,
        report.imported,
        report.skipped_duplicate,
        report.skipped_trashed,
        len(report.failed),
    )
    return report
