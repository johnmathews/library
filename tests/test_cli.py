"""Tests for the `library` account-management CLI (typer)."""

import asyncio
import datetime
import uuid
from collections.abc import Iterator

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

import library.cli as cli_module
from library.auth.passwords import verify_password
from library.cli import app
from library.config import get_settings
from library.extraction.judge import FieldVerdict, JudgeResult
from library.jobs import job_app
from library.models import Document, DocumentChunk, DocumentSource, DocumentStatus, ReviewStatus
from tests.conftest import create_user, fetch_all
from tests.test_auth import execute_sql

pytestmark = pytest.mark.integration

runner = CliRunner()


def _seed_document(
    database_url: str, marker: str, *, ocr_text: str | None, with_chunk: bool
) -> int:
    """Insert a document (optionally with one chunk) and return its id."""

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
