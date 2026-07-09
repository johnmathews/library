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

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

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


class ExtractedMetadata(BaseModel):
    """Structured metadata Claude extracts from one document."""

    model_config = ConfigDict(extra="forbid")

    kind_slug: KindSlug = Field(
        description=(
            "The single document type that fits best. invoice = a request for "
            "payment (amount owed, often a due date); receipt = proof a payment "
            "already happened; utility-bill = a recurring energy/water/telecom/"
            "municipal charge (prefer over invoice for utilities); letter = "
            "personal or official correspondence with no contractual terms; "
            "contract = an agreement both parties are bound by; certificate = "
            "attests a fact or qualification; warranty = a guarantee tied to a "
            "purchase; ticket = an admission or travel ticket; manual = product "
            "or how-to instructions; research = a paper or study; reference = "
            "general reference material; note = personal notes. Use 'other' only "
            "when nothing else fits."
        )
    )
    sender_name: str | None = Field(
        description=(
            "The organisation or person that ISSUED the document, in short "
            "canonical form (e.g. 'Eneco', not 'Eneco Services B.V.'). For a "
            "letter or email the signer is the sender: a sign-off such as 'Met "
            "vriendelijke groet, Y' / 'Best regards, Y' / 'Hoogachtend, Y' means "
            "Y (or Y's organisation) is the sender, NOT the recipient. null when "
            "genuinely unclear."
        )
    )
    recipient_name: str | None = Field(
        description=(
            "The person the document is addressed TO. The salutation is the "
            "strongest signal: 'Dear John' / 'Beste John' / 'Geachte heer "
            "Mathews' / 'T.a.v. John Mathews' -> John Mathews. A letter, invoice "
            "or personally-addressed notice almost always HAS a recipient even "
            "if only a first name; extract it. Never confuse the salutation "
            "(recipient) with the sign-off (sender). null only for impersonal "
            "material such as a manual or generic reference document."
        )
    )
    title: str
    summary: str
    document_date: Annotated[
        date | None,
        BeforeValidator(_coerce_date),
        Field(description="The document's own issue or print date. null when absent."),
    ]
    amount_total: str | None
    currency: str | None
    due_date: Annotated[
        date | None,
        BeforeValidator(_coerce_date),
        Field(
            description=(
                "A date by which the recipient must ACT — pay, respond, renew or "
                "return ('te betalen voor', 'uiterste betaaldatum', 'vervaldatum' "
                "on an invoice, 'pay by', 'due by'). An obligation deadline, NOT a "
                "validity end. null when the document imposes no deadline."
            )
        ),
    ]
    expiry_date: Annotated[
        date | None,
        BeforeValidator(_coerce_date),
        Field(
            description=(
                "A date after which the document or entitlement is NO LONGER "
                "VALID (passports, warranties, insurance, contracts: 'geldig "
                "tot', 'vervalt op', 'verloopt', 'valid until', 'expires'). A "
                "validity horizon, NOT a payment deadline. Never copy the same "
                "date into both due_date and expiry_date — pick the field the "
                "surrounding words match. null when nothing expires."
            )
        ),
    ]
    language: Literal["nld", "eng", "mixed", "unknown"]
    tags: list[str]
    topics: list[str] = []
    confidence: Literal["high", "low"]
    reasoning_note: str | None
    addressee_raw: str | None = Field(
        default=None,
        description=(
            "The verbatim name the salutation/addressee block is directed at "
            "(e.g. the exact text after 'Dear' / 'Beste' / 'T.a.v.'), copied as "
            "printed, before any canonicalisation. null when the document has no "
            "salutation or addressee block."
        ),
    )
    signer_raw: str | None = Field(
        default=None,
        description=(
            "The verbatim name in the sign-off block (e.g. the exact text after "
            "'Met vriendelijke groet,' / 'Best regards,'), copied as printed. "
            "null when the document is not signed."
        ),
    )

    @field_validator(
        "sender_name",
        "recipient_name",
        "reasoning_note",
        "addressee_raw",
        "signer_raw",
        mode="after",
    )
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
