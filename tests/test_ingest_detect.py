"""Unit tests for MIME detection in library.ingest (pure, no DB, no network)."""

from library.ingest import detect_mime


def test_md_filename_with_utf8_content_is_markdown() -> None:
    assert detect_mime(b"# Heading\n\nbody text", None, "notes.md") == "text/markdown"
    assert detect_mime(b"# Heading", None, "notes.markdown") == "text/markdown"


def test_txt_filename_with_utf8_content_is_plain() -> None:
    assert detect_mime(b"just some plain text", None, "note.txt") == "text/plain"


def test_utf8_content_without_md_filename_is_plain() -> None:
    # No filename, or a non-markdown suffix, falls back to plain text.
    assert detect_mime(b"plain body", None, None) == "text/plain"
    assert detect_mime(b"plain body", None, "note.rst") == "text/plain"


def test_claimed_x_markdown_is_normalised() -> None:
    # Undecodable content (so no UTF-8 branch) with a claimed markdown alias.
    assert detect_mime(b"\xff\xfe\xfd\xfc", "text/x-markdown", None) == "text/markdown"
    assert detect_mime(b"\xff\xfe\xfd\xfc", "text/markdown; charset=utf-8", None) == "text/markdown"


def test_binary_sniff_wins_over_md_filename() -> None:
    # A real PDF carrying a .md name: the magic-bytes sniff must win.
    pdf = b"%PDF-1.4\n%%EOF\n"
    assert detect_mime(pdf, None, "tricky.md") == "application/pdf"
