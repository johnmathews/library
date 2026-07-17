"""Unit tests for library.pdf_unlock (pure over bytes, no DB/config)."""

import io

import pikepdf
import pytest

from library.pdf_unlock import PdfLockedError, unlock_pdf
from tests.ocr_fixtures import encrypt_pdf, make_text_pdf


def _plain_pdf(tmp_path) -> bytes:
    return make_text_pdf(tmp_path / "plain.pdf", lines=["Hello world"]).read_bytes()


def _opens_without_password(content: bytes) -> bool:
    with pikepdf.open(io.BytesIO(content)):
        return True


def test_unencrypted_pdf_passes_through_unchanged(tmp_path) -> None:
    content = _plain_pdf(tmp_path)
    assert unlock_pdf(content, ["2064"]) is content


def test_correct_password_unlocks_and_reopens_without_password(tmp_path) -> None:
    encrypted = encrypt_pdf(_plain_pdf(tmp_path), user_password="2064")
    with pytest.raises(pikepdf.PasswordError):
        _opens_without_password(encrypted)

    unlocked = unlock_pdf(encrypted, ["2064"])

    assert unlocked != encrypted
    assert _opens_without_password(unlocked)


def test_first_matching_password_in_the_list_wins(tmp_path) -> None:
    encrypted = encrypt_pdf(_plain_pdf(tmp_path), user_password="letmein")
    unlocked = unlock_pdf(encrypted, ["nope", "letmein", "2064"])
    assert _opens_without_password(unlocked)


def test_empty_password_is_always_tried(tmp_path) -> None:
    # A PDF whose user password is the empty string opens even when the
    # configured list is empty.
    encrypted = encrypt_pdf(_plain_pdf(tmp_path), user_password="")
    unlocked = unlock_pdf(encrypted, [])
    assert _opens_without_password(unlocked)


def test_no_matching_password_raises_and_never_leaks_the_attempts(tmp_path) -> None:
    encrypted = encrypt_pdf(_plain_pdf(tmp_path), user_password="s3cret")
    with pytest.raises(PdfLockedError) as excinfo:
        unlock_pdf(encrypted, ["2064", "hunter2"])
    # The exception reports a count, never the password values that were tried.
    message = str(excinfo.value)
    assert "2064" not in message
    assert "hunter2" not in message


def test_corrupt_bytes_pass_through_unchanged(tmp_path) -> None:
    # Not a valid PDF: don't fail the caller — the OCR pipeline handles
    # unreadable PDFs downstream. Mirrors the best-effort docx branch.
    garbage = b"%PDF-1.4 not really a pdf"
    assert unlock_pdf(garbage, ["2064"]) is garbage
