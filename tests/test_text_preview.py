"""Unit tests for the markdown -> plain-text excerpt helper."""

from library.text_preview import markdown_excerpt


def test_none_input_returns_none() -> None:
    assert markdown_excerpt(None) is None


def test_blank_and_whitespace_only_returns_none() -> None:
    assert markdown_excerpt("") is None
    assert markdown_excerpt("   \n\t  ") is None


def test_headings_and_emphasis_stripped() -> None:
    result = markdown_excerpt("# Heading\n\n**bold** and _italic_ and ~~strike~~ text")
    assert result is not None
    assert "#" not in result
    assert "*" not in result
    assert "_" not in result
    assert "~" not in result
    assert "Heading" in result
    assert "bold" in result
    assert "italic" in result
    assert "strike" in result


def test_link_text_preserved_url_dropped() -> None:
    result = markdown_excerpt("See [the invoice](https://example.com/inv.pdf) now")
    assert result is not None
    assert "the invoice" in result
    assert "https://example.com" not in result
    assert "[" not in result and "]" not in result and "(" not in result


def test_image_alt_dropped() -> None:
    result = markdown_excerpt("![company logo](logo.png) Acme Corp")
    assert result is not None
    assert "Acme Corp" in result
    assert "logo.png" not in result
    assert "!" not in result


def test_code_fences_and_inline_code_stripped() -> None:
    text = "Some text\n\n```python\nprint('hi')\n```\n\nand `inline` code"
    result = markdown_excerpt(text)
    assert result is not None
    assert "`" not in result
    assert "Some text" in result
    assert "inline" in result


def test_blockquote_and_list_bullets_stripped() -> None:
    text = "> quoted line\n\n- first\n- second\n\n1. numbered\n2. next"
    result = markdown_excerpt(text)
    assert result is not None
    assert not result.lstrip().startswith(">")
    assert "quoted line" in result
    assert "first" in result
    assert "numbered" in result
    # No leading bullet markers survive.
    assert "- first" not in result
    assert "1. numbered" not in result


def test_whitespace_collapsed_to_single_line() -> None:
    result = markdown_excerpt("line one\n\n\nline   two\t\tend")
    assert result == "line one line two end"


def test_length_cap_enforced_with_ellipsis() -> None:
    body = "word " * 100
    result = markdown_excerpt(body, max_chars=40)
    assert result is not None
    assert result.endswith("…")
    # The ellipsis is appended beyond the cap, so the text portion is capped.
    assert len(result) <= 41


def test_short_text_not_truncated() -> None:
    result = markdown_excerpt("short body", max_chars=240)
    assert result == "short body"


def test_realistic_invoice_body() -> None:
    body = (
        "# Invoice 2026-0042\n\n"
        "**From:** Acme Utilities BV  \n"
        "**To:** John Mathews\n\n"
        "## Summary\n\n"
        "Your electricity bill for *May 2026* is due.\n\n"
        "- Amount: EUR 123.45\n"
        "- Due date: 2026-06-01\n\n"
        "> Please pay via [the portal](https://pay.example.com).\n"
    )
    result = markdown_excerpt(body)
    assert result is not None
    assert "\n" not in result
    assert "#" not in result
    assert "*" not in result
    assert ">" not in result
    assert "Invoice 2026-0042" in result
    assert "the portal" in result
    assert "https://pay.example.com" not in result
