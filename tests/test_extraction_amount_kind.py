"""The extractor's two new money fields, and how apply persists them."""

from library.extraction.schema import MAX_REFERENCE_CHARS, ExtractedMetadata, normalize_amount_kind


def test_the_seven_kinds_are_accepted() -> None:
    for kind in (
        "payment_due",
        "payment_made",
        "assessment",
        "coverage_limit",
        "balance",
        "estimate",
        "none",
    ):
        assert normalize_amount_kind(kind) == kind


def test_an_unknown_kind_becomes_none_rather_than_a_payment() -> None:
    """A kind we cannot read must never default into a summable one."""
    assert normalize_amount_kind("invoice_total") is None
    assert normalize_amount_kind("") is None
    assert normalize_amount_kind(None) is None


def test_kind_matching_is_case_and_space_insensitive() -> None:
    assert normalize_amount_kind("  Payment_Made ") == "payment_made"
    assert normalize_amount_kind("payment made") == "payment_made"


def test_a_blank_reference_normalises_to_none() -> None:
    result = ExtractedMetadata.model_validate(_minimal_payload(reference="   "))
    assert result.reference is None


def test_a_reference_is_kept_verbatim_apart_from_trimming() -> None:
    result = ExtractedMetadata.model_validate(_minimal_payload(reference=" INV-77/A "))
    assert result.reference == "INV-77/A"


def test_an_over_long_reference_is_truncated_not_dropped() -> None:
    """Document.reference is String(128); an unclamped value raises a
    DataError at commit rather than a validation error here, which is
    exactly the failure class this project has already shipped once (as an
    unclamped suggested_label in the facets labeller).
    """
    long_reference = "R" * 300
    result = ExtractedMetadata.model_validate(_minimal_payload(reference=long_reference))
    assert result.reference is not None
    assert len(result.reference) == MAX_REFERENCE_CHARS
    assert result.reference == long_reference[:MAX_REFERENCE_CHARS]


def _minimal_payload(**overrides: object) -> dict[str, object]:
    """The smallest payload ExtractedMetadata validates, plus overrides.

    Built from the model's own required fields so this test does not drift when
    unrelated fields are added.
    """
    payload: dict[str, object] = {
        "kind_slug": "invoice",
        "sender_name": "Vendor",
        "recipient_name": None,
        "title": "A title",
        "summary": "A summary.",
        "document_date": None,
        "amount_total": None,
        "currency": None,
        "amount_kind": None,
        "reference": None,
        "due_date": None,
        "expiry_date": None,
        "language": "unknown",
        "tags": [],
        "confidence": "high",
        "reasoning_note": None,
        "addressee_raw": None,
        "signer_raw": None,
    }
    payload.update(overrides)
    return payload
