# Document Markdown Layer + Page-Aware Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a per-page Claude-vision markdown rendering of each document (grounded on OCR text), store it in a new `document_pages` table, chunk/embed from it with page provenance, and surface page-numbered citations in Ask through to a PDF deep-link.

**Architecture:** A new best-effort pipeline stage `markdown` (between `extract` and `embed`) rasterizes pages with pypdfium2, sends the page images plus the full OCR text to `client.messages.parse()` with a `DocumentMarkdown` schema, and writes one `document_pages` row per page. The `embed` stage then chunks each page's markdown (falling back to `ocr_text` when no pages exist), tagging every `document_chunks` row with its `page_number`. Retrieval carries that page number through `SemanticHit` → `AskCitation` → the `/api/ask` schema → the frontend, which renders `Title, p.N` and deep-links the PDF iframe via `#page=N`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, Procrastinate, Anthropic SDK (`messages.parse`), pypdfium2 + Pillow, Typer; Vue 3 + TS + Vitest + Playwright. `uv` for all deps/tests, `pytest` + `coverage`, `ruff` format + lint.

## Global Constraints

- Type annotations on every function signature and non-obvious variable. Target Python 3.13.
- Use `uv` for all dependency/test commands (`uv run pytest …`, `uv run ruff …`). Never `pip`.
- `ruff format` + `ruff check` must be clean before every commit.
- Tests are required for every task; never skip them. Backend `pytest`, frontend `vitest`, e2e `playwright`.
- **Best-effort invariant:** the markdown and embed stages must NEVER fail a document. Disabled feature, missing API key, blown budget, unusable input, or API error ⇒ record an ingestion event and return normally; the document still reaches `indexed`.
- FTS stays on `ocr_text` — do not add markdown to the search vectors. _(Later
  revisited: migration `0025_fts_page_markdown` (2026-07-10) folds the page
  markdown into FTS via `coalesce(pages_markdown, ocr_text)` so image-PDF bodies
  are findable — see [ingestion.md](../../ingestion.md) "Markdown layer".)_
- Default markdown model is `claude-haiku-4-5`; no escalation this phase.
- Record cost per run in the `markdown_completed` event so the daily budget guard can sum it. The markdown budget is **independent** of the extraction budget (scope each spend query to its own event name).
- Migration revision is `0007`, `down_revision = "0006"`.
- End every commit message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Spec: `docs/superpowers/specs/2026-06-21-markdown-layer-design.md`.

---

### Task 1: Config settings

**Files:**
- Modify: `src/library/config.py` (after the extraction settings, ~line 41)
- Test: `tests/test_config.py` (add a test; create if absent — follow existing config-test style)

**Interfaces:**
- Produces: `Settings.markdown_enabled: bool`, `Settings.markdown_model: str`, `Settings.markdown_daily_budget_usd: float`, `Settings.markdown_max_pages: int`, `Settings.markdown_page_batch: int`, `Settings.markdown_image_long_side_px: int` (env prefix `LIBRARY_`, so `LIBRARY_MARKDOWN_ENABLED`, etc.).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from library.config import Settings


def test_markdown_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.markdown_enabled is True
    assert settings.markdown_model == "claude-haiku-4-5"
    assert settings.markdown_daily_budget_usd == 5.0
    assert settings.markdown_max_pages == 20
    assert settings.markdown_page_batch == 10
    assert settings.markdown_image_long_side_px == 1600


def test_markdown_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARY_MARKDOWN_ENABLED", "false")
    monkeypatch.setenv("LIBRARY_MARKDOWN_MAX_PAGES", "5")
    settings = Settings(_env_file=None)
    assert settings.markdown_enabled is False
    assert settings.markdown_max_pages == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -k markdown -v`
Expected: FAIL (`AttributeError`/validation: no `markdown_enabled`).

- [ ] **Step 3: Add the settings**

```python
# src/library/config.py — directly after the extraction_* / before embedding_* settings
    markdown_enabled: bool = True
    markdown_model: str = "claude-haiku-4-5"
    markdown_daily_budget_usd: float = 5.0
    markdown_max_pages: int = 20
    markdown_page_batch: int = 10
    markdown_image_long_side_px: int = 1600
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -k markdown -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/config.py tests/test_config.py
uv run ruff check src/library/config.py tests/test_config.py
git add src/library/config.py tests/test_config.py
git commit -m "feat(markdown): add markdown layer config settings

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Models + migration 0007

**Files:**
- Modify: `src/library/models.py` (`DocumentStatus` enum ~line 65; `Document` relationships ~line 304; `DocumentChunk` ~line 333; add `DocumentPage` after `DocumentChunk`)
- Create: `migrations/versions/0007_markdown_layer.py`
- Test: `tests/test_models_markdown.py`; `tests/test_migrations.py` (extend if it exists; else add the round-trip test shown)

**Interfaces:**
- Produces: `DocumentStatus.MARKDOWN = "markdown"`; `DocumentPage(document_id, page_number, markdown, char_count, created_at)` PK `(document_id, page_number)`, `Document.pages` relationship (ordered, `lazy="raise"`); `DocumentChunk.page_number: int | None`.

Note: the `documents.status` column is `Enum(..., native_enum=False)` with **no** CHECK constraint (verified in migration 0001), stored as VARCHAR(16). Adding the `"markdown"` value needs **no** DDL for that column. Migration 0007 only creates `document_pages` and adds `document_chunks.page_number`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_markdown.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models_markdown.py -v`
Expected: FAIL (`AttributeError: MARKDOWN` / no `DocumentPage`).

- [ ] **Step 3: Implement the model changes**

```python
# src/library/models.py — in DocumentStatus (StrEnum), insert MARKDOWN between EXTRACT and EMBED:
    EXTRACT = "extract"
    MARKDOWN = "markdown"
    EMBED = "embed"
```

```python
# src/library/models.py — add page_number to DocumentChunk (after `chunk_index`):
    page_number: Mapped[int | None] = mapped_column(Integer)
```

```python
# src/library/models.py — add to Document, next to the `chunks` relationship:
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="DocumentPage.page_number",
    )
```

```python
# src/library/models.py — new class directly after DocumentChunk:
class DocumentPage(Base):
    """Per-page markdown rendering of a document — the canonical "understood" layer.

    Generated by Claude vision grounded on the OCR text. One row per page;
    the full-document markdown is these rows ordered by ``page_number``. This
    is the source for page-aware chunking (``DocumentChunk.page_number``) and
    the detail-view markdown tab. Like ``chunks``, never wanted on a normal
    document load (``lazy="raise"`` on the relationship).
    """

    __tablename__ = "document_pages"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    markdown: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="pages")
```

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/0007_markdown_layer.py
"""markdown layer

Add the document_pages table and document_chunks.page_number.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_pages_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "page_number", name=op.f("pk_document_pages")),
    )
    op.add_column("document_chunks", sa.Column("page_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "page_number")
    op.drop_table("document_pages")
```

- [ ] **Step 5: Add a migration round-trip test**

```python
# tests/test_migrations.py  (add this test; the helper `alembic_config()` already
# exists in this file in the repo — reuse it. If this file does not exist, create it
# mirroring how other tests build an Alembic Config pointed at the test database.)
from alembic import command


def test_0007_upgrade_then_downgrade(alembic_config) -> None:
    command.upgrade(alembic_config, "0007")
    command.downgrade(alembic_config, "0006")
    command.upgrade(alembic_config, "head")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_models_markdown.py tests/test_migrations.py -v`
Expected: PASS. Also confirm autogenerate sees no drift:
Run: `uv run alembic check` (or the project's equivalent) — Expected: "No new upgrade operations detected."

- [ ] **Step 7: Format, lint, commit**

```bash
uv run ruff format src/library/models.py migrations/versions/0007_markdown_layer.py tests/test_models_markdown.py tests/test_migrations.py
uv run ruff check src/library/models.py migrations/versions/0007_markdown_layer.py tests/test_models_markdown.py tests/test_migrations.py
git add src/library/models.py migrations/versions/0007_markdown_layer.py tests/test_models_markdown.py tests/test_migrations.py
git commit -m "feat(markdown): document_pages table, chunk page_number, markdown status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Page renderer

**Files:**
- Create: `src/library/markdown/__init__.py` (empty)
- Create: `src/library/markdown/renderer.py`
- Test: `tests/markdown/__init__.py` (empty), `tests/markdown/test_renderer.py`

**Interfaces:**
- Produces: `render_page_images(mime_type: str, original_path: Path, derived: Path, *, max_pages: int, long_side_px: int) -> list[bytes]` — JPEG bytes per page, in page order, capped at `max_pages`, longest side ≤ `long_side_px`. Returns `[]` for `text/plain` and for input with no renderable artifact.

Reference existing rasterization: `src/library/thumbnails.py` (pypdfium2 + Pillow) and `src/library/ocr/tesseract.py` (the confidence probe rasterizes `searchable.pdf` at 300 dpi). Follow those import patterns.

- [ ] **Step 1: Write the failing test**

```python
# tests/markdown/test_renderer.py
import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from library.markdown.renderer import render_page_images


def _make_pdf(path: Path, pages: int) -> None:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)  # A4 points
    pdf.save(str(path))


def test_renders_one_jpeg_per_pdf_page(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 3)
    images = render_page_images(
        "application/pdf", pdf, tmp_path, max_pages=20, long_side_px=1600
    )
    assert len(images) == 3
    for raw in images:
        img = Image.open(io.BytesIO(raw))
        assert img.format == "JPEG"
        assert max(img.size) <= 1600


def test_caps_at_max_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 5)
    images = render_page_images("application/pdf", pdf, tmp_path, max_pages=2, long_side_px=1600)
    assert len(images) == 2


def test_text_plain_returns_empty(tmp_path: Path) -> None:
    txt = tmp_path / "x"
    txt.write_text("hi")
    assert render_page_images("text/plain", txt, tmp_path, max_pages=20, long_side_px=1600) == []


def test_jpeg_image_single_page_downscaled(tmp_path: Path) -> None:
    src = tmp_path / "orig"
    Image.new("RGB", (4000, 2000), "white").save(src, format="JPEG")
    images = render_page_images("image/jpeg", src, tmp_path, max_pages=20, long_side_px=1600)
    assert len(images) == 1
    assert max(Image.open(io.BytesIO(images[0])).size) == 1600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/markdown/test_renderer.py -v`
Expected: FAIL (module `library.markdown.renderer` not found).

- [ ] **Step 3: Implement the renderer**

```python
# src/library/markdown/renderer.py
"""Rasterize a document's pages to JPEG bytes for vision markdown generation.

Mirrors the existing pypdfium2/Pillow usage in ``library.thumbnails`` and the
OCR confidence probe. PDFs render each page; single images render once; HEIC
uses the derived ``converted.jpg``; TIFF uses the OCR-produced
``searchable.pdf``; ``text/plain`` has no visual and yields no images.
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# Render PDFs at this scale relative to the PDF's point size before the
# long-side downscale; 2.0 (~144 dpi) is plenty for table fidelity.
_PDF_RENDER_SCALE: float = 2.0


def _encode(image: Image.Image, long_side_px: int) -> bytes:
    rgb = image.convert("RGB")
    longest = max(rgb.size)
    if longest > long_side_px:
        ratio = long_side_px / longest
        rgb = rgb.resize((round(rgb.width * ratio), round(rgb.height * ratio)))
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _render_pdf(path: Path, *, max_pages: int, long_side_px: int) -> list[bytes]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        count = min(len(pdf), max_pages)
        images: list[bytes] = []
        for index in range(count):
            page = pdf[index]
            bitmap = page.render(scale=_PDF_RENDER_SCALE)
            images.append(_encode(bitmap.to_pil(), long_side_px))
        return images
    finally:
        pdf.close()


def render_page_images(
    mime_type: str,
    original_path: Path,
    derived: Path,
    *,
    max_pages: int,
    long_side_px: int,
) -> list[bytes]:
    """One JPEG per page (in order), capped at ``max_pages``; ``[]`` when none."""
    if mime_type == "application/pdf":
        return _render_pdf(original_path, max_pages=max_pages, long_side_px=long_side_px)
    if mime_type == "image/tiff":
        searchable = derived / "searchable.pdf"
        if not searchable.exists():
            return []
        return _render_pdf(searchable, max_pages=max_pages, long_side_px=long_side_px)
    if mime_type in ("image/jpeg", "image/png"):
        return [_encode(Image.open(original_path), long_side_px)]
    if mime_type in ("image/heic", "image/heif"):
        converted = derived / "converted.jpg"
        if not converted.exists():
            return []
        return [_encode(Image.open(converted), long_side_px)]
    return []  # text/plain and anything else: no renderable visual
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/markdown/test_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/markdown/ tests/markdown/
uv run ruff check src/library/markdown/ tests/markdown/
git add src/library/markdown/__init__.py src/library/markdown/renderer.py tests/markdown/__init__.py tests/markdown/test_renderer.py
git commit -m "feat(markdown): page rasterizer for vision input

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Markdown schema + generator

**Files:**
- Create: `src/library/markdown/schema.py`, `src/library/markdown/generator.py`
- Test: `tests/markdown/test_generator.py`

**Interfaces:**
- Consumes: `render_page_images(...) -> list[bytes]` (Task 3); `Settings.markdown_model`, `Settings.markdown_page_batch` (Task 1).
- Produces:
  - `schema.py`: `PageMarkdown(BaseModel){page_number: int, markdown: str}`, `DocumentMarkdown(BaseModel){pages: list[PageMarkdown]}`.
  - `generator.py`: `PROMPT_VERSION: str = "2026-06-21.1"`, `MarkdownSkipped(Exception){reason: str}`, `GeneratedPage(page_number: int, markdown: str)`, `MarkdownResult(pages: list[GeneratedPage], model: str, prompt_version: str, input_tokens: int, output_tokens: int, cost_usd: float)`, and `async def generate_markdown(document: Document, ocr_text: str, page_images: list[bytes], *, client: AsyncAnthropic, settings: Settings) -> MarkdownResult`.
- Cost: reuse `library.extraction.extractor.estimate_cost_usd` (do not duplicate the pricing table).

Page-number assignment is **positional and absolute**: within each batch, returned pages are sorted by their reported `page_number`, then re-numbered `offset + 1, offset + 2, …` by position, clamped to the batch's image count. This tolerates a model that mis-numbers or returns a wrong count without ever inventing a page that has no image. A batch yielding zero pages contributes nothing; if the whole document yields zero pages, raise `MarkdownSkipped("input_unusable", …)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/markdown/test_generator.py
from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.config import Settings
from library.markdown.generator import MarkdownSkipped, generate_markdown
from library.markdown.schema import DocumentMarkdown, PageMarkdown


class _FakeMessages:
    def __init__(self, responses: list[DocumentMarkdown]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self._responses.pop(0)
        usage = SimpleNamespace(input_tokens=100, output_tokens=200)
        return SimpleNamespace(parsed_output=parsed, usage=usage)


class _FakeClient:
    def __init__(self, responses: list[DocumentMarkdown]) -> None:
        self.messages = _FakeMessages(responses)


def _settings(batch: int = 10) -> Settings:
    return Settings(_env_file=None, markdown_page_batch=batch, markdown_model="claude-haiku-4-5")


@pytest.mark.anyio
async def test_single_batch_assigns_absolute_pages() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient(
        [DocumentMarkdown(pages=[PageMarkdown(page_number=1, markdown="# A"),
                                 PageMarkdown(page_number=2, markdown="# B")])]
    )
    result = await generate_markdown(doc, "ocr", [b"img1", b"img2"], client=client, settings=_settings())
    assert [(p.page_number, p.markdown) for p in result.pages] == [(1, "# A"), (2, "# B")]
    assert result.input_tokens == 100 and result.output_tokens == 200
    assert result.cost_usd > 0


@pytest.mark.anyio
async def test_batches_offset_page_numbers() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient(
        [
            DocumentMarkdown(pages=[PageMarkdown(page_number=1, markdown="p1"),
                                    PageMarkdown(page_number=2, markdown="p2")]),
            DocumentMarkdown(pages=[PageMarkdown(page_number=1, markdown="p3")]),
        ]
    )
    result = await generate_markdown(doc, "ocr", [b"a", b"b", b"c"], client=client, settings=_settings(batch=2))
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert [p.markdown for p in result.pages] == ["p1", "p2", "p3"]
    assert len(client.messages.calls) == 2


@pytest.mark.anyio
async def test_no_pages_raises_skip() -> None:
    doc = SimpleNamespace(id=1)
    client = _FakeClient([DocumentMarkdown(pages=[])])
    with pytest.raises(MarkdownSkipped):
        await generate_markdown(doc, "ocr", [b"a"], client=client, settings=_settings())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/markdown/test_generator.py -v`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement the schema**

```python
# src/library/markdown/schema.py
"""Structured-output schema for vision markdown generation.

``DocumentMarkdown`` is the ``output_format`` passed to
``client.messages.parse()``. One ``PageMarkdown`` per input page image.
"""

from pydantic import BaseModel, ConfigDict


class PageMarkdown(BaseModel):
    """Markdown for one rendered page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int
    markdown: str


class DocumentMarkdown(BaseModel):
    """All pages of one document, in order."""

    model_config = ConfigDict(extra="forbid")

    pages: list[PageMarkdown]
```

- [ ] **Step 4: Implement the generator**

```python
# src/library/markdown/generator.py
"""Call Claude vision to render document pages as markdown, grounded on OCR text.

One ``messages.parse`` call per page-image batch; page numbers are assigned
positionally and absolutely so a mis-numbered or short response can never
invent a page without an image. API errors propagate (the SDK retried 5xx);
the caller decides what a failure means for the document.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from library.config import Settings
from library.extraction.extractor import estimate_cost_usd
from library.markdown.schema import DocumentMarkdown
from library.models import Document

logger = logging.getLogger(__name__)

# Bump when the prompt or schema changes meaningfully; stored per run.
PROMPT_VERSION: str = "2026-06-21.1"

# Markdown output can be large (a full multi-page document); allow room.
MAX_OUTPUT_TOKENS: int = 8_192
# OCR grounding text is truncated to cap spend; layout comes from the images.
MAX_GROUNDING_CHARS: int = 12_000

SYSTEM_PROMPT: str = """\
You convert scanned/þhotographed document pages into clean GitHub-flavored
markdown for "Library", a self-hosted family document archive (Dutch, English,
or mixed household paperwork).

For EACH page image you are given, produce faithful markdown:
- Reproduce real tables as markdown tables. Reconstruct borderless/columnar
  tables from the visual layout into proper markdown tables.
- Use headings, lists, and emphasis to match the document's structure.
- Transcribe text in the document's own language; do not translate or
  summarize. Do not invent content that is not on the page.
- The accompanying OCR text is a spelling/figure reference for exact numbers,
  names, and codes — prefer it when the image is ambiguous, but trust the
  image for layout and structure.

Return one entry per input page, in order, with page_number starting at 1 for
the first image you were given in this request.
"""


class MarkdownSkipped(Exception):
    """Markdown generation cannot run/produce output; skip gracefully."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class GeneratedPage:
    page_number: int  # absolute, 1-based
    markdown: str


@dataclass(frozen=True)
class MarkdownResult:
    pages: list[GeneratedPage]
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _image_block(jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii"),
        },
    }


async def generate_markdown(
    document: Document,
    ocr_text: str,
    page_images: list[bytes],
    *,
    client: AsyncAnthropic,
    settings: Settings,
) -> MarkdownResult:
    """Render ``page_images`` to per-page markdown; raise MarkdownSkipped if none."""
    batch_size = max(settings.markdown_page_batch, 1)
    grounding = ocr_text.strip()[:MAX_GROUNDING_CHARS]
    pages: list[GeneratedPage] = []
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    offset = 0

    for start in range(0, len(page_images), batch_size):
        batch = page_images[start : start + batch_size]
        content: list[dict] = [_image_block(image) for image in batch]
        content.append(
            {
                "type": "text",
                "text": (
                    "Convert these page images to markdown. "
                    "OCR text reference for this document:\n\n" + grounding
                ),
            }
        )
        response = await client.messages.parse(
            model=settings.markdown_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=DocumentMarkdown,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cost += estimate_cost_usd(
            settings.markdown_model, response.usage.input_tokens, response.usage.output_tokens
        )
        parsed = response.parsed_output
        returned = sorted(parsed.pages, key=lambda p: p.page_number) if parsed else []
        for index, page in enumerate(returned[: len(batch)]):
            pages.append(GeneratedPage(page_number=offset + index + 1, markdown=page.markdown))
        offset += len(batch)

    if not pages:
        raise MarkdownSkipped("input_unusable", "model returned no pages")

    return MarkdownResult(
        pages=pages,
        model=settings.markdown_model,
        prompt_version=PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
```

Note: remove the stray non-ASCII character in the system prompt's first line ("photographed") — type it cleanly.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/markdown/test_generator.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format src/library/markdown/ tests/markdown/
uv run ruff check src/library/markdown/ tests/markdown/
git add src/library/markdown/schema.py src/library/markdown/generator.py tests/markdown/test_generator.py
git commit -m "feat(markdown): vision generator + structured page schema

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Markdown apply (guards, budget, persistence)

**Files:**
- Create: `src/library/markdown/apply.py`
- Modify: `src/library/extraction/apply.py` (scope `todays_spend_usd` to extraction events — see below)
- Test: `tests/markdown/test_apply.py`

**Interfaces:**
- Consumes: `render_page_images` (Task 3), `generate_markdown`/`MarkdownSkipped`/`PROMPT_VERSION` (Task 4), `DocumentPage` (Task 2), `Settings.markdown_*` (Task 1).
- Produces: `async def apply_markdown(session: AsyncSession, document: Document, settings: Settings) -> None`; `async def todays_markdown_spend_usd(session: AsyncSession) -> float`.

Budget independence: `extraction.apply.todays_spend_usd` currently sums **every** ingestion event carrying `cost_usd`, which would now also count markdown spend. Scope each query to its own event name so the two budgets stay independent.

- [ ] **Step 1: Scope the extraction spend query (+ keep its tests green)**

In `src/library/extraction/apply.py`, change the `where` of `todays_spend_usd` to also filter the event name:

```python
    ).where(
        IngestionEvent.event == "extraction_completed",
        IngestionEvent.detail.has_key("cost_usd"),
        IngestionEvent.created_at >= start_of_day,
    )
```

Run the existing extraction tests to confirm no regression:
Run: `uv run pytest tests/extraction -v`
Expected: PASS.

- [ ] **Step 2: Write the failing test**

```python
# tests/markdown/test_apply.py
from __future__ import annotations

import pytest
from sqlalchemy import select

from library.markdown import apply as markdown_apply
from library.markdown.generator import GeneratedPage, MarkdownResult
from library.models import DocumentPage, IngestionEvent

# Assumes the repo's async DB fixtures: `session` (AsyncSession) and a
# `document_factory` / `make_document` helper used by other tests. Match the
# existing fixture names in tests/conftest.py.


@pytest.mark.anyio
async def test_disabled_records_skip(session, make_document, settings_factory) -> None:
    document = await make_document(session, mime_type="application/pdf", ocr_text="hello world")
    settings = settings_factory(markdown_enabled=False)
    await markdown_apply.apply_markdown(session, document, settings)
    events = (await session.execute(select(IngestionEvent.event))).scalars().all()
    assert "markdown_skipped" in events
    assert (await session.execute(select(DocumentPage))).first() is None


@pytest.mark.anyio
async def test_success_writes_pages_and_event(session, make_document, settings_factory, monkeypatch) -> None:
    document = await make_document(session, mime_type="application/pdf", ocr_text="hello world")
    settings = settings_factory(markdown_enabled=True, anthropic_api_key="sk-test")

    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [b"img1", b"img2"])

    async def fake_generate(*args, **kwargs):
        return MarkdownResult(
            pages=[GeneratedPage(1, "# Page 1"), GeneratedPage(2, "# Page 2")],
            model="claude-haiku-4-5",
            prompt_version="t",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.001,
        )

    monkeypatch.setattr(markdown_apply, "generate_markdown", fake_generate)
    # Stub the Anthropic client context manager so no network call happens.
    monkeypatch.setattr(markdown_apply, "AsyncAnthropic", _stub_anthropic())

    await markdown_apply.apply_markdown(session, document, settings)

    pages = (
        await session.execute(
            select(DocumentPage).where(DocumentPage.document_id == document.id).order_by(DocumentPage.page_number)
        )
    ).scalars().all()
    assert [p.markdown for p in pages] == ["# Page 1", "# Page 2"]
    assert pages[0].char_count == len("# Page 1")
    events = (await session.execute(select(IngestionEvent.event))).scalars().all()
    assert "markdown_completed" in events


@pytest.mark.anyio
async def test_no_images_skips(session, make_document, settings_factory, monkeypatch) -> None:
    document = await make_document(session, mime_type="text/plain", ocr_text="just text")
    settings = settings_factory(markdown_enabled=True, anthropic_api_key="sk-test")
    monkeypatch.setattr(markdown_apply, "render_page_images", lambda *a, **k: [])
    await markdown_apply.apply_markdown(session, document, settings)
    events = (await session.execute(select(IngestionEvent.event))).scalars().all()
    assert "markdown_skipped" in events
```

Add this helper near the top of the test module (a minimal async context-manager stub):

```python
def _stub_anthropic():
    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc) -> None: ...
    return _Client
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/markdown/test_apply.py -v`
Expected: FAIL (`library.markdown.apply` not found).

- [ ] **Step 4: Implement apply**

```python
# src/library/markdown/apply.py
"""Run vision markdown generation for a document and persist per-page rows.

Same invariant as extraction: **markdown never fails a document.** Disabled,
missing key, blown budget, unusable input, API errors — all end in a
skip/failed audit event and a normal return, so the pipeline continues.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import Numeric, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.markdown.generator import (
    PROMPT_VERSION,
    MarkdownSkipped,
    generate_markdown,
)
from library.markdown.renderer import render_page_images
from library.models import Document, DocumentPage, IngestionEvent
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)


async def todays_markdown_spend_usd(session: AsyncSession) -> float:
    """Sum today's (UTC) estimated markdown spend from markdown_completed events."""
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.coalesce(func.sum(IngestionEvent.detail["cost_usd"].astext.cast(Numeric)), 0)
    ).where(
        IngestionEvent.event == "markdown_completed",
        IngestionEvent.detail.has_key("cost_usd"),
        IngestionEvent.created_at >= start_of_day,
    )
    return float((await session.execute(statement)).scalar_one())


async def _record_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, Any]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def apply_markdown(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Generate per-page markdown for one document and persist it (best-effort)."""
    if not settings.markdown_enabled:
        await _record_event(session, document, "markdown_skipped", {"reason": "disabled"})
        return
    if settings.anthropic_api_key is None:
        await _record_event(session, document, "markdown_skipped", {"reason": "missing_api_key"})
        return

    spent = await todays_markdown_spend_usd(session)
    if spent >= settings.markdown_daily_budget_usd:
        await _record_event(
            session,
            document,
            "markdown_skipped",
            {"reason": "budget", "spent_usd": spent, "budget_usd": settings.markdown_daily_budget_usd},
        )
        return

    images = render_page_images(
        document.mime_type,
        path_for(document.sha256),
        derived_dir(document.sha256),
        max_pages=settings.markdown_max_pages,
        long_side_px=settings.markdown_image_long_side_px,
    )
    if not images:
        await _record_event(
            session, document, "markdown_skipped", {"reason": "input_unusable", "mime": document.mime_type}
        )
        return

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            result = await generate_markdown(
                document, document.ocr_text or "", images, client=client, settings=settings
            )
    except MarkdownSkipped as exc:
        await _record_event(
            session, document, "markdown_skipped", {"reason": exc.reason, "detail": str(exc)}
        )
        return
    except Exception as exc:
        logger.exception("markdown generation failed for document %s", document.id)
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session, document, "markdown_failed", {"error": str(exc), "prompt_version": PROMPT_VERSION}
        )
        return

    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    for page in result.pages:
        session.add(
            DocumentPage(
                document_id=document.id,
                page_number=page.page_number,
                markdown=page.markdown,
                char_count=len(page.markdown),
            )
        )
    await _record_event(
        session,
        document,
        "markdown_completed",
        {
            "model": result.model,
            "prompt_version": result.prompt_version,
            "pages": len(result.pages),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
        },
    )
    logger.info(
        "markdown completed for document %s: pages=%s cost=$%.4f", document.id, len(result.pages), result.cost_usd
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/markdown/test_apply.py tests/extraction -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format src/library/markdown/apply.py src/library/extraction/apply.py tests/markdown/test_apply.py
uv run ruff check src/library/markdown/apply.py src/library/extraction/apply.py tests/markdown/test_apply.py
git add src/library/markdown/apply.py src/library/extraction/apply.py tests/markdown/test_apply.py
git commit -m "feat(markdown): apply stage with budget guard + per-page persistence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Pipeline wiring (markdown stage + re-run task)

**Files:**
- Modify: `src/library/jobs.py` (`_NEXT_STATUS` ~line 29, imports ~line 21, add `run_markdown` after `run_extraction`, `_run_stage_hook` ~line 190, add a `markdown_document` task near `embed_document` ~line 305)
- Test: `tests/test_jobs.py` (extend the existing pipeline tests; match their fixture style)

**Interfaces:**
- Consumes: `apply_markdown` (Task 5), `DocumentStatus.MARKDOWN` (Task 2).
- Produces: lifecycle `received → ocr → extract → markdown → embed → indexed`; `run_markdown(session, document)`; Procrastinate task `markdown_document(document_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs.py  (add)
import library.jobs as jobs
from library.models import DocumentStatus


def test_next_status_includes_markdown() -> None:
    assert jobs._NEXT_STATUS[DocumentStatus.EXTRACT] == DocumentStatus.MARKDOWN
    assert jobs._NEXT_STATUS[DocumentStatus.MARKDOWN] == DocumentStatus.EMBED


@pytest.mark.anyio
async def test_pipeline_runs_markdown_stage(session_factory, make_document, monkeypatch) -> None:
    # Stub each stage hook to record the order it ran in.
    calls: list[str] = []
    async def _md(session, document): calls.append("markdown")
    monkeypatch.setattr(jobs, "run_markdown", _md)
    # ... stub run_ocr/run_extraction/run_embed similarly to no-ops that append names ...
    document = await make_document(session_factory, status=DocumentStatus.EXTRACT)
    await jobs.advance_pipeline(session_factory, document.id)
    assert calls.index("markdown") < calls.index("embed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_jobs.py -k markdown -v`
Expected: FAIL (KeyError / `run_markdown` missing).

- [ ] **Step 3: Implement the wiring**

```python
# src/library/jobs.py — imports
from library.markdown.apply import apply_markdown
from library.models import Document, DocumentChunk, DocumentPage, DocumentStatus, IngestionEvent
```

```python
# src/library/jobs.py — _NEXT_STATUS
_NEXT_STATUS: dict[DocumentStatus, DocumentStatus] = {
    DocumentStatus.RECEIVED: DocumentStatus.OCR,
    DocumentStatus.OCR: DocumentStatus.EXTRACT,
    DocumentStatus.EXTRACT: DocumentStatus.MARKDOWN,
    DocumentStatus.MARKDOWN: DocumentStatus.EMBED,
    DocumentStatus.EMBED: DocumentStatus.INDEXED,
}
```

```python
# src/library/jobs.py — after run_extraction
async def run_markdown(session: AsyncSession, document: Document) -> None:
    """Markdown stage: Claude vision per-page markdown (best-effort, never raises)."""
    await apply_markdown(session, document, get_settings())
```

```python
# src/library/jobs.py — _run_stage_hook, add a branch
    elif status is DocumentStatus.MARKDOWN:
        await run_markdown(session, document)
```

```python
# src/library/jobs.py — near embed_document
@job_app.task(name="library.jobs.markdown_document")
async def markdown_document(document_id: int) -> None:
    """Background task: (re-)generate markdown for one document, then re-embed.

    Deferred by the backfill CLI (and after a prompt upgrade), independent of
    pipeline status. Best-effort and idempotent (replaces a document's pages
    and, via run_embed, its chunks).
    """
    async with get_sessionmaker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        await apply_markdown(session, document, get_settings())
        await run_embed(session, document)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/jobs.py tests/test_jobs.py
uv run ruff check src/library/jobs.py tests/test_jobs.py
git add src/library/jobs.py tests/test_jobs.py
git commit -m "feat(markdown): insert markdown pipeline stage + re-run task

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Page-aware embedding

**Files:**
- Modify: `src/library/jobs.py` (`run_embed` ~line 127)
- Test: `tests/test_jobs.py` (extend)

**Interfaces:**
- Consumes: `DocumentPage` (Task 2), `chunk_text` (existing).
- Produces: `run_embed` builds `(text, page_number)` chunk records from `document_pages` when present (page-aware), else from `ocr_text` (`page_number=None`); each `DocumentChunk` is written with its `page_number`, `chunk_index` continuous across pages.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs.py (add)
@pytest.mark.anyio
async def test_embed_tags_chunks_with_page_number(session, make_document, monkeypatch) -> None:
    document = await make_document(session, ocr_text="fallback text")
    session.add_all([
        DocumentPage(document_id=document.id, page_number=1, markdown="alpha " * 200, char_count=1200),
        DocumentPage(document_id=document.id, page_number=2, markdown="beta " * 200, char_count=1000),
    ])
    await session.commit()

    async def fake_embed(texts, *, settings):
        return [[0.0] * 1024 for _ in texts]
    monkeypatch.setattr(jobs, "embed_texts", fake_embed)

    await jobs.run_embed(session, document)
    chunks = (await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index)
    )).scalars().all()
    assert chunks and {c.page_number for c in chunks} <= {1, 2}
    assert [c.chunk_index for c in chunks] == list(range(1, len(chunks) + 1))


@pytest.mark.anyio
async def test_embed_falls_back_to_ocr_text_when_no_pages(session, make_document, monkeypatch) -> None:
    document = await make_document(session, ocr_text="word " * 500)
    async def fake_embed(texts, *, settings):
        return [[0.0] * 1024 for _ in texts]
    monkeypatch.setattr(jobs, "embed_texts", fake_embed)
    await jobs.run_embed(session, document)
    chunks = (await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    )).scalars().all()
    assert chunks and all(c.page_number is None for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_jobs.py -k embed -v`
Expected: FAIL (chunks have no `page_number` populated / column unset).

- [ ] **Step 3: Implement page-aware embed**

First ensure `select` is imported in `jobs.py` — change `from sqlalchemy import delete` to `from sqlalchemy import delete, select`.

Replace the chunk-building portion of `run_embed` (keep the disabled/embed-error handling):

```python
# src/library/jobs.py — inside run_embed, replacing the `chunks = chunk_text(...)` block
    pages = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        )
    ).scalars().all()

    chunk_records: list[tuple[str, int | None]] = []
    if pages:
        for page in pages:
            for piece in chunk_text(
                page.markdown,
                max_chars=settings.embedding_chunk_chars,
                overlap=settings.embedding_chunk_overlap,
            ):
                chunk_records.append((piece, page.page_number))
    else:
        for piece in chunk_text(
            document.ocr_text or "",
            max_chars=settings.embedding_chunk_chars,
            overlap=settings.embedding_chunk_overlap,
        ):
            chunk_records.append((piece, None))

    if not chunk_records:
        await _record_embed_event(session, document, "embedding_skipped", {"reason": "no_text"})
        return

    texts = [text for text, _ in chunk_records]
    try:
        vectors = await embed_texts(texts, settings=settings)
    except EmbeddingError as exc:
        await _record_embed_event(
            session, document, "embedding_failed", {"error": str(exc), "chunks": len(texts)}
        )
        return

    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    for index, ((text, page_number), vector) in enumerate(
        zip(chunk_records, vectors, strict=True), start=1
    ):
        session.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                page_number=page_number,
                text=text,
                embedding=vector,
            )
        )
    await _record_embed_event(
        session,
        document,
        "embedded",
        {"chunks": len(texts), "model": settings.embedding_model_name, "page_aware": bool(pages)},
    )
    logger.info("embedded document %s into %s chunks (page_aware=%s)", document.id, len(texts), bool(pages))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/jobs.py tests/test_jobs.py
uv run ruff check src/library/jobs.py tests/test_jobs.py
git add src/library/jobs.py tests/test_jobs.py
git commit -m "feat(markdown): page-aware embedding from document_pages

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `backfill-markdown` CLI

**Files:**
- Modify: `src/library/cli.py` (imports near top; new command after `backfill-validation` ~line 285)
- Test: `tests/test_cli.py` (extend; mirror the `backfill-embeddings` CLI test)

**Interfaces:**
- Consumes: `markdown_document` task (Task 6), `DocumentPage` (Task 2).
- Produces: `library backfill-markdown [--limit N] [--include-existing]` — enqueues `markdown_document` for non-deleted documents lacking `document_pages` (or all, with `--include-existing`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py (add) — mirror the backfill-embeddings test; assert markdown_document
# is deferred once per eligible document. Patch `markdown_document.defer_async` and
# `job_app.open_async` exactly as the existing embeddings test patches its equivalents.
def test_backfill_markdown_enqueues_documents_without_pages(...):
    ...
    assert deferred_ids == [doc_without_pages.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k backfill_markdown -v`
Expected: FAIL (no such command).

- [ ] **Step 3: Implement the command**

```python
# src/library/cli.py — imports
from library.jobs import embed_document, job_app, markdown_document
from library.models import Document, DocumentChunk, DocumentPage, Kind
```

```python
# src/library/cli.py — after backfill_validation
@app.command("backfill-markdown")
def backfill_markdown(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Only enqueue the first N documents."
    ),
    include_existing: bool = typer.Option(
        False, "--include-existing", help="Re-render documents that already have pages."
    ),
) -> None:
    """Queue markdown generation (and re-embed) for documents without pages.

    Backfills the markdown layer for documents ingested before the markdown
    stage existed. Idempotent — ``markdown_document`` replaces a document's
    pages and chunks — so re-running is safe. The worker must be running to do
    the work; this command only enqueues the jobs.
    """

    async def operation(session: AsyncSession) -> int:
        statement = select(Document.id).where(Document.deleted_at.is_(None))
        if not include_existing:
            statement = statement.where(~exists().where(DocumentPage.document_id == Document.id))
        statement = statement.order_by(Document.id)
        if limit is not None:
            statement = statement.limit(limit)
        document_ids = list((await session.execute(statement)).scalars().all())
        async with job_app.open_async():
            for document_id in document_ids:
                await markdown_document.defer_async(document_id=document_id)
        return len(document_ids)

    count = _run(operation)
    typer.echo(f"queued markdown generation for {count} document(s)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k backfill_markdown -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/cli.py tests/test_cli.py
uv run ruff check src/library/cli.py tests/test_cli.py
git add src/library/cli.py tests/test_cli.py
git commit -m "feat(markdown): backfill-markdown CLI command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `GET /api/documents/{id}/markdown` endpoint

**Files:**
- Modify: `src/library/api/documents.py` (add the route + response models near the other read routes)
- Test: `tests/api/test_documents_markdown.py`

**Interfaces:**
- Consumes: `DocumentPage` (Task 2).
- Produces: `GET /api/documents/{id}/markdown` → `{"page_count": int, "pages": [{"page_number": int, "markdown": str}]}`; empty `pages` (200) when the document has none; 404 when the document doesn't exist (match the existing detail route's not-found behavior).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_documents_markdown.py — use the repo's authenticated async test client fixture.
import pytest
from sqlalchemy import select  # noqa: F401  (if needed by helpers)


@pytest.mark.anyio
async def test_markdown_endpoint_returns_pages(client, make_document, session) -> None:
    document = await make_document(session, mime_type="application/pdf")
    from library.models import DocumentPage
    session.add_all([
        DocumentPage(document_id=document.id, page_number=1, markdown="# One", char_count=5),
        DocumentPage(document_id=document.id, page_number=2, markdown="# Two", char_count=5),
    ])
    await session.commit()

    response = await client.get(f"/api/documents/{document.id}/markdown")
    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == 2
    assert body["pages"][0] == {"page_number": 1, "markdown": "# One"}


@pytest.mark.anyio
async def test_markdown_endpoint_empty_when_none(client, make_document, session) -> None:
    document = await make_document(session)
    response = await client.get(f"/api/documents/{document.id}/markdown")
    assert response.status_code == 200
    assert response.json() == {"page_count": 0, "pages": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_documents_markdown.py -v`
Expected: FAIL (404 / route missing).

- [ ] **Step 3: Implement the endpoint**

```python
# src/library/api/documents.py — response models (near the top with the other schemas)
class MarkdownPage(BaseModel):
    page_number: int
    markdown: str


class MarkdownResponse(BaseModel):
    page_count: int
    pages: list[MarkdownPage]
```

```python
# src/library/api/documents.py — new route (place beside the detail GET route)
@router.get(
    "/documents/{document_id}/markdown",
    response_model=MarkdownResponse,
    summary="The per-page markdown rendering of a document",
)
async def get_document_markdown(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MarkdownResponse:
    """Assembled per-page markdown (ordered); empty when the document has none."""
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    rows = (
        await session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        )
    ).scalars().all()
    pages = [MarkdownPage(page_number=row.page_number, markdown=row.markdown) for row in rows]
    return MarkdownResponse(page_count=len(pages), pages=pages)
```

Ensure `DocumentPage` and `select` are imported in `documents.py` (add to existing imports if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_documents_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format src/library/api/documents.py tests/api/test_documents_markdown.py
uv run ruff check src/library/api/documents.py tests/api/test_documents_markdown.py
git add src/library/api/documents.py tests/api/test_documents_markdown.py
git commit -m "feat(markdown): GET /documents/{id}/markdown endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Page number through retrieval → Ask → API

**Files:**
- Modify: `src/library/search.py` (`_vector_candidates` ~line 176, `SemanticHit` ~line 161, `semantic_search` ~line 287)
- Modify: `src/library/ask/engine.py` (`_run_semantic_search` ~line 153, `AskCitation` ~line 123, `_citations_for` ~line 217, `run_ask` ~line 232)
- Modify: `src/library/api/ask.py` (`Citation` ~line 30, the `AskResponse` construction ~line 80)
- Test: `tests/test_search.py` (extend), `tests/ask/test_engine.py` (extend), `tests/api/test_ask.py` (extend) — match existing test module names.

**Interfaces:**
- Produces: `SemanticHit.page_number: int | None`; `AskCitation.page_number: int | None`; `/api/ask` `Citation.page_number: int | None`. The engine maps `document_id → page_number` from the top semantic hit per document; aggregation citations get `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search.py (add)
@pytest.mark.anyio
async def test_semantic_hit_carries_page_number(session, make_document) -> None:
    document = await make_document(session, ocr_text="solar invoice")
    session.add(DocumentChunk(
        document_id=document.id, chunk_index=1, page_number=3, text="solar invoice", embedding=[0.1] * 1024
    ))
    await session.commit()
    hits = await semantic_search(session, query="solar", query_embedding=[0.1] * 1024, top_k=5)
    assert hits and hits[0].page_number == 3
```

```python
# tests/api/test_ask.py (add) — assert the serialized citation includes page_number
def test_ask_citation_schema_has_page_number() -> None:
    from library.api.ask import Citation
    assert "page_number" in Citation.model_fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search.py -k page_number tests/api/test_ask.py -k page_number -v`
Expected: FAIL.

- [ ] **Step 3: Thread page_number through search**

```python
# src/library/search.py — SemanticHit
@dataclass(frozen=True, slots=True)
class SemanticHit:
    document: Document
    score: float
    chunk_index: int | None
    chunk_text: str | None
    page_number: int | None
```

```python
# src/library/search.py — _vector_candidates: add page_number to both selects and the map.
# Return type becomes tuple[list[int], dict[int, tuple[int, str, int | None]]].
    nearest_per_document = (
        select(
            DocumentChunk.document_id.label("document_id"),
            DocumentChunk.chunk_index.label("chunk_index"),
            DocumentChunk.page_number.label("page_number"),
            DocumentChunk.text.label("text"),
            distance,
        )
        ...
    )
    statement = (
        select(
            nearest_per_document.c.document_id,
            nearest_per_document.c.chunk_index,
            nearest_per_document.c.page_number,
            nearest_per_document.c.text,
        )
        ...
    )
    best_chunk: dict[int, tuple[int, str, int | None]] = {}
    for document_id, chunk_index, page_number, text in (await session.execute(statement)).all():
        best_chunk[document_id] = (chunk_index, text, page_number)
        order.append(document_id)
```

```python
# src/library/search.py — semantic_search: build the hit with page_number
        chunk = best_chunk.get(document_id)
        hits.append(
            SemanticHit(
                document=document,
                score=scores[document_id],
                chunk_index=chunk[0] if chunk else None,
                chunk_text=chunk[1] if chunk else None,
                page_number=chunk[2] if chunk else None,
            )
        )
```

- [ ] **Step 4: Thread page_number through the engine**

```python
# src/library/ask/engine.py — AskCitation
@dataclass(frozen=True, slots=True)
class AskCitation:
    document_id: int
    title: str | None
    page_number: int | None = None
```

```python
# src/library/ask/engine.py — _run_semantic_search: record the page per document.
# Change the signature to also accept `pages: dict[int, int]` and fill it:
async def _run_semantic_search(session, settings, args, cited, pages):
    ...
    for hit in hits:
        cited.add(hit.document.id)
        if hit.page_number is not None and hit.document.id not in pages:
            pages[hit.document.id] = hit.page_number
        rows.append({... "page_number": hit.page_number, ...})
```

```python
# src/library/ask/engine.py — _dispatch_tool and run_ask: thread the `pages` dict through
# (create `pages: dict[int, int] = {}` in run_ask next to `cited`), then:
async def _citations_for(session, cited, pages):
    ...
    return [AskCitation(document_id=did, title=title, page_number=pages.get(did)) for did, title in rows]
```

Update `_dispatch_tool` to pass `pages` to `_run_semantic_search` (and ignore it for `query_documents`), and `run_ask` to pass `pages` into `_citations_for`.

- [ ] **Step 5: Thread page_number through the API**

```python
# src/library/api/ask.py — Citation
class Citation(BaseModel):
    document_id: int
    title: str | None
    page_number: int | None = None
```

```python
# src/library/api/ask.py — where AskResponse citations are built
        citations=[
            Citation(document_id=c.document_id, title=c.title, page_number=c.page_number)
            for c in result.citations
        ],
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_search.py tests/ask tests/api/test_ask.py -v`
Expected: PASS.

- [ ] **Step 7: Format, lint, commit**

```bash
uv run ruff format src/library/search.py src/library/ask/engine.py src/library/api/ask.py tests/test_search.py tests/ask tests/api/test_ask.py
uv run ruff check src/library/search.py src/library/ask/engine.py src/library/api/ask.py tests/test_search.py tests/ask tests/api/test_ask.py
git add src/library/search.py src/library/ask/engine.py src/library/api/ask.py tests/test_search.py tests/ask tests/api/test_ask.py
git commit -m "feat(markdown): page-numbered Ask citations end-to-end

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Frontend — citation page label + PDF deep-link

**Files:**
- Modify: `frontend/src/api/ask.ts` (`AskCitation` interface)
- Modify: `frontend/src/views/AskView.vue` (citation `RouterLink` ~line 137)
- Modify: `frontend/src/views/DocumentDetailView.vue` (read `route.query.page` ~line 594, append to `pdfPreviewIframeUrl` ~line 564)
- Test: `frontend/src/views/__tests__/AskView.spec.ts`, `frontend/src/views/__tests__/DocumentDetailView.spec.ts` (extend)

**Interfaces:**
- Consumes: `/api/ask` `Citation.page_number` (Task 10).
- Produces: citation renders `Title, p.N` (no `, p.N` when `page_number` is null) and links with `query: { page: N }`; the detail view's PDF iframe URL includes `#page=N` when `route.query.page` is present.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/views/__tests__/AskView.spec.ts (add)
it('renders the page number on a citation and links with a page query', async () => {
  // mount AskView with a stubbed askQuestion returning:
  // citations: [{ document_id: 42, title: 'Energy bill', page_number: 3 }]
  // submit a question, then:
  const link = wrapper.get('[data-testid="ask-citation"]')
  expect(link.text()).toContain('p. 3')
  expect(link.attributes('href')).toContain('page=3')
})

it('omits the page label when page_number is null', async () => {
  // citations: [{ document_id: 7, title: 'Note', page_number: null }]
  expect(wrapper.get('[data-testid="ask-citation"]').text()).not.toContain('p.')
})
```

```ts
// frontend/src/views/__tests__/DocumentDetailView.spec.ts (add)
it('deep-links the PDF iframe to the page from the route query', async () => {
  // mount with route.query.page = '2' and a pdf document; then:
  const iframe = wrapper.get('iframe')
  expect(iframe.attributes('src')).toContain('#page=2')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/views/__tests__/AskView.spec.ts src/views/__tests__/DocumentDetailView.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Update the API type**

```ts
// frontend/src/api/ask.ts
export interface AskCitation {
  document_id: number
  title: string | null
  page_number: number | null
}
```

- [ ] **Step 4: Update AskView citation rendering**

```vue
<!-- frontend/src/views/AskView.vue — the RouterLink for each citation -->
<RouterLink
  :to="{ name: 'document-detail', params: { id: citation.document_id }, query: citation.page_number ? { page: citation.page_number } : {} }"
  data-testid="ask-citation"
>
  {{ citation.title ?? 'Untitled' }}<span v-if="citation.page_number">, p. {{ citation.page_number }}</span>
  <span class="...existing id badge classes...">#{{ citation.document_id }}</span>
</RouterLink>
```

(Keep the existing id-badge markup/classes; only add the `query` binding and the `, p. N` span.)

- [ ] **Step 5: Deep-link the detail-view PDF**

```ts
// frontend/src/views/DocumentDetailView.vue — near the other route reads
const pageParam = computed<number | null>(() => {
  const value = route.query.page
  const n = Array.isArray(value) ? Number(value[0]) : Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
})
```

```ts
// frontend/src/views/DocumentDetailView.vue — pdfPreviewIframeUrl: append #page=N
const pdfPreviewIframeUrl = computed(() => {
  if (!pdfPreviewUrl.value) return ''
  const page = pageParam.value ? `&page=${pageParam.value}` : ''
  return `${pdfPreviewUrl.value}#toolbar=0&navpanes=0&view=FitH${page}`
})
```

(Browser PDF viewers accept `page` as one of the `#`-fragment parameters alongside the existing ones.)

- [ ] **Step 6: Run tests**

Run: `cd frontend && npx vitest run src/views/__tests__/AskView.spec.ts src/views/__tests__/DocumentDetailView.spec.ts`
Expected: PASS.

- [ ] **Step 7: Lint, commit**

```bash
cd frontend && npm run lint && npm run type-check && cd ..
git add frontend/src/api/ask.ts frontend/src/views/AskView.vue frontend/src/views/DocumentDetailView.vue frontend/src/views/__tests__/AskView.spec.ts frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(markdown): page-numbered Ask citations + PDF deep-link (frontend)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Frontend — markdown tab in the detail view

**Files:**
- Modify: `frontend/src/api/documents.ts` (add `fetchDocumentMarkdown`)
- Modify: `frontend/src/views/DocumentDetailView.vue` (a "Markdown" preview tab/section rendering the assembled markdown)
- Test: `frontend/src/api/__tests__/documents.spec.ts`, `frontend/src/views/__tests__/DocumentDetailView.spec.ts` (extend)

**Interfaces:**
- Consumes: `GET /api/documents/{id}/markdown` (Task 9).
- Produces: `fetchDocumentMarkdown(id: number): Promise<{ page_count: number; pages: { page_number: number; markdown: string }[] }>`; a detail-view section that fetches and renders the markdown (reuse the existing sanitized-markdown rendering helper used by `AskView.vue` — do not introduce a new markdown library).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/__tests__/documents.spec.ts (add)
it('fetchDocumentMarkdown calls the markdown endpoint', async () => {
  // stub apiFetch to assert the URL '/api/documents/42/markdown' and return a page list
  const result = await fetchDocumentMarkdown(42)
  expect(result.page_count).toBe(1)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/__tests__/documents.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement the client**

```ts
// frontend/src/api/documents.ts
export interface DocumentMarkdownPage {
  page_number: number
  markdown: string
}
export interface DocumentMarkdownResponse {
  page_count: number
  pages: DocumentMarkdownPage[]
}
export function fetchDocumentMarkdown(id: number): Promise<DocumentMarkdownResponse> {
  return apiFetch<DocumentMarkdownResponse>(`/api/documents/${id}/markdown`)
}
```

- [ ] **Step 4: Add the markdown section to the detail view**

Add a collapsible "Markdown" section (or a tab beside the existing preview) that, on first reveal, calls `fetchDocumentMarkdown(doc.id)` and renders each page's markdown via the same sanitized-render path `AskView.vue` uses. Show a friendly empty state when `page_count === 0`. Keep it behind lazy fetch so the detail load is unchanged.

- [ ] **Step 5: Run tests**

Run: `cd frontend && npx vitest run src/api/__tests__/documents.spec.ts src/views/__tests__/DocumentDetailView.spec.ts`
Expected: PASS.

- [ ] **Step 6: Lint, type-check, commit**

```bash
cd frontend && npm run lint && npm run type-check && cd ..
git add frontend/src/api/documents.ts frontend/src/views/DocumentDetailView.vue frontend/src/api/__tests__/documents.spec.ts frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(markdown): document markdown tab in the detail view

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Playwright e2e — ask → page citation → PDF deep-link

**Files:**
- Create/Modify: `frontend/e2e/ask-page-citation.spec.ts` (or extend the existing ask e2e spec — match the repo's e2e layout)
- Possibly modify: the e2e fixture/seed so a seeded document has `document_pages` + page-tagged chunks (extraction/markdown are mocked in e2e per the journal follow-up).

**Interfaces:**
- Consumes: the full stack from Tasks 9–12.
- Produces: an e2e proving open `/ask` → ask a seeded question → a citation shows `p. N` → clicking it lands on the detail view with `?page=N` and the PDF iframe `src` contains `#page=N`.

- [ ] **Step 1: Write the e2e**

```ts
// frontend/e2e/ask-page-citation.spec.ts
import { test, expect } from '@playwright/test'

test('ask citation deep-links to the cited PDF page', async ({ page }) => {
  // Seed: a document with a searchable PDF and a page-tagged chunk so the
  // mocked ask returns a citation with page_number = 2. Follow the existing
  // e2e seeding/mocking approach (see the current ask e2e + readiness gate).
  await page.goto('/ask')
  await page.getByRole('textbox').fill('what is the total?')
  await page.getByRole('button', { name: /ask/i }).click()
  const citation = page.getByTestId('ask-citation').first()
  await expect(citation).toContainText('p. 2')
  await citation.click()
  await expect(page).toHaveURL(/\/documents\/\d+\?page=2/)
  await expect(page.locator('iframe')).toHaveAttribute('src', /#.*page=2/)
})
```

- [ ] **Step 2: Run it to verify it fails (then passes after wiring the seed)**

Run: `cd frontend && npx playwright test ask-page-citation`
Expected: FAIL first (no seed/mock), then PASS once the seed + mock are in place.

- [ ] **Step 3: Implement the seed/mock and make it pass**

Wire the e2e mock for `POST /api/ask` to return a citation with `page_number: 2` (or seed real page-tagged chunks if the e2e stack embeds), and ensure the seeded document has a viewable PDF.

- [ ] **Step 4: Run the full e2e suite**

Run: `cd frontend && npx playwright test`
Expected: PASS (no regressions in existing specs).

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e/ask-page-citation.spec.ts frontend/e2e/  # + any fixture/seed files touched
git commit -m "test(e2e): ask page citation deep-links to the PDF page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Documentation, env reference, changelog, journal

**Files:**
- Modify: `docs/architecture.md` (§1.2 pipeline lifecycle + step list; §1.3 data model), `docs/ingestion.md` (new "Markdown layer" section + the events table + config table), `docs/ask.md` (citations now carry a page number; remove/adjust limitation §1.6.1), `.env.example` (the six `LIBRARY_MARKDOWN_*` vars)
- Modify: `CHANGELOG.md`
- Create: `journal/260621-markdown-layer.md`

**Interfaces:** none (docs only). Documentation must be accurate and complete — no stubs.

- [ ] **Step 1: Update architecture.md**

Update the lifecycle everywhere it appears to `received → ocr → extract → markdown → embed → indexed`; add a pipeline step describing the markdown stage; add `document_pages` and `document_chunks.page_number` to the §1.3 data-model summary; add a row to the §1.6 status table.

- [ ] **Step 2: Update ingestion.md**

Add a "Markdown layer (`library.markdown`)" section: generation (vision + OCR grounding, per-page, batching, page cap), `document_pages` storage, the new `markdown` stage and its best-effort contract, the `markdown_completed`/`markdown_skipped`/`markdown_failed` events (add to the events table), `backfill-markdown`, and the six config settings (add to the config table). Note that embed is now page-aware and falls back to `ocr_text`.

- [ ] **Step 3: Update ask.md**

Note citations now carry a page number when the best chunk came from a page-aware document, and the detail view deep-links the PDF; update limitation §1.6.1 accordingly (page numbers exist for documents with a markdown layer; older/text-only docs still cite without a page).

- [ ] **Step 4: Update .env.example**

```sh
# Markdown layer (vision rendering of each document; feeds embeddings + Ask)
LIBRARY_MARKDOWN_ENABLED=true
LIBRARY_MARKDOWN_MODEL=claude-haiku-4-5
LIBRARY_MARKDOWN_DAILY_BUDGET_USD=5.0
LIBRARY_MARKDOWN_MAX_PAGES=20
LIBRARY_MARKDOWN_PAGE_BATCH=10
LIBRARY_MARKDOWN_IMAGE_LONG_SIDE_PX=1600
```

- [ ] **Step 5: Update CHANGELOG.md and write the journal entry**

Add a CHANGELOG entry under the current version. Create `journal/260621-markdown-layer.md` capturing: the decisions (hybrid vision+OCR, fold-in of #3, `document_pages` table, new stage, separate budget, Haiku default), what shipped per task, schema changes (migration 0007), and follow-ups (e.g. markdown feeding extraction input; cross-encoder re-ranking; per-page OCR confidence; e2e using real markdown once a live key is available).

- [ ] **Step 6: Verify links and build docs references; commit**

```bash
git add docs/ .env.example CHANGELOG.md journal/260621-markdown-layer.md
git commit -m "docs(markdown): document the markdown layer + page citations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] `uv run ruff format --check . && uv run ruff check .` — clean.
- [ ] `uv run pytest` — full backend suite green (was 366; expect the new tests added).
- [ ] `cd frontend && npm run lint && npm run type-check && npx vitest run` — green (was 243).
- [ ] `cd frontend && npx playwright test` — green.
- [ ] `uv run alembic upgrade head` against a scratch DB, then `uv run alembic downgrade 0006` and back up — clean.
- [ ] Whole-branch review on the most capable model (per the workflow), then finishing-a-development-branch.
