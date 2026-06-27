"""Pydantic schema for Claude structured-output metadata extraction.

This model is the ``output_format`` passed to ``client.messages.parse()``.
Structured outputs do not support numeric min/max, string-length, or
array-length constraints, so anything of that shape is either instructed in
the prompt (summary length, tag style) or normalised by validators here
(tag slugs and cap, defensive amount parsing).

Date fields are typed ``date | None`` — pydantic renders them in the JSON
schema as ``{"type": "string", "format": "date"}``, which is in the
supported structured-outputs format list, and callers get real ``date``
objects. A before-validator trims whitespace and maps empty/placeholder
strings to ``None`` so sloppy-but-harmless model output degrades to "no
date"; a genuinely malformed date still raises a ``ValidationError``, which
the extractor treats as a parse failure and escalates.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

# The seeded rows of the `kinds` table (migration 0001). Constraining the
# schema to these slugs means the model can never invent a kind; "other" is
# the explicit catch-all.
KIND_SLUGS: tuple[str, ...] = (
    "invoice",
    "receipt",
    "certificate",
    "utility-bill",
    "parking-ticket",
    "warranty",
    "manual",
    "reference",
    "research",
    "note",
    "letter",
    "contract",
    "ticket",
    "other",
)

KindSlug = Literal[
    "invoice",
    "receipt",
    "certificate",
    "utility-bill",
    "parking-ticket",
    "warranty",
    "manual",
    "reference",
    "research",
    "note",
    "letter",
    "contract",
    "ticket",
    "other",
]

MAX_TAGS: int = 8
MAX_TOPICS: int = 12

# Strings the model sometimes emits instead of null for an absent date.
_DATE_PLACEHOLDERS: frozenset[str] = frozenset({"", "unknown", "none", "null", "n/a", "-"})


def _coerce_date(value: object) -> object:
    """Map empty/placeholder strings to None; leave real values for pydantic."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in _DATE_PLACEHOLDERS:
            return None
        return stripped
    return value


OptionalIsoDate = Annotated[date | None, BeforeValidator(_coerce_date)]


class ExtractedMetadata(BaseModel):
    """Structured metadata Claude extracts from one document."""

    model_config = ConfigDict(extra="forbid")

    kind_slug: KindSlug
    sender_name: str | None
    title: str
    summary: str
    document_date: OptionalIsoDate
    amount_total: str | None
    currency: str | None
    due_date: OptionalIsoDate
    expiry_date: OptionalIsoDate
    language: Literal["nld", "eng", "mixed", "unknown"]
    tags: list[str]
    topics: list[str] = []
    confidence: Literal["high", "medium", "low"]
    reasoning_note: str | None

    @field_validator("sender_name", "reasoning_note", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value.strip() if value else value

    @field_validator("amount_total", mode="after")
    @classmethod
    def _normalize_amount(cls, value: str | None) -> str | None:
        """Defensively normalise a decimal string; unparseable becomes None.

        A wrong amount is recoverable in the UI, but failing the whole
        extraction over "€ 12,50" is not worth it — hence normalise-or-drop
        instead of raise.
        """
        if value is None:
            return None
        cleaned = re.sub(r"[^\d.,\-]", "", value)
        if "," in cleaned and "." in cleaned:
            # Whichever separator comes last is the decimal point.
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            return str(Decimal(cleaned))
        except InvalidOperation:
            return None

    @field_validator("currency", mode="after")
    @classmethod
    def _normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        return code if re.fullmatch(r"[A-Z]{3}", code) else None

    @field_validator("tags", mode="after")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        """Lowercase-slugify, deduplicate, and cap at MAX_TAGS client-side.

        The schema cannot carry array-length or pattern constraints, so the
        prompt instructs the style and this validator enforces it.
        """
        slugs: list[str] = []
        for raw in value:
            slug = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
            if slug and slug not in slugs:
                slugs.append(slug)
        return slugs[:MAX_TAGS]

    @field_validator("topics", mode="after")
    @classmethod
    def _normalize_topics(cls, value: list[str]) -> list[str]:
        """Strip, case-insensitively deduplicate, and cap at MAX_TOPICS.

        Unlike tags, topics stay human-readable (not slugified): they are
        short prose phrases describing what a general document covers.
        """
        topics: list[str] = []
        seen: set[str] = set()
        for raw in value:
            cleaned = raw.strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                topics.append(cleaned)
        return topics[:MAX_TOPICS]
