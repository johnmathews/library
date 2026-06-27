"""Unit tests for the embedding text chunker (no DB, no network)."""

from library.embedding.chunker import chunk_markdown, chunk_text


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


# --- chunk_markdown: structure-preserving block packing ---


def test_chunk_markdown_blank_yields_no_chunks() -> None:
    assert chunk_markdown("", max_chars=100, overlap=10) == []
    assert chunk_markdown("   \n\n  \n\n", max_chars=100, overlap=10) == []


def test_chunk_markdown_preserves_intra_block_newlines() -> None:
    text = "# Heading\n\n- item one\n- item two\n- item three"
    chunks = chunk_markdown(text, max_chars=200, overlap=0)
    # Everything fits in one chunk; headings and list newlines survive.
    assert len(chunks) == 1
    assert "# Heading" in chunks[0]
    assert "- item one\n- item two\n- item three" in chunks[0]


def test_chunk_markdown_does_not_split_block_mid_line() -> None:
    # Two paragraphs, each whole; packed into separate chunks by max_chars.
    para_a = "alpha beta gamma delta"
    para_b = "epsilon zeta eta theta"
    chunks = chunk_markdown(f"{para_a}\n\n{para_b}", max_chars=25, overlap=0)
    # Each block stays intact (never split mid-line) and lands in its own chunk.
    assert para_a in chunks
    assert para_b in chunks


def test_chunk_markdown_oversized_block_falls_back_to_word_packer() -> None:
    big = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_markdown(big, max_chars=80, overlap=10)
    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "word0" in chunks[0]
    assert "word199" in " ".join(chunks)


def test_chunk_markdown_overlap_carries_a_whole_block() -> None:
    blocks = [f"block-{i} body" for i in range(6)]
    text = "\n\n".join(blocks)
    chunks = chunk_markdown(text, max_chars=30, overlap=15)
    assert len(chunks) >= 2
    # A whole block from the tail of chunk 0 reappears at the head of chunk 1.
    first_blocks = set(chunks[0].split("\n\n"))
    second_blocks = set(chunks[1].split("\n\n"))
    assert first_blocks & second_blocks
