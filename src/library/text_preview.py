"""Turn a markdown document body into a short, plain-text preview excerpt.

Used to give email-ingested ``text/markdown`` tiles a real body preview on the
dashboard grid instead of a generic "Text" placeholder. Deliberately regex-based
and dependency-free: this is a lossy, display-only excerpt, not a faithful render.
"""

import re

# Fenced code blocks (```lang ... ```), removed wholesale before other passes.
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
# Images ![alt](url) -> dropped entirely (must run before the link rule).
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Links [text](url) -> keep the visible text only.
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Leading block markers at the start of a line: blockquotes, list bullets,
# ordered-list numbers, and ATX heading hashes.
_LINE_PREFIX = re.compile(r"^[ \t]*(?:[>#]+[ \t]*|[-*+][ \t]+|\d+\.[ \t]+)", re.MULTILINE)
# Emphasis / inline-code / strikethrough marker characters.
_INLINE_MARKERS = re.compile(r"[*_~`]")
_WHITESPACE = re.compile(r"\s+")


def markdown_excerpt(text: str | None, max_chars: int = 240) -> str | None:
    """A single-line plain-text excerpt of ``text``, or ``None`` if it is empty.

    Strips common markdown syntax (headings, emphasis, code, blockquotes, list
    bullets, links/images), collapses all whitespace to single spaces, and caps
    the result at ``max_chars`` characters (on a word boundary where easy),
    appending an ellipsis when truncated. Returns ``None`` for ``None``, blank,
    or whitespace-only input, or when nothing remains after stripping.
    """
    if text is None or not text.strip():
        return None

    stripped = _FENCED_CODE.sub(" ", text)
    stripped = _IMAGE.sub(" ", stripped)
    stripped = _LINK.sub(r"\1", stripped)
    stripped = _LINE_PREFIX.sub("", stripped)
    stripped = _INLINE_MARKERS.sub("", stripped)
    stripped = _WHITESPACE.sub(" ", stripped).strip()

    if not stripped:
        return None

    if len(stripped) <= max_chars:
        return stripped

    head = stripped[:max_chars]
    # Prefer a word boundary if one is reasonably close to the cut.
    space = head.rfind(" ")
    if space >= max_chars // 2:
        head = head[:space]
    return f"{head.rstrip()}…"
