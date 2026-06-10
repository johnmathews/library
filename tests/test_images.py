"""Unit tests for HEIC normalisation."""

import io

from PIL import Image

from library.images import normalize_image


def make_heic(size: tuple[int, int] = (8, 4), orientation: int | None = None) -> bytes:
    """A tiny HEIC image, optionally carrying an EXIF orientation tag."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    image = Image.new("RGB", size, "red")
    kwargs: dict[str, bytes] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif.tobytes()
    buffer = io.BytesIO()
    image.save(buffer, format="HEIF", **kwargs)
    return buffer.getvalue()


def test_non_heic_passthrough() -> None:
    content = b"%PDF-1.4 not an image"
    result = normalize_image(content, "application/pdf")
    assert result.content == content
    assert result.mime == "application/pdf"
    assert result.converted is False


def test_jpeg_passthrough() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="JPEG")
    content = buffer.getvalue()
    result = normalize_image(content, "image/jpeg")
    assert result.content == content
    assert result.converted is False


def test_heic_converted_to_jpeg() -> None:
    result = normalize_image(make_heic(), "image/heic")
    assert result.converted is True
    assert result.mime == "image/jpeg"
    converted = Image.open(io.BytesIO(result.content))
    assert converted.format == "JPEG"
    assert converted.size == (8, 4)


def test_heic_orientation_applied() -> None:
    """Orientation 6 (rotate 90 CW) on an 8x4 HEIC yields a 4x8 upright JPEG."""
    result = normalize_image(make_heic(size=(8, 4), orientation=6), "image/heic")
    assert result.converted is True
    converted = Image.open(io.BytesIO(result.content))
    assert converted.size == (4, 8)
    # The orientation has been baked into the pixels, not left as a tag.
    assert converted.getexif().get(0x0112, 1) == 1


def test_heif_mime_also_converted() -> None:
    result = normalize_image(make_heic(), "image/heif")
    assert result.converted is True
    assert result.mime == "image/jpeg"
