"""Unit tests for the ``.docx`` -> Markdown conversion helper."""

from library.docx import (
    CONVERTED_MARKDOWN_NAME,
    DOCX_MIME,
    docx_to_markdown,
)
from tests.docx_fixtures import make_docx


def test_docx_mime_constant() -> None:
    assert DOCX_MIME == ("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert CONVERTED_MARKDOWN_NAME == "converted.md"


def test_docx_to_markdown_preserves_heading_paragraph_and_table() -> None:
    data = make_docx(
        heading="Enrolment Form",
        paragraph="Please fill in your details.",
        table_rows=[("Name", "Age"), ("Alice", "30")],
    )
    md = docx_to_markdown(data)

    assert "# Enrolment Form" in md  # heading style survives (ATX)
    assert "Please fill in your details." in md  # free text survives
    assert "| Name | Age |" in md  # table survives
    assert "| Alice | 30 |" in md


def test_docx_to_markdown_escapes_special_content() -> None:
    # XML-special characters in the source must round-trip as literal text.
    data = make_docx(paragraph="Owner: Smith & Co <legal>", table_rows=[])
    md = docx_to_markdown(data)
    assert "Smith & Co <legal>" in md
