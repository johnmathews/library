"""Deterministic, zero-cost validation of extracted metadata.

Each rule inspects already-extracted fields and the source OCR text and may
emit a :class:`Finding`. The module is pure (stdlib + models only) so it is
unit-testable without a database. Alongside the value-shape rules, several
rules cross-check extracted values against bilingual cue words in the OCR text
(due/expiry wording, salutation, sign-off) to catch the model mislabeling or
missing a recipient, sender, or date.
"""

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from library.models import Document, ReviewStatus

_MIN_PLAUSIBLE_DATE: date = date(1990, 1, 1)

# Bilingual (Dutch/English) cue-word sets for deterministic date cross-checks.
# Anchored on full tokens/phrases, NOT the "verval-" stem, because Dutch
# "vervaldatum" (a payment DUE date on an invoice) and "vervalt"/"verloopt" (an
# EXPIRY on a pass or policy) are near-homographs that must not collide.
_DUE_CUES = re.compile(
    r"vervaldatum|uiterste\s+betaaldatum|te\s+betalen\s+v[oó]{1,2}r|gelieve\s+te\s+betalen"
    r"|betaal\s+voor|due\s+by|due\s+date|payable\s+by|pay\s+by|payment\s+due|amount\s+due",
    re.IGNORECASE,
)
_EXPIRY_CUES = re.compile(
    r"geldig\s+tot|vervalt\s+op|verloopt|houdbaar\s+tot|valid\s+(?:until|through|to)"
    r"|expir(?:es|y|ation)",
    re.IGNORECASE,
)
# A salutation/addressee block signals the document names a recipient. Kept
# precise to avoid firing on ordinary prose: the dotted "t.a.v." abbreviation
# (not the bare "tav" inside "octave"/"Batavia"), "geachte"/"dear", "ter
# attentie van", and "beste" only at the start of a line (a real salutation),
# never the everyday Dutch adjective "beste" ("best") mid-sentence.
_SALUTATION_CUES = re.compile(
    r"\bdear\b|\bgeachte\b|t\.a\.v\.|ter\s+attentie\s+van|^\s*beste\b",
    re.IGNORECASE | re.MULTILINE,
)
# Kinds where "vervaldatum" means a payment DUE date (invoices/utilities) vs.
# kinds where it means an EXPIRY (passes, warranties, contracts). The
# due/expiry cross-check only fires in the direction the kind makes unambiguous,
# so a passport's "vervaldatum" is not misread as a mislabeled due date.
_MONETARY_KINDS: frozenset[str] = frozenset({"invoice", "utility-bill", "receipt", "quote"})
_VALIDITY_KINDS: frozenset[str] = frozenset({"certificate", "warranty", "contract", "ticket"})
# A sign-off block signals the document is signed (its sender).
_SIGNOFF_CUES = re.compile(
    r"met\s+vriendelijke\s+groet|vriendelijke\s+groeten|hoogachtend"
    r"|best\s+regards|kind\s+regards|yours\s+(?:sincerely|faithfully)",
    re.IGNORECASE,
)


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
    extraction = document.extra.get("extraction") if isinstance(document.extra, dict) else None
    extraction = extraction if isinstance(extraction, dict) else {}

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
    else:
        # No document_date to anchor against, so the "before document_date"
        # checks above cannot fire — still catch an implausibly ancient due or
        # expiry date on its own (a common OCR misread, e.g. a 19xx year).
        for field_name, value in (
            ("due_date", document.due_date),
            ("expiry_date", document.expiry_date),
        ):
            if value is not None and value < _MIN_PLAUSIBLE_DATE:
                findings.append(
                    Finding(
                        "date_plausibility", field_name, "warn", f"{field_name} is implausibly old"
                    )
                )

    # due_expiry_grounding — the due/expiry mix-up caught deterministically. If a
    # date is set but the OCR text shows ONLY the opposite kind of cue word, the
    # model likely mislabeled it (classically a Dutch "vervaldatum" — a due date —
    # written into expiry_date).
    if text:
        has_due_cue = bool(_DUE_CUES.search(text))
        has_expiry_cue = bool(_EXPIRY_CUES.search(text))
        # Only fire in the direction the kind disambiguates, so an ambiguous
        # homograph (Dutch "vervaldatum" = due on an invoice, expiry on a pass)
        # is not misread against a correctly-extracted date.
        if (
            document.expiry_date is not None
            and has_due_cue
            and not has_expiry_cue
            and kind_slug in _MONETARY_KINDS
        ):
            findings.append(
                Finding(
                    "due_expiry_grounding",
                    "expiry_date",
                    "warn",
                    "expiry_date is set but the text shows only payment/due wording "
                    "(the date may belong in due_date)",
                )
            )
        if (
            document.due_date is not None
            and has_expiry_cue
            and not has_due_cue
            and kind_slug in _VALIDITY_KINDS
        ):
            findings.append(
                Finding(
                    "due_expiry_grounding",
                    "due_date",
                    "warn",
                    "due_date is set but the text shows only validity/expiry wording "
                    "(the date may belong in expiry_date)",
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

    # missing_recipient — the document appears to name an addressee (a stored
    # verbatim addressee, or a salutation in the text) yet no recipient was
    # recorded. Serves the "a personally-addressed document has a recipient" goal.
    addressee_raw = extraction.get("addressee_raw")
    if document.recipient_id is None and (
        (isinstance(addressee_raw, str) and addressee_raw.strip())
        or (text and _SALUTATION_CUES.search(text))
    ):
        findings.append(
            Finding(
                "missing_recipient",
                "recipient_id",
                "warn",
                "the document appears to name a recipient but none was recorded",
            )
        )

    # missing_sender (signed letter) — a signed document with no sender, for the
    # non-monetary case the amount-bearing rule above does not cover.
    signer_raw = extraction.get("signer_raw")
    if (
        document.amount_total is None
        and document.sender_id is None
        and (
            (isinstance(signer_raw, str) and signer_raw.strip())
            or (text and _SIGNOFF_CUES.search(text))
        )
    ):
        findings.append(
            Finding(
                "missing_sender",
                "sender_id",
                "warn",
                "the document appears to be signed but the sender could not be identified",
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

    # email_item_ambiguous — this document came from an email item that selection
    # tagged as "ambiguous" or "probably_noise" (stamped on extra["email_selection"]
    # by the selection unit), so the reviewer confirms it is a real document.
    sel = document.extra.get("email_selection") if isinstance(document.extra, dict) else None
    if isinstance(sel, dict) and sel.get("verdict") in {"ambiguous", "probably_noise"}:
        note = sel.get("reason")
        message = (
            f"this email item was flagged as possibly not a real document: {note.strip()}"
            if isinstance(note, str) and note.strip()
            else "this email item was flagged as possibly not a real document"
        )
        findings.append(Finding("email_item_ambiguous", None, "warn", message))

    return findings


def derive_review_status(findings: list[Finding]) -> ReviewStatus:
    """Any finding => needs_review; otherwise unreviewed (never auto-verified)."""
    return ReviewStatus.NEEDS_REVIEW if findings else ReviewStatus.UNREVIEWED


def findings_to_payload(findings: list[Finding]) -> list[dict[str, Any]]:
    """Serialise findings for storage in ``extra["validation"]``."""
    return [asdict(f) for f in findings]
