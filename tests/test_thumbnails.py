"""Tests for first-page thumbnail rendering and the worker task."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from library.config import get_settings
from library.jobs import generate_thumbnail, run_generate_thumbnail
from library.models import Document, DocumentSource, IngestionEvent
from library.thumbnails import THUMBNAIL_NAME, THUMBNAIL_WIDTH, render_thumbnail
from tests.ocr_fixtures import make_text_pdf, render_text_image


def test_render_thumbnail_pdf_first_page(tmp_path: Path) -> None:
    pdf = make_text_pdf(tmp_path / "doc.pdf", lines=["Factuur", "Eneco"], pages=2)
    derived = tmp_path / "derived"
    derived.mkdir()

    target = render_thumbnail("application/pdf", pdf, derived)

    assert target == derived / THUMBNAIL_NAME
    image = Image.open(target)
    assert image.format == "WEBP"
    # A4 portrait rasterized to the target width (allow a pixel of rounding).
    assert abs(image.width - THUMBNAIL_WIDTH) <= 1
    assert image.height > image.width  # portrait page stays portrait


def test_render_thumbnail_image_downscales(tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    render_text_image("bonnetje", size=(2000, 1000)).save(source)
    derived = tmp_path / "derived"
    derived.mkdir()

    target = render_thumbnail("image/png", source, derived)

    assert target is not None
    image = Image.open(target)
    assert image.format == "WEBP"
    assert image.size == (THUMBNAIL_WIDTH, 240)


def test_render_thumbnail_small_image_is_not_upscaled(tmp_path: Path) -> None:
    source = tmp_path / "small.png"
    Image.new("RGB", (100, 50), "white").save(source)
    derived = tmp_path / "derived"
    derived.mkdir()

    target = render_thumbnail("image/png", source, derived)

    assert target is not None
    assert Image.open(target).size == (100, 50)


def test_render_thumbnail_heic_uses_derived_conversion(tmp_path: Path) -> None:
    derived = tmp_path / "derived"
    derived.mkdir()
    # Ingest writes converted.jpg for HEIC originals; the renderer reads that.
    Image.new("RGB", (800, 600), "white").save(derived / "converted.jpg")

    target = render_thumbnail("image/heic", tmp_path / "missing-original.heic", derived)

    assert target is not None
    assert Image.open(target).size == (THUMBNAIL_WIDTH, 360)


def test_render_thumbnail_unsupported_type_returns_none(tmp_path: Path) -> None:
    derived = tmp_path / "derived"
    derived.mkdir()

    assert render_thumbnail("text/plain", tmp_path / "note.txt", derived) is None
    assert not (derived / THUMBNAIL_NAME).exists()


# --- Worker task against the real test database -----------------------------


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def make_document(
    session_factory: async_sessionmaker[AsyncSession],
    marker: str,
    *,
    content: bytes,
    mime_type: str,
    data_dir: Path,
) -> int:
    """A document row whose original bytes really exist content-addressed."""
    sha = hashlib.sha256(content).hexdigest()
    path = data_dir / "originals" / sha[0:2] / sha[2:4] / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    async with session_factory() as session:
        document = Document(
            sha256=sha,
            mime_type=mime_type,
            source=DocumentSource.UPLOAD,
            original_filename=marker,
        )
        session.add(document)
        await session.commit()
        return document.id


async def get_events(
    session_factory: async_sessionmaker[AsyncSession], document_id: int
) -> list[tuple[str, dict[str, object]]]:
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(IngestionEvent)
                    .where(IngestionEvent.document_id == document_id)
                    .order_by(IngestionEvent.id)
                )
            )
            .scalars()
            .all()
        )
        return [(event.event, event.detail) for event in events]


@pytest.mark.integration
async def test_task_generates_real_webp_for_pdf(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    tmp_path: Path,
) -> None:
    pdf_bytes = make_text_pdf(tmp_path / "fixture.pdf", lines=["Garantiebewijs"]).read_bytes()
    document_id = await make_document(
        session_factory,
        "thumb-task-pdf",
        content=pdf_bytes,
        mime_type="application/pdf",
        data_dir=data_dir,
    )

    await run_generate_thumbnail(session_factory, document_id)

    sha = hashlib.sha256(pdf_bytes).hexdigest()
    thumb = data_dir / "derived" / sha[0:2] / sha[2:4] / sha / THUMBNAIL_NAME
    image = Image.open(thumb)
    assert image.format == "WEBP"
    assert abs(image.width - THUMBNAIL_WIDTH) <= 1

    events = await get_events(session_factory, document_id)
    assert ("thumbnail_generated", {"artifact": THUMBNAIL_NAME}) in events


@pytest.mark.integration
async def test_task_skips_plain_text(
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    document_id = await make_document(
        session_factory,
        "thumb-task-txt",
        content=b"alleen tekst, geen plaatje",
        mime_type="text/plain",
        data_dir=data_dir,
    )

    await run_generate_thumbnail(session_factory, document_id)

    events = await get_events(session_factory, document_id)
    skipped = [detail for event, detail in events if event == "thumbnail_skipped"]
    assert skipped == [{"reason": "unsupported_mime", "mime_type": "text/plain"}]


async def test_generate_thumbnail_task_registered() -> None:
    assert generate_thumbnail.name == "library.jobs.generate_thumbnail"


@pytest.mark.integration
async def test_task_missing_document_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="999999998"):
        await run_generate_thumbnail(session_factory, 999999998)
