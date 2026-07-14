"""Optional per-email LLM label pass for email item selection.

The deterministic gate in :mod:`library.email_ingest` filters *unambiguous* noise
(inline logos, tiny pixels, calendar/vCard parts). This module handles the
ambiguous middle: one Anthropic call per polled email classifies each *surviving*
item — the attachments plus, when present, the message body — as ``keep`` or
``probably_noise``, given the subject, sender, and a body excerpt for context.
The same call also renders a whole-email verdict: ``file`` (proceed with normal
ingestion) or ``hold`` (route the entire email to a human review queue).

Two invariants, both honouring "never lose a real document":

1. **A ``probably_noise`` verdict never drops anything.** The caller ingests the
   item anyway and merely flags it ``needs_review`` (via
   ``Document.extra["email_selection"]`` → the ``email_item_ambiguous`` validation
   finding). A false positive costs one review click, not a lost document.
   Likewise a ``hold`` never deletes: the held email waits for a human.
2. **Fail-open.** Disabled feature, blown budget, an API error, or a malformed /
   incomplete response all keep every item AND force ``email_verdict="file"``.
   The labeller can only *add* a flag or route to review — a failure can never
   hold an email or reject an item.

Spend is budget-gated exactly like extraction: before each call, today's spend
is summed and compared to ``email_label_daily_budget_usd``. The sum has two
parts, because a billed call lands in one of two places: emails that *filed*
record an ``email_label_completed`` event (written by the caller, anchored on
the first document the email produced), while emails that were *held* produced
no document, so their billing rides in the held row's
``held_emails.trace["label_usage"]`` instead. The gate adds both
(:func:`todays_spend_usd` + :func:`_todays_held_label_spend_usd`) — otherwise a
stream of held newsletters would be invisible to the cap and could run the
pass indefinitely. This module only reads the running totals. See
docs/ingestion.md, "Email item selection".
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Numeric, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.apply import todays_spend_usd
from library.extraction.extractor import estimate_cost_usd
from library.models import HeldEmail

logger = logging.getLogger(__name__)

#: Bump when the prompt or schema changes so stored events stay interpretable.
PROMPT_VERSION = "email-label-v2"

#: The completion event whose daily total gates this pass (written by the caller).
LABEL_EVENT = "email_label_completed"

_MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = """You triage an email for a personal document library. You are \
given the email's subject, sender, a short body excerpt, and a numbered list of \
items — the attachments and, when listed, the message body itself (kind, filename, \
MIME type, size in bytes) — judged BEFORE any OCR, so you have only this metadata \
and context.

You have two tasks.

Task 1 — for each item, return a verdict:
- "keep": a real document worth filing (an invoice, receipt, statement, letter, \
scanned form, photo of a document, a substantive message body, etc.).
- "probably_noise": almost certainly not a filed document — a signature logo or \
banner, a social-media icon, a decorative or boilerplate image, a small embedded \
graphic referenced by the message body, a body that is only a cover note.

Task 2 — for the email as a whole, return "email_verdict":
- "file": any part of this email plausibly belongs in a personal document library.
- "hold": the ENTIRE email is almost certainly not library material — a \
newsletter, a marketing blast, an automated notification, or clearly misdirected \
mail. Held emails go to a human review queue; nothing is deleted.

Rules:
- When in doubt, choose "keep" and "file". A wrong "probably_noise" or "hold" \
only sends a real document to human review; it never deletes anything, but it \
should still be rare.
- Judge by kind, filename, type, size, and how the body talks about the items \
(e.g. a body that says "see attached invoice" strongly implies the PDF is a keep).
- Give a short reason only for "probably_noise"; "keep" needs no reason. A short \
"email_reason" is required for "hold".
- Return exactly one entry per item index provided — including the body item, \
when one is listed — and no others."""


class ItemLabel(BaseModel):
    """The verdict for one item (attachment or body), keyed by its manifest ``index``."""

    model_config = ConfigDict(extra="forbid")

    index: int
    verdict: Literal["keep", "probably_noise"]
    reason: str | None = None


class EmailLabelResult(BaseModel):
    """The structured output: per-item labels plus a whole-email verdict.

    ``email_verdict`` defaults to ``file`` so a response that omits it can only
    proceed with normal ingestion, never hold — fail-open at the schema level.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ItemLabel]
    email_verdict: Literal["file", "hold"] = "file"
    email_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LabelItem:
    """One item presented to the labeller (pre-OCR metadata only).

    ``kind`` is ``"attachment"`` or ``"body"`` — the message body is judged as
    an item of its own, not just context.
    """

    index: int
    filename: str | None
    mime: str | None
    size: int | None
    kind: str = "attachment"


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
    """Result of a label pass: per-index verdicts, whole-email verdict, usage.

    ``verdicts`` maps an item index to ``(verdict, reason)``. It is empty
    whenever the pass was skipped or its response could not be trusted — the
    caller then keeps every item (fail-open). ``email_verdict`` is ``"hold"``
    only for a trusted response that said so; every skip and error path leaves
    it at ``"file"``, so a failure can never hold an email. ``usage`` is present
    only when a call actually billed, so the caller records the budget event.
    """

    verdicts: dict[int, tuple[str, str | None]]
    usage: LabelUsage | None
    skip_reason: str | None  # "budget" | "error" | None
    email_verdict: str = "file"  # "file" | "hold"
    email_reason: str | None = None


async def _todays_held_label_spend_usd(session: AsyncSession) -> float:
    """Sum today's (UTC) label spend recorded on held emails.

    A held email produced no document, so its billed label call has no
    ``email_label_completed`` event to anchor on — the usage lives in the held
    row's ``trace["label_usage"]`` instead (``email_ingest._hold_message``).
    Mirrors :func:`library.extraction.apply.todays_spend_usd` exactly: "today"
    is UTC midnight onwards (``created_at >= start_of_day``), and only rows
    whose trace actually carries a ``label_usage.cost_usd`` key count, so the
    two sums cover the same window without double- or under-counting.
    """
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.coalesce(func.sum(HeldEmail.trace["label_usage"]["cost_usd"].astext.cast(Numeric)), 0)
    ).where(
        HeldEmail.trace["label_usage"].has_key("cost_usd"),
        HeldEmail.created_at >= start_of_day,
    )
    return float((await session.execute(statement)).scalar_one())


def _manifest(items: list[LabelItem]) -> str:
    return "\n".join(
        f"[{item.index}] kind={item.kind} filename={item.filename!r} "
        f"type={item.mime} bytes={item.size}"
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
    """Label each item ``keep``/``probably_noise`` and the email ``file``/``hold``.

    Fail-open on any problem: every skip and error path keeps all items AND
    returns ``email_verdict="file"``. Reads today's label spend — the
    ``email_label_completed`` event total for filed emails *plus* the
    ``trace["label_usage"]`` total for held ones — and skips when the budget is
    reached. A response that does not cover exactly the requested
    indices is discarded entirely — verdicts cleared and the email forced to
    ``file`` (an untrustworthy response must not hold) — but its cost is still
    reported, so budget accounting stays honest even when the model misbehaves.

    The **entire** body is guarded: the budget read, the API call, and the cost
    estimate can all raise, and any of them doing so must keep every item, never
    propagate. This function is called before the ingest loop, so a raised
    exception here would otherwise abort the whole message and leave real
    attachments un-ingested — exactly what "the labeller only adds a flag" forbids.
    """
    if not items:
        return LabelOutcome({}, None, None)

    try:
        spend = await todays_spend_usd(session, LABEL_EVENT) + await _todays_held_label_spend_usd(
            session
        )
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
            f"Items to judge:\n{_manifest(items)}"
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
            # A mismatched index set is untrustworthy — keep everything and force
            # the email to "file" (an untrustworthy response must not hold), but
            # the call still cost money, so return the usage for the budget event.
            logger.warning(
                "email-label: verdict indices %s do not match items %s; keeping all",
                sorted(verdicts),
                sorted(wanted),
            )
            return LabelOutcome({}, usage, "error")

        return LabelOutcome(
            verdicts,
            usage,
            None,
            email_verdict=parsed.email_verdict,
            email_reason=parsed.email_reason,
        )
    except Exception:
        logger.exception("email-label: labelling failed; keeping all attachments")
        return LabelOutcome({}, None, "error")
