"""Tests for the `library` account-management CLI (typer)."""

import uuid
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from library.auth.passwords import verify_password
from library.cli import app
from library.config import get_settings
from tests.conftest import create_user, fetch_all
from tests.test_auth import execute_sql

pytestmark = pytest.mark.integration

runner = CliRunner()


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
