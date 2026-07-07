"""Build minimal but valid ``.docx`` bytes on the fly for ingest tests.

Fixture strategy matches ``ocr_fixtures``: nothing binary is checked in. A
``.docx`` is an OPC zip (``[Content_Types].xml`` + ``_rels/.rels`` +
``word/document.xml``); the smallest WordprocessingML that ``filetype.guess``
identifies as the Office type and that ``mammoth`` can convert is built here
directly with ``zipfile`` — no ``python-docx`` dependency.
"""

import io
import zipfile
from xml.sax.saxutils import escape

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _para(text: str, style: str | None = None) -> str:
    pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{pr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _cell(text: str) -> str:
    return (
        f'<w:tc><w:tcPr/><w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t>'
        "</w:r></w:p></w:tc>"
    )


def make_docx(
    *,
    heading: str = "Enrolment Form",
    paragraph: str = "Please fill in your details.",
    table_rows: list[tuple[str, str]] | None = None,
    marker: str | None = None,
) -> bytes:
    """Return ``.docx`` bytes with a heading, a paragraph, and a 2-column table.

    ``table_rows`` defaults to a small labelled table; pass ``[]`` for no table.
    ``marker`` appends a unique paragraph so the bytes (and thus the sha256)
    differ between calls — integration tests share one database with no
    per-test truncation, so identical content would otherwise dedup. Pass a
    unique value (e.g. ``uuid4().hex``) in any test that ingests into the DB.
    """
    if table_rows is None:
        table_rows = [("Name", "Age"), ("Alice", "30")]
    rows = "".join(f"<w:tr>{_cell(a)}{_cell(b)}</w:tr>" for a, b in table_rows)
    table = (
        f"<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>{rows}</w:tbl>"
        if table_rows
        else ""
    )
    marker_para = _para(f"ref {marker}") if marker is not None else ""
    body = _para(heading, "Heading1") + _para(paragraph) + table + marker_para
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
