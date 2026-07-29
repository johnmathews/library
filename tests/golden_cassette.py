"""Deterministic replay of recorded extraction calls.

``extractor._attempt`` is the seam: one ``messages.parse`` call in, one
``(ExtractedMetadata, CallUsage)`` out. Replacing it with a lookup makes the LLM
deterministic while leaving everything around it real — ``build_user_content``,
``_thin_scan_prefers_vision``, and both escalation switches all still run, which
is the reason to intercept here rather than mocking ``extract`` outright.

The cassette key is ``(model, sha256(content))``. Hashing the content matters
twice over: it distinguishes the two calls of an escalated extraction (different
model, different content), and it means a change to the prompt or to
``build_user_content`` **misses** the cassette and raises, rather than silently
replaying a response to a question that is no longer being asked. That is the
whole point of a characterisation test — a stale cassette that keeps passing
records the regression instead of catching it.

Cassettes hold the model's structured output and token counts — no API key, but
that does NOT make them publishable: titles, summaries, senders and amounts are
document content, and some of it is medical. They live in the private corpus
repo. See ``tests/golden_corpus.py`` for the rule and why the first version of
this comment was wrong.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from library.extraction.extractor import CallUsage
from library.extraction.schema import ExtractedMetadata
from library.models import Document
from tests.golden_corpus import cassette_path


def content_key(model: str, content: list[dict[str, Any]]) -> str:
    """A stable key for one ``_attempt`` call.

    ``sort_keys`` + ``default=str`` so the digest does not depend on dict
    ordering or on a value pydantic renders differently between versions. Image
    blocks include their base64 payload, so a different rendering of the same
    page produces a different key — deliberately: the recorded answer would no
    longer correspond to the input.
    """
    payload = json.dumps(content, sort_keys=True, default=str).encode()
    return f"{model}:{hashlib.sha256(payload).hexdigest()}"


def load_cassettes() -> dict[str, dict[str, Any]]:
    path = cassette_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def replay(cassettes: dict[str, dict[str, Any]]) -> Any:
    """Build a stand-in for ``extractor._attempt`` backed by ``cassettes``."""

    async def _replayed(
        client: Any, model: str, content: list[dict[str, Any]]
    ) -> tuple[ExtractedMetadata, CallUsage]:
        key = content_key(model, content)
        entry = cassettes.get(key)
        if entry is None:
            raise AssertionError(
                f"no recorded extraction for {key}. The prompt, the model, or "
                "build_user_content has changed since the cassettes were "
                "recorded — re-record with "
                "`python scripts/record_golden_extractions.py` and review the "
                "resulting snapshot diff. Do not paper over this: a cassette "
                "that still matches a changed prompt is answering the wrong "
                "question."
            )
        metadata = ExtractedMetadata.model_validate(entry["metadata"])
        usage = CallUsage(
            model=entry["model"],
            input_tokens=entry["input_tokens"],
            output_tokens=entry["output_tokens"],
            cost_usd=entry["cost_usd"],
        )
        return metadata, usage

    return _replayed


def apply_metadata_for_validation(
    document: Document, metadata: ExtractedMetadata, input_mode: str
) -> None:
    """Put an extraction result onto a transient Document so ``validate`` sees it.

    Deliberately **not** ``_apply_outcome``: that upserts senders, kinds and tags
    and needs a session, and both callers here are DB-free by design.

    Shared by the recorder and the replay test because they must apply the
    outcome *identically* — a snapshot recorded through one code path and checked
    through another characterises the difference between them, not the pipeline.
    They already drifted once: both referred to ``metadata.sender``, which does
    not exist (it is ``sender_name``), and the test never caught it because there
    were no cassettes to run it against.

    ``sender_id`` is a stand-in: the rules only test whether a sender resolved,
    and resolving one for real would need the database this tier avoids.
    """
    document.amount_total = Decimal(metadata.amount_total) if metadata.amount_total else None
    document.currency = metadata.currency
    document.document_date = metadata.document_date
    document.due_date = metadata.due_date
    document.expiry_date = metadata.expiry_date
    document.title = metadata.title
    document.summary = metadata.summary
    document.sender_id = 1 if metadata.sender_name else None
    document.extra = {"extraction": {"input_mode": input_mode}}
