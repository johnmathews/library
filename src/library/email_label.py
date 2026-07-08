"""Optional per-email LLM label pass for forwarded-mail attachment selection.

The deterministic gate in :mod:`library.email_ingest` filters *unambiguous* noise
(inline logos, tiny pixels, calendar/vCard parts). This module handles the
ambiguous middle: one Anthropic call per email classifies each *surviving*
attachment as ``keep`` or ``probably_noise``, given the subject, sender, and a
body excerpt for context. The body itself is judged separately by the substance
gate, so it is only context here.

Two invariants, both honouring "never lose a real document":

1. **A ``probably_noise`` verdict never drops anything.** The caller ingests the
   attachment anyway and merely flags it ``needs_review`` (via
   ``Document.extra["email_selection"]`` → the ``email_item_ambiguous`` validation
   finding). A false positive costs one review click, not a lost document.
2. **Fail-open.** Disabled feature, blown budget, an API error, or a malformed /
   incomplete response all keep every attachment. The labeller can only *add* a
   flag, never remove or reject an item.

Spend is budget-gated exactly like extraction: today's ``email_label_completed``
event totals are summed and compared to ``email_label_daily_budget_usd`` before
each call. The completion event is written by the caller (it needs a document to
hang on); this module only reads the running total. See docs/ingestion.md,
"Email item selection".
"""

import logging
from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import todays_spend_usd
from library.extraction.extractor import estimate_cost_usd

logger = logging.getLogger(__name__)

#: Bump when the prompt or schema changes so stored events stay interpretable.
PROMPT_VERSION = "email-label-v1"

#: The completion event whose daily total gates this pass (written by the caller).
LABEL_EVENT = "email_label_completed"

_MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = """You triage the attachments of a forwarded email for a personal \
document library. You are given the email's subject, sender, a short body excerpt, \
and a numbered list of attachments (filename, MIME type, size in bytes) — judged \
BEFORE any OCR, so you have only this metadata and context.

For each attachment, return a verdict:
- "keep": a real document worth filing (an invoice, receipt, statement, letter, \
scanned form, photo of a document, etc.).
- "probably_noise": almost certainly not a filed document — a signature logo or \
banner, a social-media icon, a decorative or boilerplate image, a small embedded \
graphic referenced by the message body.

Rules:
- When in doubt, choose "keep". A wrong "probably_noise" only flags a real \
document for human review; it never deletes anything, but it should still be rare.
- Judge by filename, type, size, and how the body talks about the attachments \
(e.g. a body that says "see attached invoice" strongly implies the PDF is a keep).
- Give a short reason only for "probably_noise"; "keep" needs no reason.
- Return exactly one entry per attachment index provided, and no others."""


class ItemLabel(BaseModel):
    """The verdict for one attachment, keyed by its manifest ``index``."""

    model_config = ConfigDict(extra="forbid")

    index: int
    verdict: Literal["keep", "probably_noise"]
    reason: str | None = None


class EmailLabelResult(BaseModel):
    """The structured output: one :class:`ItemLabel` per attachment judged."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemLabel]


@dataclass(frozen=True, slots=True)
class LabelItem:
    """One attachment presented to the labeller (pre-OCR metadata only)."""

    index: int
    filename: str | None
    mime: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class LabelUsage:
    """Token usage and estimated cost of one label call, for the budget event."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    prompt_version: str
    item_count: int

    def as_detail(self) -> dict[str, object]:
        """JSON detail for the ``email_label_completed`` event."""
        return {
            "cost_usd": self.cost_usd,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "prompt_version": self.prompt_version,
            "items": self.item_count,
        }


@dataclass(frozen=True, slots=True)
class LabelOutcome:
    """Result of a label pass: per-index verdicts, usage, and any skip reason.

    ``verdicts`` maps an attachment index to ``(verdict, reason)``. It is empty
    whenever the pass was skipped or its response could not be trusted — the
    caller then keeps every attachment (fail-open). ``usage`` is present only
    when a call actually billed, so the caller records the budget event.
    """

    verdicts: dict[int, tuple[str, str | None]]
    usage: LabelUsage | None
    skip_reason: str | None  # "budget" | "error" | None


def _manifest(items: list[LabelItem]) -> str:
    return "\n".join(
        f"[{item.index}] filename={item.filename!r} type={item.mime} bytes={item.size}"
        for item in items
    )


async def label_email_items(
    session: AsyncSession,
    client: AsyncAnthropic,
    settings: Settings,
    *,
    subject: str | None,
    sender: str | None,
    body_snippet: str,
    items: list[LabelItem],
) -> LabelOutcome:
    """Label each attachment ``keep``/``probably_noise`` — fail-open on any problem.

    Reads today's label spend and skips (keeping all) when the budget is reached.
    A response that does not cover exactly the requested indices is discarded
    (verdicts cleared) but its cost is still reported, so budget accounting stays
    honest even when the model misbehaves.

    The **entire** body is guarded: the budget read, the API call, and the cost
    estimate can all raise, and any of them doing so must keep every attachment,
    never propagate. This function is called before the ingest loop, so a raised
    exception here would otherwise abort the whole message and leave real
    attachments un-ingested — exactly what "the labeller only adds a flag" forbids.
    """
    if not items:
        return LabelOutcome({}, None, None)

    try:
        spend = await todays_spend_usd(session, LABEL_EVENT)
        if spend >= settings.email_label_daily_budget_usd:
            logger.info(
                "email-label: daily budget $%.2f reached ($%.4f spent); keeping all attachments",
                settings.email_label_daily_budget_usd,
                spend,
            )
            return LabelOutcome({}, None, "budget")

        user_content = (
            f"Subject: {subject or '(none)'}\n"
            f"From: {sender or '(unknown)'}\n"
            f"Body excerpt:\n{body_snippet or '(no body)'}\n\n"
            f"Attachments to judge:\n{_manifest(items)}"
        )
        response = await client.messages.parse(
            model=settings.email_label_model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_format=EmailLabelResult,
        )
        parsed = response.parsed_output
        if parsed is None:
            logger.warning("email-label: no parseable output; keeping all attachments")
            return LabelOutcome({}, None, "error")

        usage = LabelUsage(
            model=settings.email_label_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=estimate_cost_usd(
                settings.email_label_model,
                response.usage.input_tokens,
                response.usage.output_tokens,
            ),
            prompt_version=PROMPT_VERSION,
            item_count=len(items),
        )

        wanted = {item.index for item in items}
        verdicts = {label.index: (label.verdict, label.reason) for label in parsed.items}
        if set(verdicts) != wanted:
            # A mismatched index set is untrustworthy — keep everything, but the
            # call still cost money, so return the usage for the budget event.
            logger.warning(
                "email-label: verdict indices %s do not match items %s; keeping all",
                sorted(verdicts),
                sorted(wanted),
            )
            return LabelOutcome({}, usage, "error")

        return LabelOutcome(verdicts, usage, None)
    except Exception:
        logger.exception("email-label: labelling failed; keeping all attachments")
        return LabelOutcome({}, None, "error")
