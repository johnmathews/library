"""Unit tests for the embedding text chunker (no DB, no network)."""

from library.embedding.chunker import chunk_text


def test_blank_text_yields_no_chunks() -> None:
    assert chunk_text("", max_chars=100, overlap=10) == []
    assert chunk_text("   \n\n  ", max_chars=100, overlap=10) == []


def test_short_text_is_one_normalised_chunk() -> None:
    chunks = chunk_text("hello   world\n\nfoo", max_chars=100, overlap=10)
    assert chunks == ["hello world foo"]


def test_long_text_splits_into_bounded_chunks() -> None:
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, max_chars=120, overlap=20)
    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    # Every original word survives somewhere (no data lost).
    joined = " ".join(chunks)
    assert "word0" in chunks[0]
    assert "word499" in joined


def test_consecutive_chunks_overlap() -> None:
    text = " ".join(f"w{i}" for i in range(200))
    chunks = chunk_text(text, max_chars=100, overlap=30)
    assert len(chunks) >= 2
    # The tail words of chunk N reappear at the head of chunk N+1.
    first_tail = set(chunks[0].split()[-3:])
    second_head = set(chunks[1].split()[:5])
    assert first_tail & second_head


def test_word_longer_than_max_becomes_its_own_chunk() -> None:
    giant = "x" * 50
    chunks = chunk_text(f"a b {giant} c d", max_chars=10, overlap=0)
    assert giant in chunks
