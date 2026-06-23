from library.models import DocumentChunk, DocumentPage, DocumentStatus


def test_markdown_status_value() -> None:
    assert DocumentStatus.MARKDOWN == "markdown"


def test_document_page_columns() -> None:
    cols = {c.name for c in DocumentPage.__table__.columns}
    assert cols == {"document_id", "page_number", "markdown", "char_count", "created_at"}
    pk = {c.name for c in DocumentPage.__table__.primary_key.columns}
    assert pk == {"document_id", "page_number"}


def test_document_chunk_has_page_number() -> None:
    col = DocumentChunk.__table__.columns["page_number"]
    assert col.nullable is True
