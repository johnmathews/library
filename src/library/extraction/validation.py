"""Deterministic, zero-cost validation of extracted metadata.

Each rule inspects already-extracted fields and the source OCR text and may
emit a :class:`Finding`. The module is pure (stdlib + models only) so it is
unit-testable without a database. Date-grounding is deliberately out of scope
this phase (see the design spec).
"""

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from library.models import Document, ReviewStatus

_MIN_PLAUSIBLE_DATE: date = date(1990, 1, 1)


@dataclass(frozen=True, slots=True)
class Finding:
    """One validation concern about a document's extracted metadata."""

    rule: str
    field: str | None
    severity: str  # "warn" for now; reserved for future "error"
    message: str


def _amount_appears(amount: Decimal, text: str) -> bool:
    """True if the amount's digits appear in the OCR text.

    Compares on digit sequences so "12,00", "€ 12.00" and "12.00" all match a
    stored ``Decimal("12.00")``. Falls back to the integer part to tolerate
    totals printed without decimals. Lenient by design: a false "present" is
    safer than nagging on a correct amount.
    """
    text_digits = re.sub(r"\D", "", text)
    if not text_digits:
        return False
    cents = f"{amount:.2f}".replace(".", "")
    integer = str(int(amount))
    return cents in text_digits or integer in text_digits


def validate(
    document: Document,
    *,
    kind_slug: str | None,
    ocr_floor: float,
    today: date,
) -> list[Finding]:
    """Run every rule against a document; return the findings that fired."""
    findings: list[Finding] = []
    text = document.ocr_text or ""

    # amount_grounding — amount set but its digits are absent from the text.
    if (
        document.amount_total is not None
        and text
        and not _amount_appears(document.amount_total, text)
    ):
        findings.append(
            Finding(
                rule="amount_grounding",
                field="amount_total",
                severity="warn",
                message="amount_total does not appear in the document text",
            )
        )

    # date_plausibility — future, ancient, or due/expiry before the document date.
    doc_date = document.document_date
    if doc_date is not None:
        if doc_date > today:
            findings.append(
                Finding(
                    "date_plausibility", "document_date", "warn", "document_date is in the future"
                )
            )
        elif doc_date < _MIN_PLAUSIBLE_DATE:
            findings.append(
                Finding(
                    "date_plausibility", "document_date", "warn", "document_date is implausibly old"
                )
            )
        if document.due_date is not None and document.due_date < doc_date:
            findings.append(
                Finding("date_plausibility", "due_date", "warn", "due_date is before document_date")
            )
        if document.expiry_date is not None and document.expiry_date < doc_date:
            findings.append(
                Finding(
                    "date_plausibility",
                    "expiry_date",
                    "warn",
                    "expiry_date is before document_date",
                )
            )

    # amount_currency_coupling — exactly one of amount/currency is set.
    if (document.amount_total is None) != (document.currency is None):
        findings.append(
            Finding(
                "amount_currency_coupling",
                "currency",
                "warn",
                "amount_total and currency must be set together",
            )
        )

    # missing_sender — a monetary document (a bill/receipt/invoice always has a
    # payee) whose sender we could not identify. Scoped to amount-bearing docs so
    # it stays specific and doesn't nag on notes or personal letters.
    if document.amount_total is not None and document.sender_id is None:
        findings.append(
            Finding(
                "missing_sender",
                "sender_id",
                "warn",
                "this looks like a bill or receipt but the sender could not be identified",
            )
        )

    # ocr_confidence_gate — extraction built on low-confidence OCR.
    if document.ocr_confidence is not None and document.ocr_confidence < ocr_floor:
        findings.append(
            Finding(
                "ocr_confidence_gate",
                None,
                "warn",
                f"OCR confidence {document.ocr_confidence:.0f} below floor {ocr_floor:.0f}",
            )
        )

    # empty_extraction — kind=other and nothing else learned (incl. no
    # title/summary, so a general doc with a real summary is not flagged).
    if (
        (kind_slug is None or kind_slug == "other")
        and document.sender_id is None
        and document.document_date is None
        and document.amount_total is None
        and not document.title
        and not document.summary
    ):
        findings.append(
            Finding("empty_extraction", None, "warn", "extraction produced no useful metadata")
        )

    # self_reported_low — the model said it was unsure. Surface *why* when the
    # model left a one-line note (captured at apply.py into extra["extraction"]
    # ["reasoning_note"]); fall back to the generic line when it didn't.
    extraction = document.extra.get("extraction") if isinstance(document.extra, dict) else None
    if isinstance(extraction, dict) and extraction.get("confidence") == "low":
        note = extraction.get("reasoning_note")
        message = (
            f"the extractor was unsure: {note.strip()}"
            if isinstance(note, str) and note.strip()
            else "the extractor reported low confidence"
        )
        findings.append(Finding("self_reported_low", None, "warn", message))

    # email_attachments_dropped — this document came from an email whose *other*
    # attachments could not be added (stamped on extra at ingest by email_ingest,
    # only-when-non-empty), so the reviewer knows files are missing from the set.
    dropped = (
        document.extra.get("email_siblings_dropped") if isinstance(document.extra, dict) else None
    )
    if isinstance(dropped, list) and dropped:
        names = [
            str(item.get("filename") or "an unnamed file")
            for item in dropped
            if isinstance(item, dict)
        ]
        listed = ", ".join(names) if names else "one or more files"
        count = len(names) or len(dropped)
        noun = "attachment" if count == 1 else "attachments"
        findings.append(
            Finding(
                "email_attachments_dropped",
                None,
                "warn",
                f"the email included {count} other {noun} that could not be added: {listed}",
            )
        )

    return findings


def derive_review_status(findings: list[Finding]) -> ReviewStatus:
    """Any finding => needs_review; otherwise unreviewed (never auto-verified)."""
    return ReviewStatus.NEEDS_REVIEW if findings else ReviewStatus.UNREVIEWED


def findings_to_payload(findings: list[Finding]) -> list[dict[str, Any]]:
    """Serialise findings for storage in ``extra["validation"]``."""
    return [asdict(f) for f in findings]
