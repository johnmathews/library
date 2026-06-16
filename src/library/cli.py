"""`library` — administration CLI (typer): accounts and imports.

There is deliberately no signup endpoint: users of this family-scale
archive are created from the host. Each command opens a fresh async
engine (NullPool) and disposes it, so repeated invocations never share a
loop-bound connection.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable

import typer
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.auth.passwords import hash_password
from library.auth.service import revoke_all_credentials
from library.config import get_settings
from library.importer.client import PaperlessClient
from library.importer.runner import ImportReport, format_report, run_import
from library.jobs import embed_document, job_app
from library.models import Document, DocumentChunk, User

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
            statement = statement.where(
                ~exists().where(DocumentChunk.document_id == Document.id)
            )
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


def main() -> None:
    """Console-script entry point (`library`)."""
    app()


if __name__ == "__main__":
    main()
