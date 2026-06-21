"""`library` — administration CLI (typer): accounts and imports.

There is deliberately no signup endpoint: users of this family-scale
archive are created from the host. Each command opens a fresh async
engine (NullPool) and disposes it, so repeated invocations never share a
loop-bound connection.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import typer
from anthropic import AsyncAnthropic
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.auth.passwords import hash_password
from library.auth.service import revoke_all_credentials
from library.config import get_settings
from library.extraction.eval import (
    combine,
    flywheel_accuracy,
    judge_agreement,
    modal_version,
    version_distribution,
)
from library.extraction.judge import JudgeResult, judge
from library.extraction.validation import derive_review_status, findings_to_payload, validate
from library.importer.client import PaperlessClient
from library.importer.runner import ImportReport, format_report, run_import
from library.jobs import embed_document, job_app, markdown_document
from library.models import Document, DocumentChunk, DocumentPage, EvalRun, Kind, User

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
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
            )
        )
        await session.commit()

    password = _read_password(password_stdin=password_stdin)
    _run(operation)
    typer.echo(f"created user {username}")


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
        display = f" ({user.display_name})" if user.display_name else ""
        typer.echo(
            f"{user.id}\t{user.username}{display}\t{state}\tcreated {user.created_at:%Y-%m-%d}"
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
        # the Procrastinate app open.
        async with job_app.open_async(), PaperlessClient(base_url, secret) as client:
            return await run_import(
                session, client, dry_run=dry_run, no_extract=no_extract, limit=limit
            )

    report = _run(operation)
    typer.echo(format_report(report))
    if report.failed:
        raise typer.Exit(code=1)


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
            findings = validate(document, kind_slug=kind_slug, ocr_floor=floor, today=today)
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
