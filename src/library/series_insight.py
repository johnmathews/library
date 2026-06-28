"""Precompute and cache an LLM prose description for a recurring series.

The series *statistics* (``library.series``) are cheap and computed on the fly,
but the one- or two-sentence natural-language summary that fronts the chart tile
costs an LLM call. This module generates that prose once per series and caches it
in ``series_insights`` (see ``library.models.SeriesInsight``), refreshing it when a
new document joins the series.

The LLM step is best-effort and self-contained, mirroring ``extraction.apply``:
a disabled feature, a missing API key, or an insufficient series all end in a
quiet ``None`` return rather than an error — the chart simply renders without a
description until the next document triggers a successful refresh.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.extractor import estimate_cost_usd
from library.models import (
    Document,
    Kind,
    OverrideAction,
    SeriesInsight,
    SeriesMembershipOverride,
)
from library.search import DocumentFilters
from library.series import SeriesSummary, summarize_series

logger = logging.getLogger(__name__)

# Two sentences of prose need very little room; this also caps per-series spend.
MAX_DESCRIPTION_TOKENS: int = 200

# Cap manual-membership examples fed to the prompt, PER direction (pin/exclude).
# Bounds prompt size and cost; the model only needs a few examples to learn the
# owner's intent about what belongs in the series.
MAX_OVERRIDE_EXAMPLES: int = 5

SERIES_SYSTEM_PROMPT: str = """\
You describe a recurring series of household documents (e.g. the monthly energy
bill from one provider) for "Library", a self-hosted family document archive.

You are given pre-computed statistics for one series: the sender, the document
kind, the currency, the cadence, and a dated timeline of amounts.

Write ONE or TWO short sentences, in plain English, describing the spending
pattern a person would care about: the typical amount, whether it is rising,
falling, or steady, and any notable spikes or seasonality visible in the
timeline. Be concrete and quote amounts/percentages where useful. Do NOT add a
preamble, a heading, bullet points, or advice — return only the description
sentences.

The owner may have manually curated which documents belong in the series
(pinning ones the automatic grouping missed, excluding ones it wrongly
included). When such corrections are listed, treat them as authoritative about
the series' true membership."""


@dataclass(frozen=True, slots=True)
class OverrideExample:
    """One manual membership correction, rendered as a prompt hint (see W9)."""

    action: OverrideAction
    document_id: int
    title: str | None


async def load_override_examples(
    session: AsyncSession,
    sender_id: int,
    kind_id: int,
    currency: str | None,
    *,
    cap: int = MAX_OVERRIDE_EXAMPLES,
) -> list[OverrideExample]:
    """Up to ``cap`` pin and ``cap`` exclude examples for one series, oldest first."""
    currency_match = (
        SeriesMembershipOverride.currency.is_(None)
        if currency is None
        else SeriesMembershipOverride.currency == currency
    )
    statement = (
        select(
            SeriesMembershipOverride.action,
            SeriesMembershipOverride.document_id,
            Document.title,
        )
        .join(Document, Document.id == SeriesMembershipOverride.document_id)
        .where(
            SeriesMembershipOverride.sender_id == sender_id,
            SeriesMembershipOverride.kind_id == kind_id,
            currency_match,
        )
        .order_by(SeriesMembershipOverride.created_at)
    )
    pins: list[OverrideExample] = []
    excludes: list[OverrideExample] = []
    for action, document_id, title in (await session.execute(statement)).all():
        bucket = pins if action == OverrideAction.PIN else excludes
        if len(bucket) < cap:
            bucket.append(OverrideExample(action=action, document_id=document_id, title=title))
    return pins + excludes


def _describe_override(example: OverrideExample) -> str:
    return f'"{example.title or "untitled"}" [#{example.document_id}]'


def build_series_prompt(summary: SeriesSummary, overrides: Sequence[OverrideExample] = ()) -> str:
    """Render a series summary into the user prompt for description generation.

    When ``overrides`` are present, the owner's manual membership corrections are
    appended as a labelled hint block so the description reflects the curated
    series rather than the raw automatic grouping.
    """
    lines: list[str] = [
        f"Sender: {summary.sender}",
        f"Document kind: {summary.kind}",
        f"Currency: {summary.currency}",
        f"Cadence: {summary.cadence}",
        f"Number of documents: {summary.count}",
    ]
    dist = summary.distribution
    if dist is not None:
        lines.append(
            f"Amounts — mean {dist.mean}, median {dist.median}, min {dist.minimum}, "
            f"max {dist.maximum}, stdev {dist.stdev}"
        )
    if summary.trend is not None:
        lines.append(
            f"Trend (first to last): {summary.trend.direction} "
            f"({summary.trend.change_pct * 100:+.1f}%)"
        )
    timeline = ", ".join(f"{when.isoformat()}={amount}" for when, amount, _ in summary.points)
    lines.append(f"Timeline: {timeline}")

    pins = [o for o in overrides if o.action == OverrideAction.PIN]
    excludes = [o for o in overrides if o.action == OverrideAction.EXCLUDE]
    if pins or excludes:
        lines.append("")
        lines.append("The owner manually curated this series' membership (authoritative):")
        if pins:
            lines.append(
                "- Belongs in the series: " + ", ".join(_describe_override(o) for o in pins)
            )
        if excludes:
            lines.append("- Does NOT belong: " + ", ".join(_describe_override(o) for o in excludes))
    return "\n".join(lines)


async def generate_description(
    client: AsyncAnthropic,
    model: str,
    summary: SeriesSummary,
    overrides: Sequence[OverrideExample] = (),
) -> tuple[str, int, int]:
    """Call the LLM once; return ``(description, input_tokens, output_tokens)``."""
    response = await client.messages.create(
        model=model,
        max_tokens=MAX_DESCRIPTION_TOKENS,
        system=SERIES_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_series_prompt(summary, overrides)}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    return text, response.usage.input_tokens, response.usage.output_tokens


async def _kind_slug(session: AsyncSession, kind_id: int) -> str | None:
    kind = await session.get(Kind, kind_id)
    return kind.slug if kind is not None else None


async def _upsert(
    session: AsyncSession,
    *,
    sender_id: int,
    kind_id: int,
    currency: str | None,
    description: str,
    model: str,
    member_count: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> SeriesInsight:
    """Insert or replace the cached row for one series, atomically.

    Two documents in the same series indexed close together queue two refresh
    jobs; a plain SELECT-then-INSERT would race and the loser would hit the
    unique constraint. ``INSERT ... ON CONFLICT DO UPDATE`` keyed on the
    ``series_insights_sender_kind_currency`` constraint makes the upsert a single
    atomic statement instead.
    """
    mutable = {
        "description": description,
        "model": model,
        "member_count": member_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    statement = (
        pg_insert(SeriesInsight)
        .values(sender_id=sender_id, kind_id=kind_id, currency=currency, **mutable)
        .on_conflict_do_update(
            constraint="series_insights_sender_kind_currency",
            set_={**mutable, "updated_at": func.now()},
        )
    )
    await session.execute(statement)
    await session.commit()

    currency_match = (
        SeriesInsight.currency.is_(None) if currency is None else SeriesInsight.currency == currency
    )
    return (
        await session.execute(
            select(SeriesInsight).where(
                SeriesInsight.sender_id == sender_id,
                SeriesInsight.kind_id == kind_id,
                currency_match,
            )
        )
    ).scalar_one()


async def refresh_series_insight(
    session: AsyncSession,
    settings: Settings,
    sender_id: int,
    kind_id: int,
    *,
    client: AsyncAnthropic | None = None,
) -> SeriesInsight | None:
    """Regenerate and cache the prose description for one ``(sender, kind)`` series.

    Returns the upserted row, or ``None`` when the work is skipped: feature
    disabled, no API key, or the series has too few members for stats. The
    series' currency bucket is chosen by ``summarize_series`` (the dominant one),
    so the cached row is keyed by that currency.
    """
    if not settings.extraction_enabled:
        logger.debug("series insight skipped (extraction disabled)")
        return None

    kind_slug = await _kind_slug(session, kind_id)
    if kind_slug is None:
        logger.debug("series insight skipped (unknown kind_id %s)", kind_id)
        return None

    summary = await summarize_series(
        session,
        filters=DocumentFilters(sender_id=sender_id, kind_slug=kind_slug),
        settings=settings,
        reference="latest",
    )
    if summary.status != "ok":
        logger.debug(
            "series insight skipped (insufficient series for sender %s kind %s)",
            sender_id,
            kind_id,
        )
        return None

    owned_client = client is None
    if owned_client:
        if settings.anthropic_api_key is None:
            logger.debug("series insight skipped (missing api key)")
            return None
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

    overrides = await load_override_examples(session, sender_id, kind_id, summary.currency)
    try:
        description, input_tokens, output_tokens = await generate_description(
            client, settings.extraction_model, summary, overrides
        )
    finally:
        if owned_client:
            await client.close()

    if not description:
        logger.warning(
            "series insight skipped (empty description for sender %s kind %s)",
            sender_id,
            kind_id,
        )
        return None

    return await _upsert(
        session,
        sender_id=sender_id,
        kind_id=kind_id,
        currency=summary.currency,
        description=description,
        model=settings.extraction_model,
        member_count=summary.count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost_usd(settings.extraction_model, input_tokens, output_tokens),
    )
