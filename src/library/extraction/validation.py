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

# Below this many non-whitespace characters an image's OCR text is considered
# near-empty (a logo/decoration, not a document). Mirrors MIN_TEXT_CHARS in
# library.extraction.extractor — defined locally because this module must stay
# pure (stdlib + models only) and extractor.py pulls in the SDK/config/storage.
_DECORATION_IMAGE_MAX_CHARS: int = 20

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
# Bilingual (Dutch/English) CATEGORY words that are not merchant names. A sender
# resolved to one of these (classically a till receipt where the model fell back
# to "Restaurant" because the printed name was hard to read) is effectively
# useless for finding the document later. Matched full-string, case-insensitive,
# NEVER substring: "Garage Spaarndam" is a real merchant name and must not fire.
_GENERIC_SENDER_NAMES: frozenset[str] = frozenset(
    {
        "restaurant",
        "café",
        "cafe",
        "bar",
        "hotel",
        "shop",
        "store",
        "winkel",
        "supermarkt",
        "supermarket",
        "bakkerij",
        "bakery",
        "garage",
        "tankstation",
        "apotheek",
        "pharmacy",
        "ziekenhuis",
        "hospital",
        "gemeente",
        "webshop",
        "market",
        "markt",
    }
)
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


def email_findings(extra: dict[str, Any] | None) -> list[Finding]:
    """The email-channel rules, computable from ``Document.extra`` alone.

    Pure over the hints the email channel stamps at ingest
    (``email_siblings_dropped``, ``email_selection``) — no other document
    field is read. That independence is the point: the email channel runs
    these at document creation (``library.ingest.ingest_file``) so a flagged
    email item is ``needs_review`` immediately, even when extraction is
    disabled or has not run yet; :func:`validate` reuses them verbatim so an
    extraction pass later recomputes the same findings.
    """
    extra = extra if isinstance(extra, dict) else {}
    findings: list[Finding] = []

    # email_attachments_dropped — this document came from an email whose *other*
    # attachments could not be added (stamped on extra at ingest by email_ingest,
    # only-when-non-empty), so the reviewer knows files are missing from the set.
    dropped = extra.get("email_siblings_dropped")
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
    # tagged as "probably_noise" (stamped on extra["email_selection"] by the
    # selection unit; that is the only verdict it ever writes for a flagged
    # item), so the reviewer confirms it is a real document.
    sel = extra.get("email_selection")
    if isinstance(sel, dict) and sel.get("verdict") == "probably_noise":
        note = sel.get("reason")
        message = (
            f"this email item was flagged as possibly not a real document: {note.strip()}"
            if isinstance(note, str) and note.strip()
            else "this email item was flagged as possibly not a real document"
        )
        findings.append(Finding("email_item_ambiguous", None, "warn", message))

    return findings


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
    sender_name: str | None,
    ocr_floor: float,
    today: date,
) -> list[Finding]:
    """Run every rule against a document; return the findings that fired."""
    findings: list[Finding] = []
    text = document.ocr_text or ""
    extraction = document.extra.get("extraction") if isinstance(document.extra, dict) else None
    extraction = extraction if isinstance(extraction, dict) else {}

    # amount_grounding — amount set but its digits are absent from the text.
    # Only meaningful when the model actually read the OCR text. When extraction
    # ran on the page IMAGE instead (input_mode "document"/"image" — the vision
    # fallback or the born-unusable-OCR path), the amount was grounded in the
    # image, not the OCR text, so its absence from thin OCR is expected, not a
    # review concern. Absent/legacy input_mode predates the image paths and was
    # always text, so it keeps the check.
    read_the_image = extraction.get("input_mode") in ("document", "image")
    if (
        document.amount_total is not None
        and text
        and not read_the_image
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

    # generic_sender — the sender resolved to a bare category word ("Restaurant",
    # "Winkel", …), not a merchant/organisation name. Classically a scanned till
    # receipt where the printed shop name was hard to read and the model fell
    # back to the venue type. Full-string match only: a real name that contains
    # a category word ("Garage Spaarndam") must not fire. A null sender is
    # missing_sender's job, not this rule's. The caller resolves the name from
    # the document's sender relationship and threads it in as ``sender_name``.
    if sender_name is not None and sender_name.strip().casefold() in _GENERIC_SENDER_NAMES:
        findings.append(
            Finding(
                "generic_sender",
                "sender",
                "warn",
                f'the sender "{sender_name.strip()}" is a generic category word, '
                "not the merchant or organisation name printed on the document",
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

    # missing_amount — the text carries a payment/due term ("te betalen", "due by",
    # "vervaldatum", …) but no amount was extracted. The classic thin-OCR image-PDF
    # miss: the letterhead's payment line was OCR'd while the body's total was not.
    # A safety net for the confident-but-wrong case the vision escalation (which
    # only fires on low confidence) does not catch. Scoped to monetary kinds so the
    # Dutch homograph "vervaldatum" (expiry on a passport/ID/warranty, not a due
    # date) does not spuriously flag those — same guard as due_expiry_grounding.
    if (
        document.amount_total is None
        and kind_slug in _MONETARY_KINDS
        and text
        and _DUE_CUES.search(text)
    ):
        findings.append(
            Finding(
                "missing_amount",
                "amount_total",
                "warn",
                "the document mentions payment or a due date but no amount was extracted",
            )
        )

    # missing_date — a monetary document with an amount but no document_date: a
    # bill or receipt always carries a printed date. The date analogue of
    # missing_amount for the confident-but-wrong thin-OCR case (the total was
    # read but the date line was not). Scoped to monetary kinds so undated
    # reference material quoting a price is not nagged about.
    if (
        document.amount_total is not None
        and document.document_date is None
        and kind_slug in _MONETARY_KINDS
    ):
        findings.append(
            Finding(
                "missing_date",
                "document_date",
                "warn",
                "this looks like a bill or receipt but no document date was extracted",
            )
        )

    # ocr_confidence_gate — extraction built on low-confidence OCR. Moot when the
    # model read the page IMAGE instead (input_mode document/image — the vision
    # fallback or born-unusable-OCR path): the accepted extraction did not consume
    # the OCR text, so its confidence says nothing about the result. Same
    # image-vs-text gate as amount_grounding above.
    if (
        document.ocr_confidence is not None
        and document.ocr_confidence < ocr_floor
        and not read_the_image
    ):
        findings.append(
            Finding(
                "ocr_confidence_gate",
                None,
                "warn",
                f"OCR confidence {document.ocr_confidence:.0f} below floor {ocr_floor:.0f}",
            )
        )

    # decoration_image — an image document whose OCR yielded almost no text
    # AND whose extraction grounded nothing. Thin OCR alone is not enough:
    # below MIN_TEXT_CHARS the extractor reads the image itself (vision
    # fallback), which rescues real photographed receipts — those come back
    # with an amount, date, or sender and must stay quiet here. A logo yields
    # none of the groundable fields (titles/summaries don't count: the model
    # happily invents "Company X logo"), so requiring all three empty keeps
    # the rule pointed at decorations that slipped past ingest filtering
    # instead of hiding them behind generic empty_extraction/missing_* reasons.
    if (
        (document.mime_type or "").startswith("image/")
        and len(re.sub(r"\s", "", text)) < _DECORATION_IMAGE_MAX_CHARS
        and document.amount_total is None
        and document.document_date is None
        and document.sender_id is None
    ):
        findings.append(
            Finding(
                "decoration_image",
                None,
                "warn",
                "this image produced almost no text and is likely a logo or "
                "decoration rather than a real document",
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

    # Email-channel rules (email_attachments_dropped, email_item_ambiguous) —
    # they read only Document.extra, so they live in the pure email_findings
    # helper the email channel also runs at ingest (before any extraction).
    findings.extend(email_findings(document.extra if isinstance(document.extra, dict) else None))

    return findings


def derive_review_status(findings: list[Finding]) -> ReviewStatus:
    """Any finding => needs_review; otherwise unreviewed (never auto-verified)."""
    return ReviewStatus.NEEDS_REVIEW if findings else ReviewStatus.UNREVIEWED


def findings_to_payload(findings: list[Finding]) -> list[dict[str, Any]]:
    """Serialise findings for storage in ``extra["validation"]``."""
    return [asdict(f) for f in findings]
