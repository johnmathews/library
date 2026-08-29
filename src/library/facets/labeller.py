"""Assign facet values to one document, choosing only from the closed vocabulary.

The model never widens the vocabulary. ``parse_label_response`` maps anything it
returns that is not an allowed value (or an alias of one) onto ``unknown`` plus a
*suggestion*, which a later task queues for approval. That mapping is pure, so
the guarantee is tested without a model.

Uses ``settings.extraction_model`` rather than a setting of its own: every
``*_model`` setting needs a matching row in ``MODEL_PRICING_USD_PER_MTOK`` or the
app refuses to boot.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from library.config import LLMBackend, Settings
from library.facets.vocabulary import VocabularyFacet
from library.llm import subscription
from library.llm.envelope import strip_json_envelope

logger = logging.getLogger(__name__)

MAX_LABEL_TOKENS: int = 600
MAX_EXCERPT_CHARS: int = 2000
# ``facet_value_suggestions.suggested_label`` is VARCHAR(255). Anything longer
# reaches the insert as a statement-level Postgres error, which aborts whatever
# transaction the labeller was invited into (ingest, or a backfill run), so the
# clamp belongs here — at the boundary where the model's text becomes our data.
MAX_SUGGESTED_LABEL_CHARS: int = 255

LABELLER_SYSTEM_PROMPT: str = """\
You assign labels to a household document for "Library", a self-hosted family
document archive.

You are given a CLOSED vocabulary of facets. Each facet is one dimension, and a
document takes AT MOST ONE value per facet. You may only choose values that
appear in the vocabulary; aliases listed beside a value also identify it.

If no listed value fits a facet, return "value": null for that facet and put the
label you WOULD have wanted in "suggest". Never invent a value in the "value"
field. Omit a facet entirely when it does not apply to this document.

"confidence" is your confidence in that single value, from 0 to 1. Be honest:
a low confidence sends the document to a human, which is the correct outcome
when you are unsure.

Return ONLY a JSON object of this shape, with no prose or code fences:
{"labels": [{"facet": "...", "value": "..."|null, "confidence": 0.0,
             "reason": "one short clause", "suggest": "..."|null}]}"""


@dataclass(frozen=True, slots=True)
class DocumentFields:
    """The document facts the labeller is allowed to see."""

    title: str | None
    summary: str | None
    sender: str | None
    kind: str | None
    amount: str | None
    currency: str | None
    excerpt: str | None


@dataclass(frozen=True, slots=True)
class LabelProposal:
    """One facet's proposed value. ``value_key is None`` means unknown."""

    facet_key: str
    value_key: str | None
    confidence: float
    reason: str
    suggested_label: str | None


class LabelEntry(BaseModel):
    """One facet's raw proposal, exactly as the model may return it.

    Deliberately permissive — this is the ``output_format`` for
    ``client.messages.parse()``, not the closed-vocabulary gate. A model
    naming a facet or value outside the vocabulary must still reach
    :func:`parse_label_response` so it becomes a *suggestion* rather than a
    rejected response, so nothing here constrains ``facet``/``value`` to a
    known set.
    """

    facet: str
    value: str | None = None
    confidence: float = 0.0
    reason: str = ""
    suggest: str | None = None


class LabelResponse(BaseModel):
    """Structured-output schema for the facet labeller (see :class:`LabelEntry`)."""

    labels: list[LabelEntry] = Field(default_factory=list)


class LabelParseError(Exception):
    """The facet labeller's structured-output call returned nothing parseable."""


def build_labelling_prompt(vocabulary: Sequence[VocabularyFacet], fields: DocumentFields) -> str:
    lines: list[str] = ["VOCABULARY (choose only from these):"]
    for facet in vocabulary:
        lines.append(f"- {facet.key} ({facet.label}):")
        if not facet.values:
            lines.append("    (no values yet — return null and suggest one if it applies)")
        for value in facet.values:
            alias_note = f"  [also: {', '.join(value.aliases)}]" if value.aliases else ""
            lines.append(f"    {value.key} — {value.label}{alias_note}")
    excerpt = (fields.excerpt or "")[:MAX_EXCERPT_CHARS]
    lines += [
        "",
        "DOCUMENT:",
        f"Sender: {fields.sender}",
        f"Kind: {fields.kind}",
        f"Title: {fields.title}",
        f"Summary: {fields.summary}",
        f"Amount: {fields.amount} {fields.currency}",
        f"Text excerpt: {excerpt}",
    ]
    return "\n".join(lines)


def parse_label_response(
    payload: str, vocabulary: Sequence[VocabularyFacet]
) -> list[LabelProposal]:
    """Map a model response onto proposals, enforcing the closed set.

    Never raises: a malformed response yields no proposals, which leaves the
    document unlabelled and visible in the review queue rather than failing the
    whole labelling run. Tolerates a markdown-fenced payload or surrounding
    prose (see :func:`~library.llm.envelope.strip_json_envelope`) — belt-and-braces alongside the
    structured-output call in :func:`label_document`, and the only path the
    subscription backend (free text, no ``messages.parse``) gets.
    """
    try:
        parsed = json.loads(strip_json_envelope(payload))
        entries = parsed["labels"]
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("facet labeller returned an unparseable payload")
        return []
    if not isinstance(entries, list):
        return []

    by_key = {facet.key: facet for facet in vocabulary}
    proposals: list[LabelProposal] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        facet_key = entry.get("facet")
        if not isinstance(facet_key, str):
            continue  # a non-string facet cannot name a facet; discard the entry
        facet = by_key.get(facet_key)
        if facet is None:
            continue  # an invented facet is discarded outright
        raw_value = entry.get("value")
        resolved: str | None = None
        if isinstance(raw_value, str):
            # Casefolded, not `.lower()`'d: the vocabulary genuinely contains
            # non-ASCII keys' display forms and aliases (``Škoda``, ``Citroën``),
            # and the model echoes them back in whatever casing it likes
            # (``Skoda``, ``SKODA``, ``škoda``...). Only the *matching* is
            # case-insensitive — the stored key/alias text is never touched, and
            # a value that matches nothing still falls through to `unknown` plus
            # a suggestion, so the closed-set guarantee is unaffected.
            folded_value = raw_value.casefold()
            match = next((v for v in facet.values if v.key.casefold() == folded_value), None)
            if match is None:
                match = next(
                    (
                        v
                        for v in facet.values
                        if any(a.casefold() == folded_value for a in v.aliases)
                    ),
                    None,
                )
            resolved = match.key if match is not None else None
        raw_suggest = entry.get("suggest")
        suggested = raw_suggest if isinstance(raw_suggest, str) else None
        if resolved is None and suggested is None and isinstance(raw_value, str):
            # The model named a value outside the vocabulary: keep it as the
            # suggestion rather than discarding what it was trying to say.
            suggested = raw_value
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        raw_reason = entry.get("reason")
        reason = raw_reason if isinstance(raw_reason, str) else ""
        # Truncate rather than discard: an over-long suggestion is still
        # evidence of what the model wanted, and a human reads it before it can
        # ever widen the vocabulary.
        clamped = suggested[:MAX_SUGGESTED_LABEL_CHARS] if suggested else None
        proposals.append(
            LabelProposal(
                facet_key=facet.key,
                value_key=resolved,
                confidence=min(1.0, max(0.0, confidence)),
                reason=reason,
                suggested_label=clamped or None,
            )
        )
    return proposals


async def label_document(
    settings: Settings,
    vocabulary: Sequence[VocabularyFacet],
    fields: DocumentFields,
    *,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> tuple[list[LabelProposal], int, int] | None:
    """``(proposals, input_tokens, output_tokens)``, or None when unrunnable.

    Mirrors ``series_insight.describe_series``: a missing API key is a quiet
    ``None`` (the caller skips the document) rather than an error.

    The API backend uses ``client.messages.parse()`` with :class:`LabelResponse`
    as the structured-output schema (the same approach as
    ``library.extraction.extractor``), rather than instructing the model in
    prose to return bare JSON: a real ``claude-haiku-4-5`` response wrapped its
    (otherwise-correct) JSON in a markdown fence despite that instruction, and
    every document in the run logged an unparseable payload. The subscription
    backend returns free text and cannot use ``parse()``, so it still goes
    through :func:`parse_label_response` directly, which is why that function
    also tolerates a fenced/prose-wrapped payload on its own.
    """
    prompt = build_labelling_prompt(vocabulary, fields)
    if backend == "subscription":
        result = await subscription.text_call(
            config_dir=settings.claude_config_dir,
            model=settings.extraction_model,
            system_prompt=LABELLER_SYSTEM_PROMPT,
            prompt=prompt,
        )
        return (
            parse_label_response(result.text, vocabulary),
            result.usage.input_tokens,
            result.usage.output_tokens,
        )

    async def _call(anthropic: AsyncAnthropic) -> tuple[list[LabelProposal], int, int]:
        response = await anthropic.messages.parse(
            model=settings.extraction_model,
            max_tokens=MAX_LABEL_TOKENS,
            system=LABELLER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=LabelResponse,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise LabelParseError(f"{settings.extraction_model} returned no parseable output")
        return (
            parse_label_response(parsed.model_dump_json(), vocabulary),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    if client is not None:
        return await _call(client)
    if settings.anthropic_api_key is None:
        return None
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as owned:
        return await _call(owned)
