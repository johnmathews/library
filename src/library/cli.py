"""`library` — administration CLI (typer): accounts and imports.

There is deliberately no signup endpoint: users of this family-scale
archive are created from the host. Each command opens a fresh async
engine (NullPool) and disposes it, so repeated invocations never share a
loop-bound connection.
"""

import asyncio
import hashlib
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import typer
from anthropic import AsyncAnthropic
from sqlalchemy import BigInteger, Select, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.auth.passwords import hash_password
from library.auth.service import revoke_all_credentials
from library.config import get_settings
from library.extraction.apply import get_or_create_user_recipient
from library.extraction.eval import (
    combine,
    flywheel_accuracy,
    judge_agreement,
    modal_version,
    version_distribution,
)
from library.extraction.extractor import PROMPT_VERSION
from library.extraction.judge import JudgeResult, judge
from library.extraction.validation import derive_review_status, findings_to_payload, validate
from library.importer.client import PaperlessClient
from library.importer.runner import ImportReport, format_report, run_import
from library.ingest import resolve_owner_id
from library.jobs import (
    classify_document_matters,
    embed_document,
    extract_document,
    job_app,
    markdown_document,
    process_document,
)
from library.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    EvalRun,
    IngestionEvent,
    Kind,
    Sender,
    User,
)
from library.pdf_unlock import PdfLockedError, unlock_pdf
from library.storage import path_for, remove, store

app: typer.Typer = typer.Typer(
    no_args_is_help=True, help="Library administration (accounts, imports)."
)
user_app: typer.Typer = typer.Typer(no_args_is_help=True, help="Manage user accounts.")
app.add_typer(user_app, name="user")
import_app: typer.Typer = typer.Typer(
    no_args_is_help=True, help="Import documents from external systems."
)
app.add_typer(import_app, name="import")


def _run[T](operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run one database operation on a fresh engine, then dispose it."""

    async def _execute() -> T:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await operation(session)
        finally:
            await engine.dispose()

    return asyncio.run(_execute())


def _read_password(*, password_stdin: bool, confirm: bool = True) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=confirm)
    if not password:
        typer.echo("error: empty password")
        raise typer.Exit(code=1)
    return password


async def _get_user(session: AsyncSession, username: str) -> User:
    user = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if user is None:
        typer.echo(f"error: no such user: {username}")
        raise typer.Exit(code=1)
    return user


@user_app.command("add")
def user_add(
    username: str,
    display_name: str = typer.Option("", "--display-name", help="Human-readable name."),
    admin: bool = typer.Option(False, "--admin", help="Grant admin privileges."),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from stdin instead of prompting."
    ),
) -> None:
    """Create a user (prompts for a password unless --password-stdin)."""

    async def operation(session: AsyncSession) -> None:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            typer.echo(f"error: user already exists: {username}")
            raise typer.Exit(code=1)
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            is_admin=admin,
        )
        session.add(user)
        # Auto-link a recipient (named by display name, else username) so
        # documents addressed to this person resolve to it — mirrors create_user.
        await session.flush()
        await get_or_create_user_recipient(session, user)
        await session.commit()

    password = _read_password(password_stdin=password_stdin)
    _run(operation)
    role = "admin user" if admin else "user"
    typer.echo(f"created {role} {username}")


@user_app.command("set-admin")
def user_set_admin(
    username: str,
    revoke: bool = typer.Option(
        False, "--revoke", help="Remove admin privileges instead of granting them."
    ),
) -> None:
    """Grant (or, with --revoke, remove) admin privileges for a user."""

    async def operation(session: AsyncSession) -> None:
        user = await _get_user(session, username)
        user.is_admin = not revoke
        await session.commit()

    _run(operation)
    verb = "revoked admin from" if revoke else "granted admin to"
    typer.echo(f"{verb} {username}")


@user_app.command("passwd")
def user_passwd(
    username: str,
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from stdin instead of prompting."
    ),
) -> None:
    """Set a new password for a user."""

    async def operation(session: AsyncSession) -> None:
        user = await _get_user(session, username)
        user.password_hash = hash_password(password)
        await session.commit()

    password = _read_password(password_stdin=password_stdin)
    _run(operation)
    typer.echo(f"password updated for {username}")


@user_app.command("disable")
def user_disable(username: str) -> None:
    """Deactivate a user and revoke all their sessions and API tokens."""

    async def operation(session: AsyncSession) -> None:
        user = await _get_user(session, username)
        user.is_active = False
        await session.commit()
        await revoke_all_credentials(session, user.id)

    _run(operation)
    typer.echo(f"disabled user {username} (sessions and tokens revoked)")


@user_app.command("list")
def user_list() -> None:
    """List all users."""

    async def operation(session: AsyncSession) -> list[User]:
        return list((await session.execute(select(User).order_by(User.id))).scalars().all())

    for user in _run(operation):
        state = "active" if user.is_active else "disabled"
        role = "admin" if user.is_admin else "user"
        display = f" ({user.display_name})" if user.display_name else ""
        typer.echo(
            f"{user.id}\t{user.username}{display}\t{role}\t{state}"
            f"\tcreated {user.created_at:%Y-%m-%d}"
        )


@import_app.command("paperless")
def import_paperless(
    url: str | None = typer.Option(
        None, "--url", help="paperless-ngx base URL (default: $LIBRARY_PAPERLESS_URL)."
    ),
    token: str | None = typer.Option(
        None, "--token", help="paperless-ngx API token (default: $LIBRARY_PAPERLESS_TOKEN)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch and map everything; write nothing, print the summary."
    ),
    no_extract: bool = typer.Option(
        False, "--no-extract", help="Do not queue Claude extraction for imported documents."
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only consider the first N documents."
    ),
) -> None:
    """Import every document from a paperless-ngx instance (see docs/migration.md).

    Idempotent: re-runs skip documents already imported (by paperless id or
    by content hash). Run with --dry-run first.
    """
    settings = get_settings()
    base_url = url or settings.paperless_url
    secret = token or (
        settings.paperless_token.get_secret_value() if settings.paperless_token else None
    )
    if not base_url or not secret:
        typer.echo(
            "error: paperless URL and token required "
            "(--url/--token or LIBRARY_PAPERLESS_URL/LIBRARY_PAPERLESS_TOKEN)"
        )
        raise typer.Exit(code=1)

    async def operation(session: AsyncSession) -> ImportReport:
        # Deferring follow-up jobs (extraction, thumbnails, pipeline) needs
        # the Procrastinate app open. Attribute imported docs to the configured
        # default owner so the owner-as-recipient fallback can fire.
        default_owner_id = await resolve_owner_id(session, settings.import_default_owner)
        async with job_app.open_async(), PaperlessClient(base_url, secret) as client:
            return await run_import(
                session,
                client,
                dry_run=dry_run,
                no_extract=no_extract,
                limit=limit,
                default_owner_id=default_owner_id,
            )

    report = _run(operation)
    typer.echo(format_report(report))
    if report.failed:
        raise typer.Exit(code=1)


# General document kinds (KIND_SLUGS subset) that carry free-form prose worth
# re-extracting under a new prompt. Restricting to these is the safe default so
# transactional kinds (invoices, receipts, ...) are never re-paid for.
GENERAL_KIND_SLUGS: frozenset[str] = frozenset({"manual", "reference", "research", "note"})


@app.command("backfill")
def backfill(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N matching documents (by id)."
    ),
    general_only: bool = typer.Option(
        True,
        "--general-only/--all-kinds",
        help=(
            "Restrict to general kinds (manual, reference, research, note) so "
            "transactional documents like invoices are never re-extracted. "
            "Pass --all-kinds to consider every kind."
        ),
    ),
    kinds: str | None = typer.Option(
        None,
        "--kinds",
        help=(
            "Comma-separated kind slugs to restrict to (e.g. 'letter,invoice,"
            "receipt' to backfill recipient-bearing documents first). Overrides "
            "--general-only/--all-kinds."
        ),
    ),
    include_current: bool = typer.Option(
        False,
        "--include-current",
        help=(
            "Re-enqueue documents already at the current prompt version too "
            "(e.g. to pick up markdown-chunking changes)."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print how many documents would be enqueued (with scope); enqueue nothing.",
    ),
) -> None:
    """Re-run extract -> markdown -> embed for documents on an old extraction prompt.

    Targets documents whose extraction ``prompt_version`` is missing or differs
    from the current ``PROMPT_VERSION`` so they pick up the latest prompt,
    long-doc sampling, ``topics``, and structure-preserving markdown chunking.
    By default only general kinds (manual, reference, research, note) are
    considered so invoices and receipts are never re-extracted; pass
    ``--all-kinds`` to include everything, or ``--include-current`` to re-enqueue
    regardless of prompt version.

    Re-runs the same path new uploads use (``extract_document`` then
    ``markdown_document``, which re-embeds), so it honours
    ``extra["user_edited_fields"]`` and respects the daily extraction budget —
    documents beyond the budget are skipped worker-side and can be re-queued the
    next day. Use ``--limit`` to throttle per run. The worker must be running to
    do the work; this command only enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = select(Document.id).where(Document.deleted_at.is_(None))
        if not include_current:
            prompt_version = Document.extra["extraction"]["prompt_version"].astext
            statement = statement.where(
                or_(prompt_version.is_(None), prompt_version != PROMPT_VERSION)
            )
        if kinds:
            slugs = [slug.strip() for slug in kinds.split(",") if slug.strip()]
            statement = statement.where(
                Document.kind_id.in_(select(Kind.id).where(Kind.slug.in_(slugs)))
            )
        elif general_only:
            statement = statement.where(
                Document.kind_id.in_(select(Kind.id).where(Kind.slug.in_(GENERAL_KIND_SLUGS)))
            )
        statement = statement.order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        if dry_run:
            return len(document_ids)
        async with job_app.open_async():
            for document_id in document_ids:
                await extract_document.defer_async(document_id=document_id)
                await markdown_document.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    if dry_run:
        if kinds:
            kinds_scope = f"kinds {kinds}"
        else:
            kinds_scope = "general kinds" if general_only else "all kinds"
        version_scope = (
            "all prompt versions" if include_current else f"prompt_version != {PROMPT_VERSION}"
        )
        typer.echo(f"would queue backfill for {count} document(s) ({kinds_scope}, {version_scope})")
    else:
        typer.echo(f"queued backfill for {count} document(s)")


@app.command("backfill-embeddings")
def backfill_embeddings(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N documents."
    ),
    include_existing: bool = typer.Option(
        False, "--include-existing", help="Re-embed documents that already have chunks."
    ),
) -> None:
    """Queue embedding for documents that have OCR text but no chunks yet.

    Backfills the semantic index for documents ingested before the embedding
    stage existed. Idempotent — ``embed_document`` replaces a document's
    chunks — so re-running is safe. The worker must be running to compute the
    embeddings; this command only enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = select(Document.id).where(
            Document.deleted_at.is_(None), Document.ocr_text.isnot(None)
        )
        if not include_existing:
            statement = statement.where(~exists().where(DocumentChunk.document_id == Document.id))
        statement = statement.order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        async with job_app.open_async():
            for document_id in document_ids:
                await embed_document.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    typer.echo(f"queued embedding for {count} document(s)")


@app.command("backfill-validation")
def backfill_validation(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only process the first N documents."
    ),
) -> None:
    """Recompute review_status + validation findings for existing documents.

    Idempotent: re-running recomputes from current field values. Use after
    deploying new validation rules or to seed the queue on an existing corpus.
    """
    floor = get_settings().extraction_validation_ocr_floor

    async def operation(session: AsyncSession) -> int:
        statement = select(Document).where(Document.deleted_at.is_(None)).order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        documents = list((await session.execute(statement)).scalars().all())
        today = datetime.now(UTC).date()
        for document in documents:
            kind_slug = None
            if document.kind_id is not None:
                kind = await session.get(Kind, document.kind_id)
                kind_slug = kind.slug if kind is not None else None
            sender_name = None
            if document.sender_id is not None:
                sender = await session.get(Sender, document.sender_id)
                sender_name = sender.name if sender is not None else None
            findings = validate(
                document, kind_slug=kind_slug, sender_name=sender_name, ocr_floor=floor, today=today
            )
            document.review_status = derive_review_status(findings)
            document.extra = {
                **document.extra,
                "validation": {
                    "prompt_version": "backfill",
                    "findings": findings_to_payload(findings),
                    "validated_at": datetime.now(UTC).isoformat(),
                },
            }
        await session.commit()
        return len(documents)

    count = _run(operation)
    typer.echo(f"revalidated {count} document(s)")


@app.command("backfill-markdown")
def backfill_markdown(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N documents."
    ),
    include_existing: bool = typer.Option(
        False, "--include-existing", help="Re-render documents that already have pages."
    ),
) -> None:
    """Queue markdown generation (and re-embed) for documents without pages.

    Backfills the markdown layer for documents ingested before the markdown
    stage existed. Idempotent — ``markdown_document`` replaces a document's
    pages and chunks — so re-running is safe. The worker must be running to do
    the work; this command only enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = select(Document.id).where(Document.deleted_at.is_(None))
        if not include_existing:
            statement = statement.where(~exists().where(DocumentPage.document_id == Document.id))
        statement = statement.order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        async with job_app.open_async():
            for document_id in document_ids:
                await markdown_document.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    typer.echo(f"queued markdown generation for {count} document(s)")


@app.command("backfill-summaries")
def backfill_summaries(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N documents."
    ),
) -> None:
    """Queue metadata extraction for indexed documents that have no summary.

    Backfills summaries for documents ingested before the summary was
    generated. Re-runs the same extraction path new uploads use (via
    ``extract_document``), so it honours ``extra["user_edited_fields"]`` and
    respects the daily extraction budget — documents beyond the budget are
    skipped and can be re-queued the next day. The worker must be running to
    do the work; this command only enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = (
            select(Document.id)
            .where(
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.INDEXED,
                Document.summary.is_(None),
            )
            .order_by(Document.id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        async with job_app.open_async():
            for document_id in document_ids:
                await extract_document.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    typer.echo(f"queued extraction for {count} document(s)")


@app.command("sweep-matters")
def sweep_matters(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N documents."
    ),
    all_documents: bool = typer.Option(
        False,
        "--all",
        help="Re-classify every non-deleted document, not just unclassified ones.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report the candidate count without enqueuing anything."
    ),
) -> None:
    """Queue business-matter classification for documents.

    Run after the matter vocabulary changes (a matter added, renamed, or its
    hint edited) so existing documents are re-filed against the new list. By
    default only documents that have never been classified are queued (those
    with no ``extra['matter_classification']`` provenance); pass ``--all`` to
    re-classify everything. Classification is merge-only and honours
    ``extra['user_edited_fields']``, so hand-curated matters are never
    overwritten. The worker must be running to do the work; this command only
    enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = select(Document.id).where(Document.deleted_at.is_(None))
        if not all_documents:
            # Not yet classified: no provenance stamp from a prior pass.
            statement = statement.where(Document.extra["matter_classification"].is_(None))
        statement = statement.order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        if dry_run:
            return len(document_ids)
        async with job_app.open_async():
            for document_id in document_ids:
                await classify_document_matters.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    if dry_run:
        typer.echo(f"{count} document(s) would be queued for matter classification")
    else:
        typer.echo(f"queued matter classification for {count} document(s)")


# Junk image documents (email logo/tracker PNGs) carry almost no OCR text and
# are tiny on disk. A real photographed/scanned page fails BOTH tests: dense
# OCR output and a file comfortably over 20 kB.
JUNK_OCR_CHARS_MAX: int = 100
JUNK_IMAGE_BYTES_MAX: int = 20_000


def _sweep_candidates_statement() -> Select[Any]:
    """Select (Document, ocr_chars, size_bytes) rows matching the junk-image heuristic.

    Size comes from the first ``received`` IngestionEvent's ``detail->>'size'``
    (documents have no size column); paperless-imported documents may lack it,
    in which case the size branch is NULL and only the OCR branch can match.
    """
    received_size = (
        select(cast(IngestionEvent.detail["size"].astext, BigInteger))
        .where(
            IngestionEvent.document_id == Document.id,
            IngestionEvent.event == "received",
        )
        .order_by(IngestionEvent.id)
        .limit(1)
        .scalar_subquery()
    )
    ocr_chars = func.length(func.coalesce(Document.ocr_text, ""))
    return (
        select(Document, ocr_chars.label("ocr_chars"), received_size.label("size_bytes"))
        .where(
            Document.deleted_at.is_(None),
            Document.mime_type.like("image/%"),
            or_(ocr_chars < JUNK_OCR_CHARS_MAX, received_size < JUNK_IMAGE_BYTES_MAX),
        )
        .order_by(Document.id)
    )


@app.command("sweep-junk")
def sweep_junk(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Soft-delete the documents named via --ids (default: dry run, list only).",
    ),
    ids: str | None = typer.Option(
        None,
        "--ids",
        help="Comma-separated document ids to soft-delete (required with --apply).",
    ),
) -> None:
    """Find (and with --apply, soft-delete) junk image documents.

    Candidates are non-deleted ``image/*`` documents whose OCR text is under
    100 characters OR whose original upload was under 20 kB (size read from the
    ``received`` ingestion event; absent for some paperless imports). The
    default is a DRY RUN that only lists candidates. ``--apply`` requires an
    explicit ``--ids`` list, refuses any id outside the candidate set, skips
    already-deleted ids, and never hard-deletes: it mirrors the API delete
    endpoint (sets ``deleted_at`` and records a ``deleted`` event with empty
    detail), so swept documents can be restored until the retention purge.
    """
    if apply and not ids:
        typer.echo(
            "error: --apply requires --ids with an explicit comma-separated list of "
            "document ids (run without --apply first to list candidates)"
        )
        raise typer.Exit(code=1)
    if ids and not apply:
        typer.echo("error: --ids only makes sense with --apply")
        raise typer.Exit(code=1)

    if not apply:

        async def list_operation(session: AsyncSession) -> list[tuple[Document, int, int | None]]:
            rows = (await session.execute(_sweep_candidates_statement())).all()
            return [(row[0], row[1], row[2]) for row in rows]

        candidates = _run(list_operation)
        for document, ocr_chars, size_bytes in candidates:
            size = "?" if size_bytes is None else str(size_bytes)
            typer.echo(
                f"{document.id}\t{document.original_filename or '-'}\t{document.mime_type}"
                f"\t{size} B\t{ocr_chars} OCR chars\t{document.source.value}"
            )
        typer.echo(f"{len(candidates)} junk image candidate(s) — dry run, nothing deleted")
        if candidates:
            id_list = ",".join(str(document.id) for document, _, _ in candidates)
            typer.echo(f"apply with: library sweep-junk --apply --ids {id_list}")
        return

    try:
        target_ids = sorted({int(part) for part in (ids or "").split(",") if part.strip()})
    except ValueError:
        typer.echo(f"error: --ids must be a comma-separated list of integers, got: {ids}")
        raise typer.Exit(code=1) from None
    if not target_ids:
        typer.echo("error: --ids is empty")
        raise typer.Exit(code=1)

    async def apply_operation(session: AsyncSession) -> tuple[list[int], list[int]]:
        candidate_ids = {
            row[0].id for row in (await session.execute(_sweep_candidates_statement())).all()
        }
        documents = {
            document.id: document
            for document in (
                await session.execute(select(Document).where(Document.id.in_(target_ids)))
            ).scalars()
        }
        missing = [i for i in target_ids if i not in documents]
        already = [i for i in target_ids if i in documents and documents[i].deleted_at is not None]
        refused = [
            i
            for i in target_ids
            if i in documents and documents[i].deleted_at is None and i not in candidate_ids
        ]
        if missing or refused:
            # Refuse the whole batch before touching anything: --ids must name
            # only current candidates (or already-deleted ids, which are skipped).
            for document_id in missing:
                typer.echo(f"error: {document_id} does not exist")
            for document_id in refused:
                typer.echo(
                    f"error: {document_id} is not a junk-image candidate — refusing to delete it"
                )
            typer.echo("nothing deleted")
            raise typer.Exit(code=1)

        deleted: list[int] = []
        for document_id in target_ids:
            if document_id in already:
                continue
            document = documents[document_id]
            # Mirror DELETE /api/documents/{id}: soft-delete + audit event.
            document.deleted_at = datetime.now(UTC)
            session.add(IngestionEvent(document_id=document.id, event="deleted", detail={}))
            deleted.append(document_id)
        await session.commit()
        return deleted, already

    deleted, already = _run(apply_operation)
    for document_id in already:
        typer.echo(f"{document_id} already deleted — skipped")
    for document_id in deleted:
        typer.echo(f"soft-deleted {document_id}")
    typer.echo(f"soft-deleted {len(deleted)} document(s), skipped {len(already)} already deleted")


# Encrypted PDFs cannot be OCR'd (pypdfium2 cannot read them without the
# password), so a password-protected upload made before ingest-time unlocking
# existed always landed in `failed`. That is the candidate set this backfill
# re-examines with the configured `pdf_unlock_passwords`.
def _encrypted_candidates_statement() -> Select[Any]:
    return (
        select(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.mime_type == "application/pdf",
            Document.status == DocumentStatus.FAILED,
        )
        .order_by(Document.id)
    )


def _classify_pdf(content: bytes, passwords: list[str]) -> tuple[str, bytes | None]:
    """Classify a stored PDF against the passwords via ``unlock_pdf``'s contract.

    - ``("skip", None)`` — not encrypted / unreadable (not a candidate).
    - ``("locked", None)`` — encrypted, no configured password unlocks it.
    - ``("unlockable", <bytes>)`` — encrypted; the bytes are the decrypted PDF.
    """
    try:
        unlocked = unlock_pdf(content, passwords)
    except PdfLockedError:
        return "locked", None
    if unlocked is content:
        return "skip", None
    return "unlockable", unlocked


@app.command("sweep-encrypted")
def sweep_encrypted(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Unlock the documents named via --ids in place (default: dry run, list only).",
    ),
    ids: str | None = typer.Option(
        None,
        "--ids",
        help="Comma-separated document ids to unlock (required with --apply).",
    ),
) -> None:
    """Find (and with --apply, unlock in place) encrypted PDF documents.

    Candidates are non-deleted ``application/pdf`` documents stuck in ``failed``
    whose stored original is encrypted. Each is tested against the configured
    ``LIBRARY_PDF_UNLOCK_PASSWORDS`` (the empty password is always tried). The
    default is a DRY RUN that only lists the encrypted candidates and whether
    each is ``unlockable`` or ``locked`` — nothing is written, and passwords are
    never printed.

    ``--apply`` requires an explicit ``--ids`` list and refuses any id that is
    not a current *unlockable* candidate. For each accepted id it stores the
    decrypted PDF (the new source of truth), points the row at the new sha256,
    resets it to ``received`` (clearing the stale OCR fields), records a
    ``pdf_unlocked_backfill`` event, removes the old encrypted original, and
    re-queues ``process_document`` so the worker OCRs/extracts/embeds it. A
    document whose decrypted content already exists (sha256 collision) is skipped
    and reported. The worker must be running for the re-queued jobs to run.
    """
    settings = get_settings()
    passwords = settings.pdf_unlock_passwords

    if apply and not ids:
        typer.echo(
            "error: --apply requires --ids with an explicit comma-separated list of "
            "document ids (run without --apply first to list candidates)"
        )
        raise typer.Exit(code=1)
    if ids and not apply:
        typer.echo("error: --ids only makes sense with --apply")
        raise typer.Exit(code=1)

    if not apply:

        async def list_operation(session: AsyncSession) -> tuple[int, list[tuple[Document, str]]]:
            documents = (await session.execute(_encrypted_candidates_statement())).scalars().all()
            candidates: list[tuple[Document, str]] = []
            for document in documents:
                try:
                    content = path_for(document.sha256).read_bytes()
                except FileNotFoundError:
                    candidates.append((document, "missing-file"))
                    continue
                label, _ = _classify_pdf(content, passwords)
                if label != "skip":
                    candidates.append((document, label))
            return len(documents), candidates

        scanned, candidates = _run(list_operation)
        unlockable = [document for document, label in candidates if label == "unlockable"]
        for document, label in candidates:
            typer.echo(f"{document.id}\t{document.original_filename or '-'}\t{label}")
        typer.echo(
            f"{scanned} failed PDF(s) scanned, {len(candidates)} encrypted, "
            f"{len(unlockable)} unlockable with the known passwords — dry run, nothing changed"
        )
        if unlockable:
            id_list = ",".join(str(document.id) for document in unlockable)
            typer.echo(f"apply with: library sweep-encrypted --apply --ids {id_list}")
        return

    try:
        target_ids = sorted({int(part) for part in (ids or "").split(",") if part.strip()})
    except ValueError:
        typer.echo(f"error: --ids must be a comma-separated list of integers, got: {ids}")
        raise typer.Exit(code=1) from None
    if not target_ids:
        typer.echo("error: --ids is empty")
        raise typer.Exit(code=1)

    async def apply_operation(
        session: AsyncSession,
    ) -> tuple[list[int], list[tuple[int, int]]]:
        documents = {
            document.id: document
            for document in (
                await session.execute(select(Document).where(Document.id.in_(target_ids)))
            ).scalars()
        }
        # Recompute the unlockable decrypted bytes per target; refuse any id that
        # is not a current unlockable candidate (whole batch, before any write).
        decrypted: dict[int, bytes] = {}
        missing = [i for i in target_ids if i not in documents]
        refused: list[int] = []
        for document_id in target_ids:
            document = documents.get(document_id)
            if document is None:
                continue
            if (
                document.deleted_at is not None
                or document.mime_type != "application/pdf"
                or document.status is not DocumentStatus.FAILED
            ):
                refused.append(document_id)
                continue
            try:
                content = path_for(document.sha256).read_bytes()
            except FileNotFoundError:
                refused.append(document_id)
                continue
            label, unlocked = _classify_pdf(content, passwords)
            if label != "unlockable" or unlocked is None:
                refused.append(document_id)
                continue
            decrypted[document_id] = unlocked
        if missing or refused:
            for document_id in missing:
                typer.echo(f"error: {document_id} does not exist")
            for document_id in refused:
                typer.echo(
                    f"error: {document_id} is not an unlockable encrypted PDF candidate — "
                    "refusing to touch it"
                )
            typer.echo("nothing changed")
            raise typer.Exit(code=1)

        unlocked_ids: list[int] = []
        collisions: list[tuple[int, int]] = []
        removed_shas: list[str] = []
        for document_id in target_ids:
            document = documents[document_id]
            new_content = decrypted[document_id]
            new_sha = hashlib.sha256(new_content).hexdigest()
            old_sha = document.sha256
            # sha256 is globally unique: if the decrypted content already exists
            # (as any document, including soft-deleted), skip rather than raise.
            existing_id = (
                await session.execute(
                    select(Document.id).where(
                        Document.sha256 == new_sha, Document.id != document.id
                    )
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                collisions.append((document_id, existing_id))
                continue
            store(new_content)  # write the decrypted original before pointing the row at it
            document.sha256 = new_sha
            document.status = DocumentStatus.RECEIVED
            document.ocr_text = None
            document.ocr_confidence = None
            document.page_count = None
            document.searchable_pdf = False
            session.add(
                IngestionEvent(
                    document_id=document.id,
                    event="pdf_unlocked_backfill",
                    detail={"old_sha256": old_sha, "new_sha256": new_sha, "pdf_unlocked": True},
                )
            )
            removed_shas.append(old_sha)
            unlocked_ids.append(document_id)
        await session.commit()
        # Post-commit (the row already points at the new file): drop the old
        # encrypted originals and re-queue the pipeline. A crash here only
        # orphans a harmless old file — never a row pointing at a missing file.
        for old_sha in removed_shas:
            remove(old_sha)
        if unlocked_ids:
            async with job_app.open_async():
                for document_id in unlocked_ids:
                    await process_document.defer_async(document_id=document_id)
        return unlocked_ids, collisions

    unlocked_ids, collisions = _run(apply_operation)
    for document_id, existing_id in collisions:
        typer.echo(
            f"{document_id} skipped — decrypted content already exists as document {existing_id}"
        )
    for document_id in unlocked_ids:
        typer.echo(f"unlocked {document_id} (re-queued for processing)")
    typer.echo(f"unlocked {len(unlocked_ids)} document(s), skipped {len(collisions)} collision(s)")


@app.command("eval-extractions")
def eval_extractions(
    sample: int | None = typer.Option(
        None, "--sample", min=1, help="Judge a head-slice of N documents."
    ),
    judge_all: bool = typer.Option(
        False, "--all", help="Judge every eligible document (ignores --sample)."
    ),
) -> None:
    """Score extraction quality (flywheel + LLM judge) and record an eval run.

    Flywheel accuracy is computed over every document carrying corrections.
    The judge runs over the sampled set (or all eligible documents with OCR
    text) for coverage. One eval_runs row is written, pinned to the modal
    prompt_version + model so runs are comparable over time.

    Sampling is a deterministic head-slice (``eligible[:N]``). Random sampling
    is a documented follow-up.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        typer.echo("error: LIBRARY_ANTHROPIC_API_KEY is required to run the judge")
        raise typer.Exit(code=1)

    async def operation(session: AsyncSession) -> EvalRun:
        all_docs = list(
            (
                await session.execute(
                    select(Document).where(Document.deleted_at.is_(None)).order_by(Document.id)
                )
            )
            .scalars()
            .all()
        )
        flywheel = flywheel_accuracy(all_docs)

        eligible = [d for d in all_docs if (d.ocr_text or "").strip()]
        if not judge_all and sample is not None:
            eligible = eligible[:sample]

        results: list[JudgeResult] = []
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            for document in eligible:
                results.append(await judge(document, client=client, settings=settings))

        agreement = judge_agreement(results)
        per_field = combine(flywheel, agreement)
        distribution = version_distribution(eligible or all_docs)
        version, model = modal_version(distribution)
        overall = {
            "documents_total": len(all_docs),
            "reviewed_total": sum(1 for d in all_docs if (d.extra or {}).get("corrections")),
            "judged_total": len(results),
        }
        run = EvalRun(
            prompt_version=version,
            model=model,
            version_mix=distribution,
            sample_size=len(results),
            per_field=per_field,
            overall=overall,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    run = _run(operation)
    typer.echo(
        f"eval run #{run.id}"
        f"  prompt={run.prompt_version} model={run.model} judged={run.sample_size}"
    )
    typer.echo(f"{'field':<18}{'flywheel':>12}{'judge':>12}{'n':>6}")
    for field, scores in run.per_field.items():
        fw = "-" if scores["flywheel_accuracy"] is None else f"{scores['flywheel_accuracy']:.0%}"
        jg = "-" if scores["judge_agreement"] is None else f"{scores['judge_agreement']:.0%}"
        typer.echo(f"{field:<18}{fw:>12}{jg:>12}{scores['n']:>6}")


def main() -> None:
    """Console-script entry point (`library`)."""
    app()


if __name__ == "__main__":
    main()
