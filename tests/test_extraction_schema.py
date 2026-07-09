"""Tests for the ExtractedMetadata structured-output schema."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from library.extraction.schema import KIND_SLUGS, MAX_TAGS, MAX_TOPICS, ExtractedMetadata


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind_slug": "invoice",
        "sender_name": "Eneco",
        "recipient_name": "John",
        "title": "Energierekening mei 2026",
        "summary": "Maandfactuur voor energie. Te betalen voor 1 juli 2026.",
        "document_date": "2026-05-15",
        "amount_total": "123.45",
        "currency": "EUR",
        "due_date": "2026-07-01",
        "expiry_date": None,
        "language": "nld",
        "tags": ["energie", "wonen"],
        "confidence": "high",
        "reasoning_note": None,
    }
    base.update(overrides)
    return base


def test_valid_payload_parses_with_real_dates() -> None:
    metadata = ExtractedMetadata.model_validate(payload())
    assert metadata.kind_slug == "invoice"
    assert metadata.document_date == date(2026, 5, 15)
    assert metadata.due_date == date(2026, 7, 1)
    assert metadata.expiry_date is None
    assert metadata.amount_total == "123.45"
    assert metadata.currency == "EUR"


def test_all_seeded_kind_slugs_accepted() -> None:
    for slug in KIND_SLUGS:
        assert ExtractedMetadata.model_validate(payload(kind_slug=slug)).kind_slug == slug


def test_unknown_kind_slug_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate(payload(kind_slug="tax-return"))


def test_unknown_confidence_and_language_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate(payload(confidence="very-high"))
    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate(payload(language="deu"))


def test_placeholder_date_strings_become_none() -> None:
    for value in ("", "  ", "unknown", "None", "n/a", "-"):
        metadata = ExtractedMetadata.model_validate(payload(document_date=value))
        assert metadata.document_date is None


def test_date_with_surrounding_whitespace_parses() -> None:
    metadata = ExtractedMetadata.model_validate(payload(document_date=" 2026-05-15 "))
    assert metadata.document_date == date(2026, 5, 15)


def test_malformed_date_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate(payload(document_date="2026-13-45"))


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate(payload(invented_field="boom"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123.45", "123.45"),
        (" 1234.56 ", "1234.56"),
        ("€ 12,50", "12.50"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("garbage", None),
        (None, None),
    ],
)
def test_amount_parsed_defensively(raw: str | None, expected: str | None) -> None:
    metadata = ExtractedMetadata.model_validate(payload(amount_total=raw))
    assert metadata.amount_total == expected


def test_currency_normalised_or_dropped() -> None:
    assert ExtractedMetadata.model_validate(payload(currency=" eur ")).currency == "EUR"
    assert ExtractedMetadata.model_validate(payload(currency="euros")).currency is None


def test_tags_normalised_deduplicated_and_capped() -> None:
    raw = ["Tax", "  Energie Rekening ", "tax", *[f"tag-{i}" for i in range(10)]]
    metadata = ExtractedMetadata.model_validate(payload(tags=raw))
    assert metadata.tags[:2] == ["tax", "energie-rekening"]
    assert len(metadata.tags) == MAX_TAGS
    assert len(set(metadata.tags)) == MAX_TAGS


def test_blank_sender_becomes_none() -> None:
    assert ExtractedMetadata.model_validate(payload(sender_name="   ")).sender_name is None


def test_recipient_name_parses_and_trims() -> None:
    assert (
        ExtractedMetadata.model_validate(payload(recipient_name=" Wife ")).recipient_name == "Wife"
    )
    assert ExtractedMetadata.model_validate(payload(recipient_name=None)).recipient_name is None


def test_blank_recipient_becomes_none() -> None:
    assert ExtractedMetadata.model_validate(payload(recipient_name="   ")).recipient_name is None


def test_addressee_and_signer_raw_default_none_when_omitted() -> None:
    """The verbatim salutation/sign-off capture fields are optional."""
    base = payload()
    base.pop("addressee_raw", None)
    base.pop("signer_raw", None)
    metadata = ExtractedMetadata.model_validate(base)
    assert metadata.addressee_raw is None
    assert metadata.signer_raw is None


def test_addressee_and_signer_raw_parse_and_trim() -> None:
    metadata = ExtractedMetadata.model_validate(
        payload(addressee_raw="  Mr. J. de Vries ", signer_raw=" Eneco B.V. ")
    )
    assert metadata.addressee_raw == "Mr. J. de Vries"
    assert metadata.signer_raw == "Eneco B.V."


def test_blank_addressee_and_signer_raw_become_none() -> None:
    metadata = ExtractedMetadata.model_validate(payload(addressee_raw="   ", signer_raw=""))
    assert metadata.addressee_raw is None
    assert metadata.signer_raw is None


def test_focus_fields_carry_json_schema_descriptions() -> None:
    """Per-field descriptions guide the model (Anthropic structured-output best practice)."""
    props = ExtractedMetadata.model_json_schema()["properties"]
    for field in (
        "kind_slug",
        "sender_name",
        "recipient_name",
        "document_date",
        "due_date",
        "expiry_date",
        "addressee_raw",
        "signer_raw",
    ):
        assert props[field].get("description"), f"{field} is missing a schema description"


def test_topics_default_empty() -> None:
    """topics is optional and defaults to an empty list when omitted."""
    base = payload()
    base.pop("topics", None)
    assert ExtractedMetadata.model_validate(base).topics == []


def test_topics_trimmed_deduped_capped() -> None:
    """topics are trimmed, case-insensitively deduped, capped, NOT slugified."""
    raw = [
        "  Machine Learning ",
        "machine learning",
        "Neural Networks",
        "",
        "   ",
        *[f"Topic {i}" for i in range(15)],
    ]
    metadata = ExtractedMetadata.model_validate(payload(topics=raw))
    # Human-readable, not slugified; first two survive dedup in order.
    assert metadata.topics[:2] == ["Machine Learning", "Neural Networks"]
    assert len(metadata.topics) == MAX_TOPICS
    # Case-insensitive dedup: no two topics equal ignoring case.
    lowered = [t.lower() for t in metadata.topics]
    assert len(set(lowered)) == len(lowered)
