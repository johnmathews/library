"""Call Claude to extract document metadata via structured outputs.

``extract()`` is pure orchestration around the Anthropic SDK: build input
content (OCR text; the original file when OCR produced nothing usable, or
when a scanned document's text is too thin per page to trust — the density
trigger), call ``client.messages.parse()`` on the primary model, and escalate
once to the bigger model on low confidence or a parse/validation failure. API
errors propagate (the SDK already retried 429/5xx); the caller decides what
a failure means for the document.
"""

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic

from library.config import Settings
from library.extraction.pricing import MODEL_PRICING_USD_PER_MTOK
from library.extraction.schema import KIND_SLUGS, ExtractedMetadata
from library.models import Document
from library.storage import derived_dir, path_for

logger = logging.getLogger(__name__)

# Bump whenever the system prompt or schema changes meaningfully; stored per
# run so old-prompt documents can be found and re-extracted later.
PROMPT_VERSION: str = "2026-07-14.1"

# Short OCR text (<= this many characters) is sent whole: metadata lives on
# the first pages, and this caps per-document spend for transactional docs.
MAX_TEXT_CHARS: int = 8_000
# Total budget for the sampled representation of a long document: general
# reference material (manuals, papers, notes) carries useful signal
# throughout, so longer text is window-sampled up to this many characters.
MAX_TEXT_CHARS_LONG: int = 24_000
# Number of evenly-spaced windows taken across a long document.
_SAMPLE_WINDOWS: int = 6
# Below this many stripped characters the OCR text is considered garbage and
# the original file is sent instead (when it is a PDF or image).
MIN_TEXT_CHARS: int = 20
# Largest original we are willing to base64 into a request.
MAX_FILE_BYTES: int = 5 * 1024 * 1024

MAX_OUTPUT_TOKENS: int = 2_048

SYSTEM_PROMPT: str = f"""\
You extract metadata for "Library", a self-hosted family document archive.
The archive is a mixed collection in Dutch, English, or a mix, spanning two
broad groups: (1) transactional paperwork — invoices, receipts, certificates,
utility bills, parking tickets, warranties, letters, contracts, tickets — and
(2) general reference material — manuals, reference documents, research
papers, and notes. General-reference items are often long and multi-topic, so
their text may reach you sampled (windows of the document joined by "[...]")
rather than in full. Input is OCR text (possibly noisy) or the original
document/image.

Rules:
- Write ALL free-text output fields (title, summary, reasoning_note) in
  English, even when the document itself is in Dutch or another language.
  Translate as needed; keep proper nouns (names, brands) as-is.
- kind_slug: one of {", ".join(KIND_SLUGS)}. Disambiguate the common pairs:
  invoice = a request for payment (amount owed, usually a due date) vs receipt =
  proof a payment already happened; utility-bill = a recurring energy/water/
  telecom/municipal charge (prefer it over invoice for utilities); letter =
  personal/official correspondence with no binding terms vs contract = an
  agreement both parties are bound by; certificate = attests a fact/
  qualification vs warranty = a guarantee tied to a purchase. Use "other" only
  when nothing else fits.
- title: short and descriptive, in English.
- summary: in English, and adaptive to the document. For transactional
  paperwork keep it to at most two sentences. For general reference material
  (manuals, reference docs, research papers, notes) write 3-6 sentences
  capturing what it covers.
- sender_name: the organisation or person that ISSUED the document, in a short
  canonical form (e.g. "Eneco", not "Eneco Services B.V., afdeling facturatie").
  For a letter or email the SIGNER is the sender: a sign-off such as "Met
  vriendelijke groet, Y" / "Best regards, Y" / "Hoogachtend, Y" means Y (or Y's
  organisation) is the sender, NOT the recipient. Prefer the issuing
  organisation when both a person and a company sign. It must be the name
  PRINTED on the document (on a till receipt: the shop name in the header),
  never a generic category word such as "Restaurant", "Shop", or "Winkel" —
  a category is not a name. If no name is legible anywhere, return null
  rather than a category word. null when genuinely unclear.
- recipient_name: the person the document is addressed TO, in short canonical
  form (e.g. "John"). The SALUTATION is the strongest signal: "Dear John" /
  "Beste John" / "Geachte heer Mathews" / "T.a.v. John Mathews" -> the recipient
  is that person. A letter, invoice, or personally-addressed notice almost
  always HAS a recipient even if only a first name — extract it. Never confuse
  the salutation (recipient) with the sign-off (sender). null only for truly
  impersonal material such as a manual or generic reference document.
- addressee_raw: the verbatim salutation/addressee text, copied exactly as
  printed (e.g. "Dhr. J. de Vries" after "T.a.v."), before any shortening. null
  when there is no salutation or addressee block.
- signer_raw: the verbatim sign-off name, copied exactly as printed (e.g. the
  line after "Met vriendelijke groet,"). null when the document is not signed.
- Dates are ISO YYYY-MM-DD strings; null when absent.
  - document_date: the document's own issue/print date.
  - due_date: a date by which the recipient must ACT — pay, respond, renew, or
    return ("te betalen voor", "uiterste betaaldatum", "vervaldatum" on an
    invoice, "pay by", "due by"). An obligation deadline.
  - expiry_date: a date after which the document/entitlement is NO LONGER VALID
    (passports, warranties, insurance, contracts: "geldig tot", "vervalt op",
    "verloopt", "valid until", "expires"). A validity horizon, not a payment
    deadline. Dutch note: "vervaldatum" on an invoice is a due_date, but
    "vervalt"/"verloopt" on a pass or policy is an expiry_date. Never copy the
    same date into both fields — choose the one the surrounding words match; a
    document may have neither, one, or both.
- amount_total: the document's main total as a plain decimal string, e.g.
  "1234.56", with currency as an ISO 4217 code, e.g. "EUR". Both null when
  not applicable.
- tags: at most 8 lowercase slug tags ("hyphenated-like-this"), useful for
  finding the document later. No duplicates of kind or sender.
- topics: up to 12 short, human-readable topic phrases for general reference
  material, capturing the subjects it covers. Leave empty ([]) for
  transactional paperwork.
- language: nld, eng, mixed, or unknown — the language of the document.
- A missing sender, date, or amount is entirely normal for general reference
  material — do not guess values, leave them null, and treat their absence as
  expected, NOT as a low-confidence signal.
- confidence: "low" when the text is garbled or you had to guess key fields,
  "high" only when the document is clear.
- reasoning_note: one short line when something needed judgement; else null.
"""

# Claude-accepted image media types we can send as-is.
_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})

# Joins the sampled windows of a long document; the marker makes the gaps
# explicit to the model so it does not read across a discontinuity.
_SAMPLE_SEPARATOR: str = "\n\n[...]\n\n"


def _sample_long_text(text: str) -> str:
    """Represent a long document by evenly-spaced windows of its text.

    Short text (<= ``MAX_TEXT_CHARS``) is returned unchanged. Longer text is
    sampled into ``_SAMPLE_WINDOWS`` windows of ``MAX_TEXT_CHARS_LONG //
    _SAMPLE_WINDOWS`` characters each — the first starting at the very
    beginning and the last ending at the very end — joined by
    ``_SAMPLE_SEPARATOR`` so head, tail, and the middle all reach the model
    while spend stays bounded by ``MAX_TEXT_CHARS_LONG``.
    """
    if len(text) <= MAX_TEXT_CHARS:
        return text
    window = MAX_TEXT_CHARS_LONG // _SAMPLE_WINDOWS
    last_start = len(text) - window
    starts = [round(i * last_start / (_SAMPLE_WINDOWS - 1)) for i in range(_SAMPLE_WINDOWS)]
    windows = [text[start : start + window] for start in starts]
    return _SAMPLE_SEPARATOR.join(windows)[:MAX_TEXT_CHARS_LONG]


class ExtractionSkipped(Exception):
    """Extraction cannot run for this document; skip it gracefully."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


class ExtractionParseError(Exception):
    """The model returned no parseable structured output."""


@dataclass(frozen=True)
class CallUsage:
    """Token usage and estimated cost of one API call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ExtractionOutcome:
    """A successful extraction plus its provenance and cost."""

    metadata: ExtractedMetadata
    model: str
    prompt_version: str
    input_mode: str  # "text" | "document" | "image"
    escalated: bool
    calls: list[CallUsage] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    @property
    def cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.calls)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost of one call from the static pricing table.

    Raises :class:`KeyError` for a model with no pricing row — silently
    returning 0 would zero the recorded cost *and* the daily-spend budget
    gate. Configured ``*_model`` knobs are validated against the table at
    startup (see :class:`library.config.Settings`), so this only fires for a
    model passed programmatically that was never priced.
    """
    if model not in MODEL_PRICING_USD_PER_MTOK:
        raise KeyError(f"no pricing row for model {model!r}; add it to MODEL_PRICING_USD_PER_MTOK")
    input_price, output_price = MODEL_PRICING_USD_PER_MTOK[model]
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


def _email_context_block(recipient_hint: str | None) -> dict[str, Any] | None:
    """A one-line envelope hint for an email attachment, or None.

    The document's own salutation always wins; this only helps the model when
    the document names no recipient. Takes a resolved display name, never a raw
    email address, so no address leaks into the prompt.
    """
    if not recipient_hint:
        return None
    return {
        "type": "text",
        "text": (
            f"Context: this document arrived as an attachment to an email "
            f"addressed to {recipient_hint}. A recipient named in the document "
            f"itself always takes priority; use this only if the document names "
            f"no recipient."
        ),
    }


def build_user_content(
    document: Document,
    ocr_text: str,
    *,
    recipient_hint: str | None = None,
    force_file: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Build the user message content and report the input mode used.

    Prefers truncated OCR text; falls back to sending the original file as a
    base64 ``document``/``image`` block when the text is empty or garbage.
    Raises :class:`ExtractionSkipped` when neither input is usable. When
    ``recipient_hint`` is given (a resolved display name for an email
    attachment), a one-line envelope hint is appended to the content.

    ``force_file=True`` skips the OCR-text branch and sends the original file
    even when usable text exists — used by the low-confidence escalation to let
    the model *read* an image PDF whose OCR captured only the letterhead. It
    still raises :class:`ExtractionSkipped` when the original cannot be sent
    (unusable mime, missing, or oversized), so the caller can fall back to text.
    """
    hint = _email_context_block(recipient_hint)
    text = ocr_text.strip()
    if not force_file and len(text) >= MIN_TEXT_CHARS:
        content: list[dict[str, Any]] = [{"type": "text", "text": _sample_long_text(text)}]
        if hint is not None:
            content.append(hint)
        return content, "text"

    mime = document.mime_type
    if mime == "application/pdf":
        path = path_for(document.sha256)
        media_type = "application/pdf"
        block_type = "document"
    elif mime in _IMAGE_MEDIA_TYPES:
        path = path_for(document.sha256)
        media_type = mime
        block_type = "image"
    elif mime in ("image/heic", "image/heif"):
        # Claude does not accept HEIC; the ingest step wrote a JPEG conversion.
        path = derived_dir(document.sha256) / "converted.jpg"
        media_type = "image/jpeg"
        block_type = "image"
    else:
        raise ExtractionSkipped(
            "input_unusable", f"no usable OCR text and mime {mime} cannot be sent directly"
        )

    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ExtractionSkipped("input_unusable", f"original artifact missing: {path}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ExtractionSkipped(
            "file_too_large", f"{len(raw)} bytes exceeds the {MAX_FILE_BYTES} byte direct-input cap"
        )

    block: dict[str, Any] = {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(raw).decode("ascii"),
        },
    }
    content = [
        block,
        {"type": "text", "text": "Extract the metadata for this document."},
    ]
    if hint is not None:
        content.append(hint)
    return content, block_type


def _thin_scan_prefers_vision(document: Document, text: str, min_chars_per_page: int) -> bool:
    """True when a scanned document's OCR text is too thin to trust as input.

    Density (chars per page) is an *input-based* vision trigger: garbled
    tesseract output on a till receipt can come back confidently wrong on
    text, so the low-confidence escalation alone never fires (prod doc 150).
    Only documents where OCR actually ran are eligible — born-digital
    text-layer documents have ``ocr_confidence`` NULL and their short-but-clean
    text extracts fine. A non-positive threshold disables the trigger, and the
    ``MIN_TEXT_CHARS`` empty/garbage floor keeps its own (unconditional)
    file-fallback path in :func:`build_user_content`.
    """
    if min_chars_per_page <= 0 or document.ocr_confidence is None:
        return False
    if len(text) < MIN_TEXT_CHARS:
        return False  # empty/garbage: build_user_content falls back on its own
    return len(text) / max(document.page_count or 1, 1) < min_chars_per_page


async def _attempt(
    client: AsyncAnthropic, model: str, content: list[dict[str, Any]]
) -> tuple[ExtractedMetadata, CallUsage]:
    """One ``messages.parse`` call; raises on parse/validation failure."""
    response = await client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractedMetadata,
    )
    usage = CallUsage(
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cost_usd=estimate_cost_usd(
            model, response.usage.input_tokens, response.usage.output_tokens
        ),
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ExtractionParseError(f"{model} returned no parseable output")
    return parsed, usage


async def extract(
    document: Document,
    ocr_text: str,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    recipient_hint: str | None = None,
) -> ExtractionOutcome:
    """Extract metadata for one document, escalating once when warranted.

    Escalates to ``settings.extraction_escalation_model`` when the primary
    model reports low confidence or fails to produce valid structured output
    (``ValidationError``/JSON errors are ``ValueError`` subclasses). API
    errors propagate to the caller. ``recipient_hint`` is an optional resolved
    display name (email-attachment envelope) appended to the prompt as context.
    """
    # Density-based vision trigger: a thin scan (OCR ran, few chars per page)
    # sends the original file on the PRIMARY call. When the file cannot be
    # sent (unusable mime / missing artifact / oversized), fall back to the
    # normal text path — the trigger must never fail a document that extracts
    # today.
    prefer_vision = _thin_scan_prefers_vision(
        document, ocr_text.strip(), settings.extraction_vision_min_chars_per_page
    )
    try:
        content, input_mode = build_user_content(
            document, ocr_text, recipient_hint=recipient_hint, force_file=prefer_vision
        )
    except ExtractionSkipped:
        if not prefer_vision:
            raise
        logger.info(
            "document %s: thin-scan vision trigger fired but the original cannot "
            "be sent; falling back to text input",
            document.id,
        )
        content, input_mode = build_user_content(document, ocr_text, recipient_hint=recipient_hint)
    calls: list[CallUsage] = []

    metadata: ExtractedMetadata | None = None
    try:
        metadata, usage = await _attempt(client, settings.extraction_model, content)
        calls.append(usage)
    except (ExtractionParseError, ValueError) as exc:
        logger.warning(
            "document %s: %s failed structured parse (%s); escalating",
            document.id,
            settings.extraction_model,
            exc,
        )

    if metadata is not None and metadata.confidence != "low":
        return ExtractionOutcome(
            metadata=metadata,
            model=settings.extraction_model,
            prompt_version=PROMPT_VERSION,
            input_mode=input_mode,
            escalated=False,
            calls=calls,
        )

    # Escalation. When the primary ran on OCR text, prefer re-attempting with the
    # ORIGINAL FILE (vision): a low-confidence result on an image PDF is usually
    # thin OCR — only the letterhead reached the model — and the model can read
    # the amount/recipient/date straight off the page. Fall back to the same text
    # content when the original cannot be sent (unusable mime / missing /
    # oversized), so escalation never regresses.
    escalation_content, escalation_mode = content, input_mode
    if input_mode == "text":
        try:
            escalation_content, escalation_mode = build_user_content(
                document, ocr_text, recipient_hint=recipient_hint, force_file=True
            )
        except ExtractionSkipped:
            escalation_content, escalation_mode = content, input_mode

    metadata, usage = await _attempt(
        client, settings.extraction_escalation_model, escalation_content
    )
    calls.append(usage)
    return ExtractionOutcome(
        metadata=metadata,
        model=settings.extraction_escalation_model,
        prompt_version=PROMPT_VERSION,
        input_mode=escalation_mode,
        escalated=True,
        calls=calls,
    )
