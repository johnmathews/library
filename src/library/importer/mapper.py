"""Map paperless-ngx API payloads onto Library's document model.

Pure functions: no database access, no HTTP. The runner resolves the
mapped values (sender upsert, kind lookup, tag creation) against the
database. Mapping rules are documented in docs/migration.md.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

NEEDS_REVIEW_TAG_SLUG: str = "needs-review"

# paperless document-type name (lowercased) -> Library kind slug. Unmapped
# types fall back to "other" plus a `paperless:<type-name>` tag so the
# original classification is never lost.
KIND_SLUG_BY_TYPE_NAME: dict[str, str] = {
    # invoice
    "invoice": "invoice",
    "invoices": "invoice",
    "factuur": "invoice",
    "facturen": "invoice",
    "rekening": "invoice",
    # receipt
    "receipt": "receipt",
    "receipts": "receipt",
    "bon": "receipt",
    "kassabon": "receipt",
    "aankoopbon": "receipt",
    "kwitantie": "receipt",
    # certificate
    "certificate": "certificate",
    "certificaat": "certificate",
    "diploma": "certificate",
    "getuigschrift": "certificate",
    # utility bill
    "utility bill": "utility-bill",
    "energierekening": "utility-bill",
    "energiefactuur": "utility-bill",
    "nutsrekening": "utility-bill",
    # parking ticket
    "parking ticket": "parking-ticket",
    "parkeerbon": "parking-ticket",
    # warranty
    "warranty": "warranty",
    "garantie": "warranty",
    "garantiebewijs": "warranty",
    # manual
    "manual": "manual",
    "handleiding": "manual",
    "gebruiksaanwijzing": "manual",
    # letter
    "letter": "letter",
    "brief": "letter",
    "correspondentie": "letter",
    # contract
    "contract": "contract",
    "overeenkomst": "contract",
    "polis": "contract",
    # ticket
    "ticket": "ticket",
    "kaartje": "ticket",
    "toegangsbewijs": "ticket",
    "entreebewijs": "ticket",
}

# Currency-prefixed decimal, the paperless monetary wire format
# ("EUR123.45"); a bare number is also valid (currency then comes from the
# field's extra_data.default_currency, if any).
_MONETARY_RE: re.Pattern[str] = re.compile(r"^([A-Za-z]{3})?\s*(-?\d+(?:[.,]\d{1,2})?)$")


def slugify(value: str) -> str:
    """Lowercase, non-alphanumerics to hyphens, capped to the 64-char slug column."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:64].strip("-")


def parse_created(value: str | None) -> date | None:
    """Parse paperless ``created``: plain date on API v9, datetime on older instances."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def parse_monetary(value: object) -> tuple[Decimal, str | None] | None:
    """Parse a paperless monetary custom-field value into (amount, currency|None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return Decimal(str(value)), None
    if not isinstance(value, str):
        return None
    match = _MONETARY_RE.match(value.strip())
    if match is None:
        return None
    try:
        amount = Decimal(match.group(2).replace(",", "."))
    except InvalidOperation:  # pragma: no cover - regex precludes this
        return None
    currency = match.group(1).upper() if match.group(1) else None
    return amount, currency


@dataclass(frozen=True)
class TagSpec:
    """A Library tag to attach: slug plus the display name to use if created."""

    slug: str
    name: str


@dataclass(frozen=True)
class Taxonomies:
    """paperless taxonomy objects indexed by id, as fetched once per run."""

    tags: dict[int, dict[str, Any]]
    correspondents: dict[int, dict[str, Any]]
    document_types: dict[int, dict[str, Any]]
    custom_fields: dict[int, dict[str, Any]]

    @classmethod
    def from_lists(
        cls,
        tags: list[dict[str, Any]],
        correspondents: list[dict[str, Any]],
        document_types: list[dict[str, Any]],
        custom_fields: list[dict[str, Any]],
    ) -> "Taxonomies":
        return cls(
            tags={item["id"]: item for item in tags},
            correspondents={item["id"]: item for item in correspondents},
            document_types={item["id"]: item for item in document_types},
            custom_fields={item["id"]: item for item in custom_fields},
        )


@dataclass
class MappedDocument:
    """A paperless document translated into Library terms, ready to import."""

    paperless_id: int
    title: str | None
    document_date: date | None
    content: str | None  # paperless OCR text, reused as ocr_text when present
    mime_type: str | None
    original_filename: str | None
    sender_name: str | None
    kind_slug: str | None  # None = no document type in paperless; leave to extraction
    tags: list[TagSpec] = field(default_factory=list)
    amount_total: Decimal | None = None
    currency: str | None = None
    # custom-field name -> linked paperless document ids (second-pass remap)
    linked_document_ids: dict[str, list[int]] = field(default_factory=dict)
    # the extra["paperless"] payload (batch_id/linked_documents added by the runner)
    extra: dict[str, Any] = field(default_factory=dict)


def _map_custom_fields(
    entries: list[dict[str, Any]], taxonomies: Taxonomies
) -> tuple[dict[str, Any], dict[str, list[int]], Decimal | None, str | None]:
    """Resolve custom-field values; return (values, links, amount, currency)."""
    values: dict[str, Any] = {}
    links: dict[str, list[int]] = {}
    amount: Decimal | None = None
    currency: str | None = None
    for entry in entries:
        definition = taxonomies.custom_fields.get(entry.get("field"))  # type: ignore[arg-type]
        name = definition["name"] if definition else f"field-{entry.get('field')}"
        data_type = definition.get("data_type") if definition else None
        extra_data = (definition.get("extra_data") or {}) if definition else {}
        value = entry.get("value")
        if data_type == "monetary" and value is not None:
            parsed = parse_monetary(value)
            if parsed is not None and amount is None:
                amount, currency = parsed
                if currency is None:
                    default = extra_data.get("default_currency")
                    currency = default.upper() if isinstance(default, str) and default else None
            values[name] = value
        elif data_type == "select" and value is not None:
            options = extra_data.get("select_options") or []
            label = next(
                (opt.get("label") for opt in options if opt.get("id") == value),
                value,
            )
            values[name] = label
        elif data_type == "documentlink" and value:
            links[name] = [int(item) for item in value]
            values[name] = list(value)
        else:
            values[name] = value
    return values, links, amount, currency


def map_document(doc: dict[str, Any], taxonomies: Taxonomies) -> MappedDocument:
    """Translate one ``/api/documents/`` result into Library terms."""
    tags: list[TagSpec] = []
    needs_review = False
    for tag_id in doc.get("tags") or []:
        tag = taxonomies.tags.get(tag_id)
        if tag is None:
            continue
        slug = slugify(tag["name"])
        if slug:
            tags.append(TagSpec(slug=slug, name=tag["name"]))
        if tag.get("is_inbox_tag"):
            needs_review = True
    if needs_review:
        tags.append(TagSpec(slug=NEEDS_REVIEW_TAG_SLUG, name="Needs review"))

    doc_type = taxonomies.document_types.get(doc.get("document_type"))  # type: ignore[arg-type]
    kind_slug: str | None = None
    if doc_type is not None:
        type_name = doc_type["name"]
        kind_slug = KIND_SLUG_BY_TYPE_NAME.get(type_name.strip().lower())
        if kind_slug is None:
            # Unmapped type: classify as "other" but keep the original type
            # as a tag so nothing is lost.
            kind_slug = "other"
            tags.append(
                TagSpec(
                    slug=slugify(f"paperless {type_name}"),
                    name=f"paperless:{type_name}",
                )
            )

    correspondent = taxonomies.correspondents.get(doc.get("correspondent"))  # type: ignore[arg-type]
    sender_name = correspondent["name"] if correspondent else None

    values, links, amount, currency = _map_custom_fields(doc.get("custom_fields") or [], taxonomies)

    extra: dict[str, Any] = {"id": doc["id"]}
    if doc.get("added"):
        extra["added"] = doc["added"]
    if doc.get("archive_serial_number") is not None:
        extra["asn"] = doc["archive_serial_number"]
    if doc_type is not None:
        extra["document_type"] = doc_type["name"]
    notes = [
        {"note": note.get("note"), "created": note.get("created")}
        for note in doc.get("notes") or []
    ]
    if notes:
        extra["notes"] = notes
    if values:
        extra["custom_fields"] = values

    content = (doc.get("content") or "").strip() or None
    title = (doc.get("title") or "").strip() or None

    # Deduplicate tag slugs, first occurrence wins.
    seen: set[str] = set()
    unique_tags = [tag for tag in tags if not (tag.slug in seen or seen.add(tag.slug))]

    return MappedDocument(
        paperless_id=doc["id"],
        title=title,
        document_date=parse_created(doc.get("created")),
        content=content,
        mime_type=doc.get("mime_type"),
        original_filename=doc.get("original_file_name"),
        sender_name=sender_name,
        kind_slug=kind_slug,
        tags=unique_tags,
        amount_total=amount,
        currency=currency,
        linked_document_ids=links,
        extra=extra,
    )
