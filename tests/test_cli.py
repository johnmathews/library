"""Tests for the `library` account-management CLI (typer)."""

import asyncio
import datetime
import hashlib
import io
import uuid
from collections.abc import Iterator

import pikepdf
import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

import library.cli as cli_module
from library.auth.passwords import verify_password
from library.cli import app
from library.config import get_settings
from library.extraction.extractor import PROMPT_VERSION
from library.extraction.judge import FieldVerdict, JudgeResult
from library.jobs import job_app
from library.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentSource,
    DocumentStatus,
    IngestionEvent,
    Kind,
    ReviewStatus,
)
from library.storage import path_for, store
from tests.conftest import create_user, fetch_all
from tests.ocr_fixtures import encrypt_pdf, make_text_pdf
from tests.test_auth import execute_sql

pytestmark = pytest.mark.integration

runner = CliRunner()


def _seed_document(
    database_url: str,
    marker: str,
    *,
    ocr_text: str | None,
    with_chunk: bool,
    with_page: bool = False,
    summary: str | None = None,
) -> int:
    """Insert a document (optionally with one chunk and/or one page) and return its id."""

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    ocr_text=ocr_text,
                    status=DocumentStatus.INDEXED,
                    summary=summary,
                )
                session.add(document)
                await session.commit()
                if with_chunk:
                    session.add(
                        DocumentChunk(
                            document_id=document.id,
                            chunk_index=1,
                            text="existing",
                            embedding=[0.0] * 1024,
                        )
                    )
                    await session.commit()
                if with_page:
                    session.add(
                        DocumentPage(
                            document_id=document.id,
                            page_number=1,
                            markdown="# Page 1",
                            char_count=8,
                        )
                    )
                    await session.commit()
                return document.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


@pytest.fixture
def cli_database_url(api_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point settings at the API test database for CLI commands."""
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    get_settings.cache_clear()
    yield api_database_url
    get_settings.cache_clear()


def unique_username() -> str:
    return f"cli-{uuid.uuid4().hex[:12]}"


def password_hash_of(database_url: str, username: str) -> str:
    [(value,)] = fetch_all(
        database_url, "SELECT password_hash FROM users WHERE username = :u", u=username
    )
    return value


def test_user_add_with_prompt(cli_database_url: str) -> None:
    username = unique_username()
    result = runner.invoke(
        app,
        ["user", "add", username, "--display-name", "Test Person"],
        input="hunter2hunter2\nhunter2hunter2\n",
    )
    assert result.exit_code == 0, result.output
    [(display_name, is_active)] = fetch_all(
        cli_database_url,
        "SELECT display_name, is_active FROM users WHERE username = :u",
        u=username,
    )
    assert display_name == "Test Person"
    assert is_active is True
    stored = password_hash_of(cli_database_url, username)
    assert stored.startswith("$argon2id$")
    assert verify_password("hunter2hunter2", stored)


def test_user_add_password_stdin(cli_database_url: str) -> None:
    username = unique_username()
    result = runner.invoke(
        app, ["user", "add", username, "--password-stdin"], input="from-stdin-pw\n"
    )
    assert result.exit_code == 0, result.output
    assert verify_password("from-stdin-pw", password_hash_of(cli_database_url, username))


def test_user_add_duplicate_username_fails(cli_database_url: str) -> None:
    existing = create_user(cli_database_url)
    result = runner.invoke(
        app, ["user", "add", existing.username, "--password-stdin"], input="whatever\n"
    )
    assert result.exit_code != 0
    assert "exists" in result.output


def test_user_passwd_changes_hash(cli_database_url: str) -> None:
    user = create_user(cli_database_url)
    before = password_hash_of(cli_database_url, user.username)
    result = runner.invoke(
        app, ["user", "passwd", user.username], input="new-password-1\nnew-password-1\n"
    )
    assert result.exit_code == 0, result.output
    after = password_hash_of(cli_database_url, user.username)
    assert after != before
    assert verify_password("new-password-1", after)


def test_user_passwd_unknown_user_fails(cli_database_url: str) -> None:
    result = runner.invoke(app, ["user", "passwd", "no-such-user-xyz"], input="pw\npw\n")
    assert result.exit_code != 0


def test_user_disable_revokes_everything(cli_database_url: str) -> None:
    user = create_user(cli_database_url)
    execute_sql(
        cli_database_url,
        "INSERT INTO sessions (token_hash, user_id, expires_at)"
        " VALUES (:h, :uid, now() + interval '30 days')",
        h=f"hash-{user.id}-session",
        uid=user.id,
    )
    execute_sql(
        cli_database_url,
        "INSERT INTO api_tokens (user_id, name, token_hash) VALUES (:uid, 'tok', :h)",
        h=f"hash-{user.id}-token",
        uid=user.id,
    )

    result = runner.invoke(app, ["user", "disable", user.username])
    assert result.exit_code == 0, result.output

    [(is_active,)] = fetch_all(
        cli_database_url, "SELECT is_active FROM users WHERE id = :uid", uid=user.id
    )
    assert is_active is False
    assert (
        fetch_all(cli_database_url, "SELECT 1 FROM sessions WHERE user_id = :uid", uid=user.id)
        == []
    )
    [(revoked_at,)] = fetch_all(
        cli_database_url, "SELECT revoked_at FROM api_tokens WHERE user_id = :uid", uid=user.id
    )
    assert revoked_at is not None


def test_user_list_shows_users(cli_database_url: str) -> None:
    user = create_user(cli_database_url)
    result = runner.invoke(app, ["user", "list"])
    assert result.exit_code == 0, result.output
    assert user.username in result.output


def _is_admin(database_url: str, username: str) -> bool:
    [(value,)] = fetch_all(
        database_url, "SELECT is_admin FROM users WHERE username = :u", u=username
    )
    return value


def test_user_add_admin_flag_creates_admin(cli_database_url: str) -> None:
    username = unique_username()
    result = runner.invoke(
        app, ["user", "add", username, "--admin", "--password-stdin"], input="pw-pw-pw\n"
    )
    assert result.exit_code == 0, result.output
    assert _is_admin(cli_database_url, username) is True


def test_user_add_without_admin_flag_is_not_admin(cli_database_url: str) -> None:
    username = unique_username()
    result = runner.invoke(app, ["user", "add", username, "--password-stdin"], input="pw-pw-pw\n")
    assert result.exit_code == 0, result.output
    assert _is_admin(cli_database_url, username) is False


def test_user_set_admin_grants_and_revokes(cli_database_url: str) -> None:
    user = create_user(cli_database_url)
    assert _is_admin(cli_database_url, user.username) is False

    grant = runner.invoke(app, ["user", "set-admin", user.username])
    assert grant.exit_code == 0, grant.output
    assert _is_admin(cli_database_url, user.username) is True

    revoke = runner.invoke(app, ["user", "set-admin", user.username, "--revoke"])
    assert revoke.exit_code == 0, revoke.output
    assert _is_admin(cli_database_url, user.username) is False


def test_user_set_admin_unknown_user_fails(cli_database_url: str) -> None:
    result = runner.invoke(app, ["user", "set-admin", "no-such-user-xyz"])
    assert result.exit_code != 0
    assert "no such user" in result.output


def test_backfill_embeddings_enqueues_only_unindexed(cli_database_url: str) -> None:
    needs = _seed_document(cli_database_url, "needs", ocr_text="real text", with_chunk=False)
    no_text = _seed_document(cli_database_url, "no-text", ocr_text=None, with_chunk=False)
    already = _seed_document(cli_database_url, "done", ocr_text="real text", with_chunk=True)

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-embeddings"])
    assert result.exit_code == 0, result.output

    enqueued = {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.embed_document"
    }
    assert needs in enqueued
    assert already not in enqueued  # already has chunks
    assert no_text not in enqueued  # no OCR text to embed


def test_backfill_embeddings_include_existing(cli_database_url: str) -> None:
    already = _seed_document(cli_database_url, "again", ocr_text="real text", with_chunk=True)
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-embeddings", "--include-existing"])
    assert result.exit_code == 0, result.output
    enqueued = {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.embed_document"
    }
    assert already in enqueued


def _seed_validation_document(database_url: str, *, document_date: datetime.date | None) -> int:
    """Insert a document with the given document_date and return its id."""

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    ocr_text="some text",
                    status=DocumentStatus.INDEXED,
                    document_date=document_date,
                )
                session.add(document)
                await session.commit()
                return document.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


def _fetch_review_status(database_url: str, document_id: int) -> tuple[str, object]:
    """Return (review_status, extra) for the given document id."""
    [(review_status, extra)] = fetch_all(
        database_url,
        "SELECT review_status, extra FROM documents WHERE id = :id",
        id=document_id,
    )
    return review_status, extra


def test_backfill_validation_sets_review_status(cli_database_url: str) -> None:
    future_date = datetime.date.today() + datetime.timedelta(days=30)
    future_doc = _seed_validation_document(cli_database_url, document_date=future_date)
    # A past date avoids the date_plausibility finding; no other fields trigger rules.
    past_date = datetime.date(2024, 1, 15)
    clean_doc = _seed_validation_document(cli_database_url, document_date=past_date)

    result = runner.invoke(app, ["backfill-validation"])

    assert result.exit_code == 0, result.output
    assert "revalidated" in result.output

    future_status, future_extra = _fetch_review_status(cli_database_url, future_doc)
    assert future_status == ReviewStatus.NEEDS_REVIEW
    assert isinstance(future_extra, dict)
    assert "validation" in future_extra
    assert len(future_extra["validation"]["findings"]) > 0

    clean_status, clean_extra = _fetch_review_status(cli_database_url, clean_doc)
    assert clean_status == ReviewStatus.UNREVIEWED
    assert isinstance(clean_extra, dict)
    assert "validation" in clean_extra


def _seed_eval_documents(database_url: str) -> tuple[int, int]:
    """Insert one reviewed doc (with corrections + fields_set) and one plain doc with OCR text.

    Returns (reviewed_id, plain_id).
    """

    async def _insert() -> tuple[int, int]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                reviewed = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    ocr_text="Invoice from Acme Corp dated 2024-01-15 total 99.00 EUR",
                    status=DocumentStatus.INDEXED,
                    extra={
                        "extraction": {
                            "prompt_version": "v1",
                            "model": "claude-3-5-sonnet-20241022",
                            "fields_set": ["title", "amount_total"],
                        },
                        "corrections": [{"field": "amount_total", "old": "99.00", "new": "109.00"}],
                    },
                )
                plain = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    ocr_text="Receipt for office supplies 2024-02-20",
                    status=DocumentStatus.INDEXED,
                    extra={
                        "extraction": {
                            "prompt_version": "v1",
                            "model": "claude-3-5-sonnet-20241022",
                            "fields_set": ["title"],
                        },
                    },
                )
                session.add(reviewed)
                session.add(plain)
                await session.commit()
                return reviewed.id, plain.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


def test_eval_extractions_persists_run(
    cli_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """eval-extractions --all inserts one EvalRun row with correct per_field and sample_size."""
    _seed_eval_documents(cli_database_url)

    # Stub the judge so no real Anthropic call is made. Both eligible docs have
    # OCR text, so the stub will be called twice (once per doc).
    async def _fake_judge(document: Document, *, client: object, settings: object) -> JudgeResult:
        return JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None)])

    monkeypatch.setenv("LIBRARY_ANTHROPIC_API_KEY", "sk-fake-key-for-testing")
    get_settings.cache_clear()
    monkeypatch.setattr(cli_module, "judge", _fake_judge)

    result = runner.invoke(app, ["eval-extractions", "--all"])
    assert result.exit_code == 0, result.output

    rows = fetch_all(cli_database_url, "SELECT sample_size, per_field FROM eval_runs")
    assert len(rows) == 1, f"Expected 1 eval_runs row, got {len(rows)}"
    sample_size, per_field = rows[0]
    # At least the two seeded docs (both have OCR text) must be judged. The
    # shared api_database_url accumulates rows across tests, so the count can
    # exceed 2 — assert a lower bound rather than an exact value.
    assert sample_size >= 2
    assert "title" in per_field


def test_backfill_markdown_enqueues_documents_without_pages(cli_database_url: str) -> None:
    needs = _seed_document(cli_database_url, "md-needs", ocr_text="some text", with_chunk=False)
    already = _seed_document(
        cli_database_url, "md-done", ocr_text="some text", with_chunk=False, with_page=True
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-markdown"])
    assert result.exit_code == 0, result.output

    enqueued = {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.markdown_document"
    }
    assert needs in enqueued
    assert already not in enqueued  # already has pages


def test_backfill_summaries_enqueues_documents_without_summary(cli_database_url: str) -> None:
    needs = _seed_document(cli_database_url, "sum-needs", ocr_text="real text", with_chunk=True)
    already = _seed_document(
        cli_database_url, "sum-done", ocr_text="real text", with_chunk=True, summary="A summary."
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-summaries"])
    assert result.exit_code == 0, result.output

    enqueued = {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.extract_document"
    }
    assert needs in enqueued
    assert already not in enqueued  # already has a summary


def test_backfill_summaries_respects_limit(cli_database_url: str) -> None:
    _seed_document(cli_database_url, "sum-a", ocr_text="real text", with_chunk=True)
    _seed_document(cli_database_url, "sum-b", ocr_text="real text", with_chunk=True)

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-summaries", "--limit", "1"])
    assert result.exit_code == 0, result.output

    enqueued = [
        job
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.extract_document"
    ]
    assert len(enqueued) == 1


def _seed_matter_document(database_url: str, marker: str, *, classified: bool) -> int:
    """Insert a non-deleted document, optionally already matter-classified."""

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    extra={"matter_classification": {"model": "x"}} if classified else {},
                )
                session.add(document)
                await session.commit()
                return document.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


def _sweep_enqueued(connector: InMemoryConnector) -> set[int]:
    return {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.classify_document_matters"
    }


def test_sweep_matters_enqueues_only_unclassified_by_default(cli_database_url: str) -> None:
    fresh = _seed_matter_document(cli_database_url, "matter-fresh", classified=False)
    done = _seed_matter_document(cli_database_url, "matter-done", classified=True)

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["sweep-matters"])
    assert result.exit_code == 0, result.output

    enqueued = _sweep_enqueued(connector)
    assert fresh in enqueued
    assert done not in enqueued  # already classified


def test_sweep_matters_all_reclassifies_everything(cli_database_url: str) -> None:
    fresh = _seed_matter_document(cli_database_url, "matter-all-fresh", classified=False)
    done = _seed_matter_document(cli_database_url, "matter-all-done", classified=True)

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["sweep-matters", "--all"])
    assert result.exit_code == 0, result.output

    enqueued = _sweep_enqueued(connector)
    assert {fresh, done} <= enqueued  # --all includes the already-classified doc


def test_sweep_matters_dry_run_enqueues_nothing(cli_database_url: str) -> None:
    _seed_matter_document(cli_database_url, "matter-dry", classified=False)

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["sweep-matters", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would be queued" in result.output
    assert _sweep_enqueued(connector) == set()


def _seed_extraction_document(
    database_url: str,
    *,
    kind_slug: str | None,
    prompt_version: str | None,
) -> int:
    """Insert a document with the given kind slug and extraction prompt_version.

    ``prompt_version=None`` leaves ``extra`` empty (never extracted); otherwise
    it sets ``extra["extraction"]["prompt_version"]``.
    """

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                kind_id = None
                if kind_slug is not None:
                    kind_id = (
                        await session.execute(select(Kind.id).where(Kind.slug == kind_slug))
                    ).scalar_one()
                extra: dict[str, object] = {}
                if prompt_version is not None:
                    extra = {"extraction": {"prompt_version": prompt_version}}
                document = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    ocr_text="some text",
                    status=DocumentStatus.INDEXED,
                    kind_id=kind_id,
                    extra=extra,
                )
                session.add(document)
                await session.commit()
                return document.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


def _deferred_ids(connector: InMemoryConnector, task_name: str) -> set[int]:
    return {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == task_name
    }


def test_backfill_dry_run_enqueues_nothing(cli_database_url: str) -> None:
    _seed_extraction_document(cli_database_url, kind_slug="reference", prompt_version="old-version")

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would queue" in result.output
    assert connector.jobs == {}


def test_backfill_default_targets_old_prompt_general_kinds(cli_database_url: str) -> None:
    invoice_old = _seed_extraction_document(
        cli_database_url, kind_slug="invoice", prompt_version="old-version"
    )
    ref_old = _seed_extraction_document(
        cli_database_url, kind_slug="reference", prompt_version="old-version"
    )
    ref_current = _seed_extraction_document(
        cli_database_url, kind_slug="reference", prompt_version=PROMPT_VERSION
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill"])
    assert result.exit_code == 0, result.output

    extract_ids = _deferred_ids(connector, "library.jobs.extract_document")
    markdown_ids = _deferred_ids(connector, "library.jobs.markdown_document")
    assert ref_old in extract_ids
    assert ref_old in markdown_ids  # markdown re-embeds
    assert invoice_old not in extract_ids  # not a general kind
    assert ref_current not in extract_ids  # already at current prompt version


def test_backfill_all_kinds_includes_invoice(cli_database_url: str) -> None:
    invoice_old = _seed_extraction_document(
        cli_database_url, kind_slug="invoice", prompt_version="old-version"
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill", "--all-kinds"])
    assert result.exit_code == 0, result.output

    assert invoice_old in _deferred_ids(connector, "library.jobs.extract_document")


def test_backfill_kinds_filter_scopes_to_named_kinds(cli_database_url: str) -> None:
    """--kinds overrides the general-only default and restricts to the given slugs."""
    letter_old = _seed_extraction_document(
        cli_database_url, kind_slug="letter", prompt_version="old-version"
    )
    invoice_old = _seed_extraction_document(
        cli_database_url, kind_slug="invoice", prompt_version="old-version"
    )
    manual_old = _seed_extraction_document(
        cli_database_url, kind_slug="manual", prompt_version="old-version"
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill", "--kinds", "letter,invoice"])
    assert result.exit_code == 0, result.output

    extract_ids = _deferred_ids(connector, "library.jobs.extract_document")
    assert letter_old in extract_ids  # a named kind
    assert invoice_old in extract_ids  # a named kind (transactional, normally excluded)
    assert manual_old not in extract_ids  # a general kind, but not named -> excluded


def test_backfill_include_current_includes_up_to_date(cli_database_url: str) -> None:
    ref_current = _seed_extraction_document(
        cli_database_url, kind_slug="reference", prompt_version=PROMPT_VERSION
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill", "--include-current"])
    assert result.exit_code == 0, result.output

    assert ref_current in _deferred_ids(connector, "library.jobs.extract_document")


def test_backfill_respects_limit(cli_database_url: str) -> None:
    _seed_extraction_document(cli_database_url, kind_slug="reference", prompt_version="old-version")
    _seed_extraction_document(cli_database_url, kind_slug="research", prompt_version="old-version")

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill", "--limit", "1"])
    assert result.exit_code == 0, result.output

    assert len(_deferred_ids(connector, "library.jobs.extract_document")) == 1
    assert len(_deferred_ids(connector, "library.jobs.markdown_document")) == 1


def _seed_sweep_document(
    database_url: str,
    *,
    mime_type: str,
    ocr_text: str | None,
    original_filename: str,
    size: int | None = None,
) -> int:
    """Insert a document (optionally with a 'received' event carrying size) and return its id."""

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = Document(
                    sha256=uuid.uuid4().hex * 2,
                    mime_type=mime_type,
                    source=DocumentSource.EMAIL,
                    ocr_text=ocr_text,
                    status=DocumentStatus.INDEXED,
                    original_filename=original_filename,
                )
                session.add(document)
                await session.flush()
                if size is not None:
                    session.add(
                        IngestionEvent(
                            document_id=document.id,
                            event="received",
                            detail={
                                "filename": original_filename,
                                "size": size,
                                "mime_type": mime_type,
                                "source": DocumentSource.EMAIL.value,
                            },
                        )
                    )
                await session.commit()
                return document.id
        finally:
            await engine.dispose()

    return asyncio.run(_insert())


def _sweep_marker() -> str:
    return uuid.uuid4().hex[:12]


def test_sweep_junk_dry_run_lists_candidates_only(cli_database_url: str) -> None:
    """Dry run lists tiny/short-OCR images but not dense documents, and deletes nothing."""
    marker = _sweep_marker()
    junk = _seed_sweep_document(
        cli_database_url,
        mime_type="image/png",
        ocr_text="logo",
        original_filename=f"junk-{marker}.png",
        size=4321,
    )
    dense_pdf = _seed_sweep_document(
        cli_database_url,
        mime_type="application/pdf",
        ocr_text="dense prose " * 50,
        original_filename=f"real-{marker}.pdf",
        size=250_000,
    )
    big_image = _seed_sweep_document(
        cli_database_url,
        mime_type="image/png",
        ocr_text="scanned page text " * 20,
        original_filename=f"scan-{marker}.png",
        size=500_000,
    )

    result = runner.invoke(app, ["sweep-junk"])
    assert result.exit_code == 0, result.output

    assert f"junk-{marker}.png" in result.output
    assert "4321" in result.output
    assert f"real-{marker}.pdf" not in result.output  # not an image
    assert f"scan-{marker}.png" not in result.output  # dense OCR and large size
    assert "--apply --ids" in result.output  # hint on how to apply

    # Nothing was deleted by the dry run.
    for document_id in (junk, dense_pdf, big_image):
        [(deleted_at,)] = fetch_all(
            cli_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=document_id
        )
        assert deleted_at is None


def test_sweep_junk_dry_run_shows_unknown_size(cli_database_url: str) -> None:
    """A candidate without a 'received' size (e.g. paperless import) shows '?' for bytes."""
    marker = _sweep_marker()
    junk = _seed_sweep_document(
        cli_database_url,
        mime_type="image/gif",
        ocr_text=None,
        original_filename=f"nosize-{marker}.gif",
    )

    result = runner.invoke(app, ["sweep-junk"])
    assert result.exit_code == 0, result.output
    [line] = [line for line in result.output.splitlines() if f"nosize-{marker}.gif" in line]
    assert line.startswith(f"{junk}\t")
    assert "?" in line


def test_sweep_junk_apply_soft_deletes_and_is_idempotent(cli_database_url: str) -> None:
    marker = _sweep_marker()
    junk = _seed_sweep_document(
        cli_database_url,
        mime_type="image/png",
        ocr_text="",
        original_filename=f"apply-{marker}.png",
        size=1234,
    )

    result = runner.invoke(app, ["sweep-junk", "--apply", "--ids", str(junk)])
    assert result.exit_code == 0, result.output

    [(deleted_at,)] = fetch_all(
        cli_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=junk
    )
    assert deleted_at is not None
    events = fetch_all(
        cli_database_url,
        "SELECT detail FROM ingestion_events WHERE document_id = :id AND event = 'deleted'",
        id=junk,
    )
    assert len(events) == 1
    assert events[0][0] == {}  # same detail shape as the API delete endpoint

    # Second run: reported as already deleted, skipped, no second event.
    again = runner.invoke(app, ["sweep-junk", "--apply", "--ids", str(junk)])
    assert again.exit_code == 0, again.output
    assert "already deleted" in again.output
    events = fetch_all(
        cli_database_url,
        "SELECT detail FROM ingestion_events WHERE document_id = :id AND event = 'deleted'",
        id=junk,
    )
    assert len(events) == 1


def test_sweep_junk_apply_refuses_non_candidate(cli_database_url: str) -> None:
    marker = _sweep_marker()
    dense_pdf = _seed_sweep_document(
        cli_database_url,
        mime_type="application/pdf",
        ocr_text="dense prose " * 50,
        original_filename=f"keep-{marker}.pdf",
        size=250_000,
    )

    result = runner.invoke(app, ["sweep-junk", "--apply", "--ids", str(dense_pdf)])
    assert result.exit_code != 0
    assert "not" in result.output and str(dense_pdf) in result.output

    [(deleted_at,)] = fetch_all(
        cli_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=dense_pdf
    )
    assert deleted_at is None


def test_sweep_junk_apply_refusal_deletes_nothing_in_the_batch(cli_database_url: str) -> None:
    """One refused id aborts the whole batch: valid candidates in it stay undeleted."""
    marker = _sweep_marker()
    junk = _seed_sweep_document(
        cli_database_url,
        mime_type="image/png",
        ocr_text="x",
        original_filename=f"batch-{marker}.png",
        size=999,
    )
    dense_pdf = _seed_sweep_document(
        cli_database_url,
        mime_type="application/pdf",
        ocr_text="dense prose " * 50,
        original_filename=f"batch-keep-{marker}.pdf",
        size=250_000,
    )

    result = runner.invoke(app, ["sweep-junk", "--apply", "--ids", f"{junk},{dense_pdf}"])
    assert result.exit_code != 0

    [(deleted_at,)] = fetch_all(
        cli_database_url, "SELECT deleted_at FROM documents WHERE id = :id", id=junk
    )
    assert deleted_at is None


def test_sweep_junk_apply_without_ids_errors(cli_database_url: str) -> None:
    result = runner.invoke(app, ["sweep-junk", "--apply"])
    assert result.exit_code != 0
    assert "--ids" in result.output


def test_backfill_markdown_include_existing(cli_database_url: str) -> None:
    already = _seed_document(
        cli_database_url, "md-again", ocr_text="some text", with_chunk=False, with_page=True
    )
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["backfill-markdown", "--include-existing"])
    assert result.exit_code == 0, result.output
    enqueued = {
        job["args"]["document_id"]
        for job in connector.jobs.values()
        if job["task_name"] == "library.jobs.markdown_document"
    }
    assert already in enqueued


@pytest.fixture
def cli_data_dir(cli_database_url: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point storage at a temp data dir (originals live on disk for the sweep)."""
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield cli_database_url
    get_settings.cache_clear()


def _seed_stored_pdf(
    database_url: str,
    content: bytes,
    *,
    filename: str,
    status: DocumentStatus = DocumentStatus.FAILED,
    store_file: bool = True,
) -> int:
    """Insert a PDF document (with its bytes on disk) and return its id."""
    sha = hashlib.sha256(content).hexdigest()

    async def _insert() -> int:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                document = Document(
                    sha256=sha,
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=status,
                    original_filename=filename,
                    ocr_text=None,
                )
                session.add(document)
                await session.commit()
                return document.id
        finally:
            await engine.dispose()

    document_id = asyncio.run(_insert())
    if store_file:
        store(content)  # writes under get_settings().data_dir (the temp dir)
    return document_id


def _make_pdf(tmp_path, text: str) -> bytes:
    return make_text_pdf(tmp_path / f"{uuid.uuid4().hex}.pdf", lines=[text]).read_bytes()


def test_sweep_encrypted_dry_run_classifies(cli_data_dir: str, tmp_path) -> None:
    plain = _make_pdf(tmp_path, "Onbeveiligd")
    unlockable_id = _seed_stored_pdf(
        cli_data_dir, encrypt_pdf(plain, user_password="2064"), filename="known.pdf"
    )
    locked_id = _seed_stored_pdf(
        cli_data_dir, encrypt_pdf(plain, user_password="not-configured"), filename="locked.pdf"
    )
    plain_failed_id = _seed_stored_pdf(cli_data_dir, plain, filename="plain.pdf")

    result = runner.invoke(app, ["sweep-encrypted"])
    assert result.exit_code == 0, result.output
    assert f"{unlockable_id}\tknown.pdf\tunlockable" in result.output
    assert f"{locked_id}\tlocked.pdf\tlocked" in result.output
    assert f"{plain_failed_id}\t" not in result.output  # not encrypted → not listed
    assert "3 failed PDF(s) scanned, 2 encrypted, 1 unlockable" in result.output
    assert f"--apply --ids {unlockable_id}" in result.output


def test_sweep_encrypted_apply_unlocks_in_place(cli_data_dir: str, tmp_path) -> None:
    plain = _make_pdf(tmp_path, "Vertrouwelijk")
    encrypted = encrypt_pdf(plain, user_password="2064")
    old_sha = hashlib.sha256(encrypted).hexdigest()
    document_id = _seed_stored_pdf(cli_data_dir, encrypted, filename="known.pdf")

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["sweep-encrypted", "--apply", "--ids", str(document_id)])
    assert result.exit_code == 0, result.output
    assert f"unlocked {document_id}" in result.output

    # Row now points at the decrypted content and is reset for reprocessing. The
    # new sha is read from the DB, not recomputed: pikepdf.save is not guaranteed
    # to be byte-stable across calls on every libqpdf build, so the assertion is
    # "the row changed to a decrypted, password-free file", not a fixed hash.
    [(new_sha, status, ocr_text)] = fetch_all(
        cli_data_dir,
        "SELECT sha256, status, ocr_text FROM documents WHERE id = :id",
        id=document_id,
    )
    assert new_sha != old_sha
    assert (status, ocr_text) == ("received", None)

    # The stored original reopens without a password; the old file is gone.
    with pikepdf.open(io.BytesIO(path_for(new_sha).read_bytes())):
        pass
    assert not path_for(old_sha).exists()

    # Provenance event records the exact old→new transition, and a
    # process_document job was re-queued.
    events = fetch_all(
        cli_data_dir,
        "SELECT detail->>'old_sha256', detail->>'new_sha256' FROM ingestion_events "
        "WHERE document_id = :id AND event = 'pdf_unlocked_backfill'",
        id=document_id,
    )
    assert events == [(old_sha, new_sha)]
    queued = [
        j for j in connector.jobs.values() if j["task_name"] == "library.jobs.process_document"
    ]
    assert [j["args"]["document_id"] for j in queued] == [document_id]


def test_sweep_encrypted_apply_refuses_non_candidate(cli_data_dir: str, tmp_path) -> None:
    plain = _make_pdf(tmp_path, "Geheim")
    locked_id = _seed_stored_pdf(
        cli_data_dir, encrypt_pdf(plain, user_password="not-configured"), filename="locked.pdf"
    )
    before = fetch_all(cli_data_dir, "SELECT sha256 FROM documents WHERE id = :id", id=locked_id)

    result = runner.invoke(app, ["sweep-encrypted", "--apply", "--ids", str(locked_id)])
    assert result.exit_code == 1
    assert "refusing" in result.output
    assert "nothing changed" in result.output
    # Untouched.
    after = fetch_all(cli_data_dir, "SELECT sha256 FROM documents WHERE id = :id", id=locked_id)
    assert after == before


def test_sweep_encrypted_apply_skips_collision(
    cli_data_dir: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _make_pdf(tmp_path, "Dubbel")
    encrypted = encrypt_pdf(plain, user_password="2064")
    old_sha = hashlib.sha256(encrypted).hexdigest()
    # Force a deterministic decrypted payload so the collision is exact
    # regardless of pikepdf.save byte-stability across libqpdf builds.
    decrypted = b"%PDF-1.7 fake-decrypted-collision\n"
    decrypted_sha = hashlib.sha256(decrypted).hexdigest()
    monkeypatch.setattr(cli_module, "unlock_pdf", lambda content, passwords: decrypted)
    document_id = _seed_stored_pdf(cli_data_dir, encrypted, filename="known.pdf")
    # Another document already holds the decrypted content (sha collision).
    existing_id = _seed_stored_pdf(
        cli_data_dir,
        b"placeholder",
        filename="already.pdf",
        status=DocumentStatus.INDEXED,
        store_file=False,
    )
    execute_sql(
        cli_data_dir,
        "UPDATE documents SET sha256 = :sha WHERE id = :id",
        sha=decrypted_sha,
        id=existing_id,
    )

    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        result = runner.invoke(app, ["sweep-encrypted", "--apply", "--ids", str(document_id)])
    assert result.exit_code == 0, result.output
    assert (
        f"{document_id} skipped — decrypted content already exists as document {existing_id}"
        in (result.output)
    )
    # The locked document is untouched (still the encrypted sha).
    rows = fetch_all(cli_data_dir, "SELECT sha256 FROM documents WHERE id = :id", id=document_id)
    assert rows == [(old_sha,)]
