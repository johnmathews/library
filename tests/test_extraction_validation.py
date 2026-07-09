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


def test_expiry_before_document_date_fires() -> None:
    """The expiry-before-document-date rule (previously untested — finding 7)."""
    doc = _doc(document_date=date(2026, 5, 1), expiry_date=date(2026, 4, 1))
    finding = _finding(
        validate(doc, kind_slug="warranty", ocr_floor=FLOOR, today=TODAY), "date_plausibility"
    )
    assert finding.field == "expiry_date"


def test_due_date_implausibly_old_without_document_date_fires() -> None:
    """With no document_date to anchor against, a 19xx due date is still caught."""
    doc = _doc(due_date=date(1970, 1, 1))
    finding = _finding(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY), "date_plausibility"
    )
    assert finding.field == "due_date"


def test_due_expiry_grounding_flags_expiry_with_only_due_cue() -> None:
    """A Dutch 'vervaldatum' (a due date) mislabeled as expiry_date is caught."""
    doc = _doc(expiry_date=date(2026, 7, 1), ocr_text="Factuur. Vervaldatum: 1 juli 2026.")
    finding = _finding(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY), "due_expiry_grounding"
    )
    assert finding.field == "expiry_date"


def test_due_expiry_grounding_flags_due_with_only_expiry_cue() -> None:
    doc = _doc(due_date=date(2030, 1, 1), ocr_text="Paspoort geldig tot 1 januari 2030.")
    finding = _finding(
        validate(doc, kind_slug="certificate", ocr_floor=FLOOR, today=TODAY), "due_expiry_grounding"
    )
    assert finding.field == "due_date"


def test_due_expiry_grounding_quiet_when_both_cues_present() -> None:
    doc = _doc(
        expiry_date=date(2026, 7, 1),
        ocr_text="Vervaldatum 1 juli 2026. Pas geldig tot 1 juli 2026.",
    )
    assert "due_expiry_grounding" not in _rules(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_recipient_fires_on_salutation() -> None:
    doc = _doc(recipient_id=None, ocr_text="Beste John,\n\nHierbij uw factuur.")
    finding = _finding(
        validate(doc, kind_slug="letter", ocr_floor=FLOOR, today=TODAY), "missing_recipient"
    )
    assert finding.field == "recipient_id"


def test_missing_recipient_fires_on_stored_addressee_raw() -> None:
    doc = _doc(recipient_id=None, extra={"extraction": {"addressee_raw": "Dhr. J. de Vries"}})
    assert "missing_recipient" in _rules(
        validate(doc, kind_slug="letter", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_recipient_quiet_when_recipient_present() -> None:
    doc = _doc(recipient_id=3, ocr_text="Beste John,")
    assert "missing_recipient" not in _rules(
        validate(doc, kind_slug="letter", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_recipient_quiet_on_ordinary_prose() -> None:
    """The salutation cues must not fire on everyday words (no needs_review noise)."""
    for prose in (
        "Gustav Mahler composed the octave range in Batavia.",  # 'tav' substrings
        "Dit is de beste optie voor uw situatie.",  # mid-sentence Dutch 'beste'
    ):
        doc = _doc(recipient_id=None, ocr_text=prose)
        assert "missing_recipient" not in _rules(
            validate(doc, kind_slug="research", ocr_floor=FLOOR, today=TODAY)
        ), prose


def test_due_expiry_grounding_quiet_on_passport_vervaldatum() -> None:
    """A passport/certificate 'vervaldatum' (its expiry) must not be misread as due."""
    doc = _doc(expiry_date=date(2030, 1, 1), ocr_text="Paspoort. Vervaldatum: 1 januari 2030.")
    assert "due_expiry_grounding" not in _rules(
        validate(doc, kind_slug="certificate", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_amount_fires_on_payment_cue_without_amount() -> None:
    """The thin-OCR miss: a payment/due term present but amount_total is null."""
    doc = _doc(
        amount_total=None,
        ocr_text="Garage Spaarndam. Gelieve te betalen binnen 14 dagen.",
    )
    finding = _finding(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY), "missing_amount"
    )
    assert finding.field == "amount_total"


def test_missing_amount_quiet_when_amount_present() -> None:
    doc = _doc(
        amount_total=Decimal("144.19"),
        currency="EUR",
        ocr_text="Totaal € 144,19. Gelieve te betalen binnen 14 dagen.",
    )
    assert "missing_amount" not in _rules(
        validate(doc, kind_slug="invoice", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_amount_quiet_without_payment_cue() -> None:
    doc = _doc(amount_total=None, ocr_text="A reference note about routers. No money here.")
    assert "missing_amount" not in _rules(
        validate(doc, kind_slug="reference", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_amount_quiet_on_non_monetary_vervaldatum() -> None:
    """A passport's 'vervaldatum' (expiry) with no amount must NOT flag missing_amount."""
    doc = _doc(amount_total=None, ocr_text="Paspoort. Vervaldatum: 1 januari 2030.")
    assert "missing_amount" not in _rules(
        validate(doc, kind_slug="certificate", ocr_floor=FLOOR, today=TODAY)
    )


def test_missing_sender_fires_on_signoff_without_amount() -> None:
    doc = _doc(
        sender_id=None,
        amount_total=None,
        ocr_text="...\n\nMet vriendelijke groet,\nGemeente Amsterdam",
    )
    finding = _finding(
        validate(doc, kind_slug="letter", ocr_floor=FLOOR, today=TODAY), "missing_sender"
    )
    assert "signed" in finding.message


def test_missing_sender_signoff_quiet_when_sender_present() -> None:
    doc = _doc(sender_id=9, amount_total=None, ocr_text="Best regards,\nEneco")
    assert "missing_sender" not in _rules(
        validate(doc, kind_slug="letter", ocr_floor=FLOOR, today=TODAY)
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


def test_email_item_ambiguous_fires_with_reason() -> None:
    doc = _doc(
        title="Calendar invite",
        extra={
            "email_selection": {
                "verdict": "ambiguous",
                "reason": "looks like a calendar invite",
                "source": "llm_label",
            }
        },
    )
    findings = validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    finding = _finding(findings, "email_item_ambiguous")
    assert "looks like a calendar invite" in finding.message
    assert derive_review_status(findings) is ReviewStatus.NEEDS_REVIEW


def test_email_item_ambiguous_probably_noise_fires() -> None:
    doc = _doc(
        extra={
            "email_selection": {
                "verdict": "probably_noise",
                "reason": "email signature logo",
                "source": "heuristic",
            }
        },
    )
    assert "email_item_ambiguous" in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_email_item_ambiguous_absent_when_verdict_keep() -> None:
    doc = _doc(
        extra={"email_selection": {"verdict": "keep", "reason": None, "source": "heuristic"}}
    )
    assert "email_item_ambiguous" not in _rules(
        validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY)
    )


def test_email_item_ambiguous_without_reason_generic_message() -> None:
    for extra in (
        {"email_selection": {"verdict": "ambiguous"}},
        {"email_selection": {"verdict": "ambiguous", "reason": None}},
        {"email_selection": {"verdict": "ambiguous", "reason": "  "}},
    ):
        doc = _doc(extra=extra)
        finding = _finding(
            validate(doc, kind_slug="other", ocr_floor=FLOOR, today=TODAY), "email_item_ambiguous"
        )
        assert "None" not in finding.message
        assert finding.message.strip()


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
