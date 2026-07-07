"""Unit tests for the pure extraction-validation rules (no DB, no IO)."""

from datetime import date
from decimal import Decimal

from library.extraction.validation import (
    Finding,
    derive_review_status,
    findings_to_payload,
    validate,
)
from library.models import Document, ReviewStatus

TODAY = date(2026, 6, 21)
FLOOR = 50.0


def _doc(**kwargs: object) -> Document:
    """A transient Document carrying only the attributes a rule reads.

    Built via the normal SQLAlchemy constructor (NOT __new__ + setattr):
    mapped columns are data descriptors, so an instance-dict value set with
    object.__setattr__ would be ignored on read. Unset scalar columns read
    back as None on a transient instance, which is what the rules expect.
    """
    defaults: dict[str, object] = {"ocr_text": "", "extra": {}}
    defaults.update(kwargs)
    return Document(**defaults)


def _rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


def test_amount_grounded_in_text_does_not_fire() -> None:
    doc = _doc(amount_total=Decimal("12.00"), currency="EUR", ocr_text="Totaal € 12,00 voldaan")
    assert "amount_grounding" not in _rules(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY)
    )


def test_amount_absent_from_text_fires() -> None:
    doc = _doc(amount_total=Decimal("999.99"), currency="EUR", ocr_text="Totaal € 12,00 voldaan")
    assert "amount_grounding" in _rules(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY)
    )


def test_future_document_date_fires() -> None:
    doc = _doc(document_date=date(2027, 1, 1))
    assert "date_plausibility" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_implausibly_old_document_date_fires() -> None:
    doc = _doc(document_date=date(1980, 1, 1))
    assert "date_plausibility" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_due_before_document_date_fires() -> None:
    doc = _doc(document_date=date(2026, 5, 1), due_date=date(2026, 4, 1))
    assert "date_plausibility" in _rules(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY)
    )


def test_amount_without_currency_fires() -> None:
    doc = _doc(amount_total=Decimal("10.00"), currency=None, ocr_text="10.00")
    assert "amount_currency_coupling" in _rules(
        validate(doc, kind_slug="receipt", ocr_floor=FLOOR, today=TODAY)
    )


def test_low_ocr_confidence_fires() -> None:
    doc = _doc(ocr_confidence=30.0)
    assert "ocr_confidence_gate" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_empty_extraction_fires() -> None:
    doc = _doc()  # kind other, no sender, no date, no amount
    assert "empty_extraction" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_general_doc_with_summary_not_flagged_empty() -> None:
    """A general doc with a real title/summary but no sender/date/amount is
    informative, so empty_extraction must NOT fire."""
    doc = _doc(title="Router setup guide", summary="How to configure the home router.")
    assert "empty_extraction" not in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_reference_kind_never_empty() -> None:
    """A non-other kind (e.g. reference) never triggers empty_extraction,
    even with no sender/date/amount/title/summary."""
    doc = _doc()
    assert "empty_extraction" not in _rules(
        validate(doc, kind_slug="reference", ocr_floor=FLOOR, today=TODAY)
    )


def test_self_reported_low_confidence_fires() -> None:
    doc = _doc(extra={"extraction": {"confidence": "low"}})
    assert "self_reported_low" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def _finding(findings: list[Finding], rule: str) -> Finding:
    matches = [f for f in findings if f.rule == rule]
    assert matches, f"expected a {rule} finding, got {[f.rule for f in findings]}"
    return matches[0]


def test_self_reported_low_surfaces_reasoning_note() -> None:
    """When the model left a reasoning note, it replaces the generic message."""
    doc = _doc(
        extra={
            "extraction": {"confidence": "low", "reasoning_note": "two candidate totals on page 2"}
        }
    )
    finding = _finding(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY), "self_reported_low"
    )
    assert "two candidate totals on page 2" in finding.message


def test_self_reported_low_without_note_falls_back_to_generic() -> None:
    for extra in (
        {"extraction": {"confidence": "low"}},
        {"extraction": {"confidence": "low", "reasoning_note": "  "}},
    ):
        doc = _doc(extra=extra)
        finding = _finding(
            validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY), "self_reported_low"
        )
        assert finding.message == "the extractor reported low confidence"


def test_missing_sender_fires_on_amount_without_sender() -> None:
    doc = _doc(amount_total=Decimal("42.00"), currency="EUR", sender_id=None, ocr_text="42.00")
    finding = _finding(
        validate(doc, kind_slug="receipt", ocr_floor=FLOOR, today=TODAY), "missing_sender"
    )
    assert finding.field == "sender_id"
    assert "sender" in finding.message


def test_missing_sender_does_not_fire_when_sender_present() -> None:
    doc = _doc(amount_total=Decimal("42.00"), currency="EUR", sender_id=7, ocr_text="42.00")
    assert "missing_sender" not in _rules(
        validate(doc, kind_slug="receipt", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_sender_does_not_fire_without_amount() -> None:
    doc = _doc(title="A note", summary="no money here", sender_id=None)
    assert "missing_sender" not in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_email_attachments_dropped_fires_and_lists_files() -> None:
    doc = _doc(
        title="Cover photo",
        extra={
            "email_siblings_dropped": [
                {"filename": "invoice-1.pdf", "reason": "unsupported_type", "detail": "x"},
                {"filename": "invoice-2.pdf", "reason": "unsupported_type", "detail": "x"},
            ]
        },
    )
    finding = _finding(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY), "email_attachments_dropped"
    )
    assert "2 other attachments" in finding.message
    assert "invoice-1.pdf" in finding.message and "invoice-2.pdf" in finding.message


def test_email_attachments_dropped_absent_when_no_siblings() -> None:
    doc = _doc(title="Cover photo", extra={"email_siblings_dropped": []})
    assert "email_attachments_dropped" not in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_clean_document_has_no_findings() -> None:
    doc = _doc(
        amount_total=Decimal("12.00"),
        currency="EUR",
        document_date=date(2026, 5, 1),
        ocr_text="Factuur totaal € 12,00",
        ocr_confidence=90.0,
        sender_id=7,
        extra={"extraction": {"confidence": "high"}},
    )
    assert validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY) == []


def test_derive_status_and_payload() -> None:
    assert derive_review_status([]) is ReviewStatus.UNREVIEWED
    findings = [Finding(rule="empty_extraction", field=None, severity="warn", message="x")]
    assert derive_review_status(findings) is ReviewStatus.NEEDS_REVIEW
    assert findings_to_payload(findings) == [
        {"rule": "empty_extraction", "field": None, "severity": "warn", "message": "x"}
    ]
