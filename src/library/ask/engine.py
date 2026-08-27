"""Agentic /ask: Claude orchestrates retrieval tools to answer with citations.

Claude is given read tools — ``semantic_search`` (hybrid content retrieval),
``query_documents`` (structured aggregation over metadata),
``compare_to_series`` (statistical summary of a recurring-document series),
and ``get_document`` (full text + comments for one located document) — plus a
confirmation-gated write tool, ``update_document_metadata``, and decides which
to call for a question. It must answer only from tool results and cite the
document ids it used. The loop is bounded (``ask_max_tool_turns``); the
embedding and answer cost is summed for the audit log.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import LLMBackend, Settings
from library.documents_service import (
    apply_document_update,
    header_fields_changed,
    revalidate_after_edit,
)
from library.embedding import EmbeddingError, embed_query
from library.extraction.extractor import estimate_cost_usd
from library.jobs import embed_document
from library.llm import subscription
from library.models import Document, DocumentComment, DocumentPage, ReviewStatus
from library.schemas import DocumentUpdate
from library.search import DocumentFilters, search_reach, semantic_search
from library.series import serialise_summary, summarize_series
from library.structured_query import CONCEPT_TO_KIND, query_documents

logger = logging.getLogger(__name__)

ASK_SYSTEM_PROMPT_TEMPLATE: str = """\
You answer questions about a personal/family document archive (invoices,
contracts, utility bills, letters, receipts in Dutch and English).

Today's date is {today}. The current year is {year}. Resolve relative dates
against today: "last year" means {last_year}, "this year" means {year}.

Use the tools to find evidence, then answer:
- semantic_search: find documents by content/meaning (e.g. "travel allowance
  clause in my contract"). Use for questions about what a document says.
- query_documents: aggregate over structured metadata (e.g. "who was my energy
  provider last year", "how much did I spend on utilities in 2025"). Use for
  who/how-many/how-much/which-over-time questions. Filter by kind, sender,
  recipient, date range, and the user's own projects, matters and tags.
- compare_to_series: compare a recurring bill to its usual values / last year /
  trend (e.g. "is this electricity bill higher than usual?"). Takes the same
  filters.
- get_document: read one document in full (structured fields, the user's
  comments, and its text) once you have located it via another tool. A
  document's comments are the user's own notes about it and are authoritative
  personal context — trust them over inference from the document alone (e.g.
  a comment saying "this is my current house" settles which address is
  current).
- update_document_metadata: update a document's metadata (title, summary,
  sender, recipient, kind, tags, projects, dates, amount, currency, language).
  You may only edit a document that a tool surfaced earlier in THIS
  conversation. It is confirmation-gated: FIRST call it with confirmed=false to
  preview the change (nothing is written), then state the exact proposed change
  to the user in prose and wait for their explicit agreement. Only AFTER the
  user agrees in a later message may you call it again with confirmed=true to
  save. Never edit a document that was not surfaced in this conversation, and
  never set confirmed=true before the user has explicitly agreed.

The user may attach one or more images (a photo or scan of a document) with the
question. Read them directly as evidence, and combine what they show with tool
results when answering.

The "Archive context" block at the end of this prompt names the user, the
recipient names that are theirs, and the archive's vocabulary: kind, matter,
project and tag slugs, and the most frequent senders. Use those exact slugs and
names in tool calls instead of guessing; when a question says "my"/"me"/"I",
it means that user. If it carries an "About the user" note, that is the user's
own account of their household and circumstances — authoritative personal
context, like document comments: trust it over inference from documents.

Rules:
- Answer ONLY from tool results. Never invent facts.
- If the tools return nothing relevant, say plainly that the archive does not
  appear to contain the answer.
- Cite the document id(s) your answer relies on, inline like [#42]. If you
  cannot answer from the tool results, say so plainly and cite nothing — do
  not list the documents you looked at and rejected.
- Some tool results carry a "coverage" block (query_documents,
  compare_to_series, and semantic_search). If `excluded` is non-empty, the
  rows do NOT account for every matching document, and you MUST say so in
  your answer with the reason and the count — e.g. "EUR 1,240 across 14
  bills; 3 more matched but no amount could be read from them". If
  `needs_review` is above zero, you MUST also say so: those documents are
  included in the number but the archive flagged their extracted metadata as
  unreliable. Never present a partial total as if it were complete, and never
  silently drop the flagged documents to make the caveat go away.
  semantic_search's coverage instead carries `unembedded`: if it is above
  zero, matching documents exist but are not in the search index, and you
  MUST say your answer is incomplete for that technical reason — never
  report this as the archive being silent on the topic.
- Be concise and direct. Dutch terms may answer English questions and vice
  versa (e.g. "reiskostenvergoeding" = travel allowance).
"""


def _system_prompt(today: date, archive_context: str | None = None) -> str:
    """Render the Ask system prompt with concrete dates so the model resolves
    relative references ("last year") against the real current date.

    ``archive_context`` (see ``library.ask.context``) is appended as the final
    block. It shares the static prompt's cache breakpoint on purpose: the
    block only changes when the taxonomy does, so a separate breakpoint would
    spend one of the four the API allows for no measurable gain.
    """
    prompt = ASK_SYSTEM_PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        year=today.year,
        last_year=today.year - 1,
    )
    if archive_context:
        prompt = f"{prompt}\n{archive_context}"
    return prompt


def _kind_hint() -> str:
    pairs = ", ".join(f"{concept}={slug}" for concept, slug in CONCEPT_TO_KIND.items())
    return f"Concept→kind hints: {pairs}."


# Filter parameters shared by the two structured tools. They map 1:1 onto
# ``DocumentFilters``; slugs are the ones the archive-context block lists.
_FILTER_PROPERTIES: dict[str, Any] = {
    "kind": {"type": "string", "description": "Kind slug filter, e.g. utility-bill."},
    "sender_contains": {"type": "string", "description": "Substring of sender name."},
    "recipient_contains": {
        "type": "string",
        "description": (
            "Substring of the recipient (addressee) name. Use the user's own "
            "recipient names from the archive context for 'my' documents."
        ),
    },
    "projects": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Project slugs; a document in ANY of them matches.",
    },
    "matters": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Matter slugs; a document in ANY of them matches.",
    },
    "tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tag slugs; a document must carry ALL of them.",
    },
    "date_from": {"type": "string", "description": "Inclusive ISO date lower bound."},
    "date_to": {"type": "string", "description": "Inclusive ISO date upper bound."},
}

# `review_status` lives in its own dict rather than `_FILTER_PROPERTIES` because
# only `query_documents` accepts it as a filter and can report the drop: its
# `coverage` block discloses a `filtered_review_status` exclusion (see
# `structured_query.py`). `compare_to_series` reports its own `coverage`
# block too, but for a different set of reasons — `summarize_series` narrows
# to one sender/kind/currency, so its `excluded` covers amountless documents,
# non-dominant groups, and non-dominant currency buckets, none of which is a
# `review_status` filter — so offering that property here would promise a
# filter the tool cannot honour or explain.
_REVIEW_STATUS_PROPERTY: dict[str, Any] = {
    "review_status": {
        "type": "string",
        "enum": ["verified", "needs_review", "unreviewed"],
        "description": (
            "Trust state of a document's EXTRACTED metadata, not of the document "
            "itself. needs_review means the archive's validator flagged the "
            "extraction — most often because the amount does not appear anywhere "
            "in the document's text. Omit to include everything (the default, and "
            "usually right). Use needs_review to LIST what the user should check; "
            "do not silently filter it out of a total, because dropping it changes "
            "the number without saying so — report the count instead."
        ),
    },
}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "semantic_search",
        "description": (
            "Hybrid full-text + semantic search over document contents. Returns "
            "the most relevant documents with a matching excerpt. Use for "
            "questions about what documents say. Accepts the same metadata "
            "filters as query_documents — scope the search whenever the question "
            "names a sender, a kind or a date range, rather than searching the "
            "whole archive and hoping. The result carries a `coverage` block: "
            "`matched` is how many documents passed your filters, `returned` how "
            "many came back, and `unembedded` how many matched documents have no "
            "search index at all. Read it before concluding anything is absent — "
            "`matched: 0` means your filters excluded everything (widen them and "
            "retry), whereas `matched: 40, returned: 0` means those 40 documents "
            "genuinely do not say this. A non-zero `unembedded` means the answer "
            "is incomplete for a technical reason: say so. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of what to find.",
                },
                **_FILTER_PROPERTIES,
                "top_k": {
                    "type": "integer",
                    "description": (
                        "How many documents to return. Omit for the archive's "
                        "configured default; raise it for 'find every document "
                        "that mentions X' questions. Values above the configured "
                        "maximum are clamped, so asking for more than the "
                        "archive allows is safe."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_documents",
        "description": (
            "Aggregate over structured metadata (sender, kind, document_date, "
            "amount_total). Use for who/how-many/how-much/over-time questions. "
            "Every result carries a `coverage` block — `matched` documents met "
            "your filters, `included` are the ones the rows account for, "
            "`excluded` maps a reason to how many were dropped for it, and "
            "`needs_review` counts included documents whose extracted metadata "
            "the archive flagged as untrustworthy. Read it before you answer: a "
            "total over `included` documents is not a total over `matched` ones. "
            "`matched`/`included`/`excluded` always count DOCUMENTS, even for "
            "distinct_senders — a coverage of 1 excluded out of 12 with 3 sender "
            "rows means 1 of 12 documents was dropped, not 1 of 3 senders. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "aggregate": {
                    "type": "string",
                    "enum": ["list", "distinct_senders", "sum_amount"],
                    "description": (
                        "distinct_senders: unique senders (e.g. providers). "
                        "sum_amount: total amounts (real expenditure — quotes/"
                        "estimates are excluded automatically; pass kind='quote' "
                        "to total quotes instead). list: matching documents."
                    ),
                },
                **_FILTER_PROPERTIES,
                **_REVIEW_STATUS_PROPERTY,
                "group_by": {
                    "type": "string",
                    "enum": ["sender", "kind"],
                    "description": "Group sum_amount by sender or kind.",
                },
            },
            "required": ["aggregate"],
        },
    },
    {
        "name": "compare_to_series",
        "description": (
            "Compare a recurring document (same sender + kind) to its usual "
            "values. Use for 'more/less than usual', 'compared to last year', "
            "'are my bills going up'. Identify the series via kind + sender. "
            "Returns distribution stats, a reference-vs-usual verdict, a trend, "
            "and a year-over-year comparison. "
            "The result carries a `coverage` block on the same terms as "
            "query_documents: a series is deliberately narrowed to one sender, "
            "one kind and one currency, so `excluded` reports the documents "
            "that narrowing removed — `no_amount`, `other_series_group`, "
            "`other_currency`, and `manually_excluded` (a user override). "
            "A 'usual' band computed over 3 of 11 matching documents is not "
            "a fact about all 11. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPERTIES,
                "reference": {
                    "type": "string",
                    "description": "'latest' (default) to compare the newest bill, or a number.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_document_metadata",
        "description": (
            "Update the metadata of a document that was surfaced by another tool "
            "in THIS conversation. Two-phase, confirmation-gated:\n"
            "1. Call with confirmed=false (the default) to PREVIEW — this writes "
            "nothing and returns the current vs proposed value for each field.\n"
            "2. State the exact change to the user in prose and wait for their "
            "explicit agreement. Only AFTER they agree, call again with "
            "confirmed=true to persist it.\n"
            "Never call with confirmed=true until the user has agreed in a later "
            "message. Only fields you provide change; tags and projects are "
            "full-replacement lists. You may only edit a document_id that a tool "
            "returned earlier in this conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "Id of a document surfaced earlier in this conversation.",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "false (default) previews without writing; true persists the "
                        "change and is only allowed after the user explicitly agrees."
                    ),
                },
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "recipient": {"type": "string", "description": "Recipient name (upserted)."},
                "sender": {"type": "string", "description": "Sender name (upserted)."},
                "kind_slug": {"type": "string", "description": "Existing kind slug."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full replacement list of tag slugs.",
                },
                "projects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full replacement list of project slugs/names.",
                },
                "matters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full replacement list of business-matter slugs/names.",
                },
                "document_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
                "due_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
                "expiry_date": {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
                "amount_total": {"type": "number"},
                "currency": {"type": "string", "description": "3-letter ISO currency code."},
                "language": {"type": "string", "description": "e.g. nld or eng."},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "get_document",
        "description": (
            "Read one document in full by its id: structured fields, the user's "
            "comments (authoritative personal context), and its text. Use after "
            "locating a document via semantic_search to answer a specific detail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
        },
    },
]

# Editable metadata fields the write tool forwards to DocumentUpdate.
#
# Derived, not listed. docs/ask.md already documents this as "the same surface as
# PATCH /api/documents/{id}", so DocumentUpdate *is* the specification and any
# hand-written copy can only drift from it — as it had: `matters` was missing, so
# an Ask write of {"matters": [...]} was silently dropped from `fields` here and
# reported as a success with nothing changed.
#
# The old comment called this "a safe subset (no status/review fields)", which
# was misleading: DocumentUpdate contains no status or review fields at all, so
# there is nothing to subset. Every field on it is user-editable by definition —
# that is what makes deriving safe rather than merely convenient.
_WRITABLE_FIELDS: tuple[str, ...] = tuple(DocumentUpdate.model_fields)


@dataclass(frozen=True, slots=True)
class AskCitation:
    """A document the answer relies on."""

    document_id: int
    title: str | None
    page_number: int | None = None


@dataclass(slots=True)
class AskResult:
    """The answer plus citations, tools used, cost, and replay blocks."""

    answer: str
    citations: list[AskCitation]
    used_tools: list[str]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    # The same total, broken down, so telemetry can report a cache hit rate.
    # `input_tokens` stays the total on both backends; these sit beside it.
    fresh_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    turn_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TitleResult:
    """A generated conversation title plus the cost of generating it."""

    title: str
    cost_usd: float = 0.0


_TITLE_SYSTEM_PROMPT: str = (
    "You name conversations for a document-archive assistant. Given the user's "
    "question and the assistant's answer, produce a short, specific title of "
    "three to six words that captures the conversation's subject. Return only "
    "the title text: no surrounding quotes, no trailing punctuation, and no "
    'prefix such as "Title:".'
)

# Returned when the loop ends with no usable answer, on either backend.
_NO_ANSWER = "I couldn't find an answer to that in the archive."

# Binds this turn's mutable state to ``_dispatch_tool`` so both backends drive
# identical tool semantics; see ``run_ask``.
_Dispatcher = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Titles are for a sidebar row; keep them short enough to read at a glance.
_TITLE_MAX_CHARS = 60


def _clean_title(raw: str) -> str:
    """Trim a model-produced title to a tidy sidebar label.

    Strips wrapping quotes, collapses whitespace, drops a trailing period, and
    caps the length. Returns "" when nothing usable remains so the caller keeps
    its fallback title.
    """
    title = " ".join(raw.split()).strip().strip("\"'“”").strip().rstrip(".")
    return title[:_TITLE_MAX_CHARS].strip()


async def generate_thread_title(
    client: AsyncAnthropic,
    *,
    model: str,
    question: str,
    answer: str,
    settings: Settings | None = None,
    backend: LLMBackend = "api",
) -> TitleResult:
    """Summarise a question/answer exchange into a short conversation title.

    One bounded, cheap model call. Returns the cleaned title (possibly "" if the
    model returned nothing usable) plus the call's estimated cost. Raises on API
    error — the caller owns the fallback, because a title must never block or
    fail an answer.

    ``backend`` is the ask surface's resolved backend — titles follow it, because
    splitting them off would leave a "subscription" deployment still needing an
    API key for a handful of tokens. That does mean a ~32-token title pays the
    Agent SDK's fixed harness cost, once per *new thread* rather than per turn.
    It is passed in rather than resolved here so the caller makes one database
    round-trip per turn, and so the standalone backfill script — which has no
    session — keeps working on the default ``api``.
    """
    # Cap both sides so a pathologically long input can't inflate the title
    # call's token cost (the question is already ≤1000 chars from the API, but
    # the backfill path reads stored queries — keep the bound explicit).
    user_text = f"Question:\n{question.strip()[:2000]}\n\nAnswer:\n{answer.strip()[:2000]}"

    if backend == "subscription" and settings is not None:
        title_result = await subscription.text_call(
            config_dir=settings.claude_config_dir,
            model=model,
            system_prompt=_TITLE_SYSTEM_PROMPT,
            prompt=user_text,
        )
        return TitleResult(
            title=_clean_title(title_result.text),
            cost_usd=estimate_cost_usd(
                model, title_result.usage.input_tokens, title_result.usage.output_tokens
            ),
        )

    response = await client.messages.create(
        model=model,
        max_tokens=32,
        system=_TITLE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    cost = estimate_cost_usd(model, response.usage.input_tokens, response.usage.output_tokens)
    return TitleResult(title=_clean_title(_text_of(response.content)), cost_usd=cost)


def _parse_date(value: object) -> date | None:
    # Deliberately swallow-and-continue, unlike `_invalid_review_status` below:
    # dates are free-text natural language the model may get slightly wrong
    # (and this shape predates the trust-filter work), whereas `review_status`
    # is a new enum-constrained filter the model is expected to get right from
    # the schema — a future reader should not "unify" the two.
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def _run_semantic_search(
    session: AsyncSession,
    settings: Settings,
    args: dict[str, Any],
    cited: set[int],
    pages: dict[int, int],
) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}
    try:
        embedding = await embed_query(query, settings=settings)
    except EmbeddingError as exc:
        logger.warning("ask semantic_search embedding failed: %s", exc)
        return {"error": "semantic search is temporarily unavailable"}
    # Reuse the shared helper (§8.2) rather than forking it, then strip
    # `review_status`: the tool's schema does not offer that property (see
    # `_REVIEW_STATUS_PROPERTY`'s comment), but `_filters_from_args` reads it
    # from `args` unconditionally, so a model that emits it anyway would get a
    # silently narrowed search this tool's coverage block cannot explain — it
    # reports reach (`matched`/`returned`/`unembedded`), not exclusion
    # reasons. Stripping here, rather than teaching the helper to omit it,
    # keeps `_filters_from_args` one shared mapping for every caller.
    filters = replace(_filters_from_args(args), review_status=None)
    top_k = _top_k_arg(args.get("top_k"), settings)
    hits = await semantic_search(
        session,
        query=query,
        query_embedding=embedding,
        filters=filters,
        top_k=top_k,
        chunks_per_doc=settings.retrieve_chunks_per_doc,
    )
    reach = await search_reach(session, filters)
    rows = []
    for hit in hits:
        cited.add(hit.document.id)
        if hit.page_number is not None and hit.document.id not in pages:
            pages[hit.document.id] = hit.page_number
        rows.append(
            {
                "document_id": hit.document.id,
                "title": hit.document.title,
                "sender": hit.document.sender.name if hit.document.sender else None,
                "recipient": hit.document.recipient.name if hit.document.recipient else None,
                "document_date": (
                    hit.document.document_date.isoformat() if hit.document.document_date else None
                ),
                "excerpt": (
                    "\n\n[…]\n\n".join(hit.chunk_texts) if hit.chunk_texts else hit.chunk_text
                ),
            }
        )
    return {
        "results": rows,
        "coverage": {
            "matched": reach.matched,
            "returned": len(rows),
            "unembedded": reach.unembedded,
        },
    }


def _top_k_arg(value: object, settings: Settings) -> int:
    """A usable ``top_k`` from a tool argument, clamped into range.

    The clamp is load-bearing, not defensive tidiness. ``semantic_search`` ends
    in ``ranked[:top_k]``, so a NEGATIVE top_k slices from the end and silently
    returns a near-arbitrary subset — measured against seven matching documents,
    ``top_k=-1`` returns six hits and ``top_k=-3`` returns four, with no error
    anywhere. A model that emits a negative value would get a quietly wrong
    answer, so the floor of 1 is what stops that.

    A non-integer degrades to the configured default rather than raising: the
    schema's ``"type": "integer"`` steers the model but does not bind it, and a
    hallucinated ``"ten"`` must not 500 inside the tool loop. This mirrors how
    ``_review_status_arg`` treats an unrecognised enum value.

    The missing-argument path (``value is None``) is clamped through the same
    ``ask_search_max_top_k`` ceiling as an explicit value, not returned as-is.
    ``settings.retrieve_top_k`` is an independently configured operator knob
    (``LIBRARY_RETRIEVE_TOP_K``) with no relationship to the ceiling, so
    without this an operator setting it above the ceiling would give the
    model's default call MORE depth than an explicit ``top_k`` at the ceiling
    is even allowed to ask for — the opposite of what a ceiling means.
    """
    if value is None:
        requested = settings.retrieve_top_k
    else:
        try:
            requested = int(str(value).strip())
        except (TypeError, ValueError):
            logger.info("ask: ignoring non-integer top_k %r", value)
            requested = settings.retrieve_top_k
    return max(1, min(requested, settings.ask_search_max_top_k))


def _text_arg(value: object) -> str | None:
    """A non-blank string argument, else None (the model sometimes sends "")."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _slug_args(value: object) -> tuple[str, ...]:
    """A list-of-slugs argument as a tuple, tolerating None, "" and a bare string."""
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else list(value) if isinstance(value, list) else []
    return tuple(slug for slug in (_text_arg(item) for item in items) if slug is not None)


def _review_status_arg(value: object) -> ReviewStatus | None:
    """A ``ReviewStatus`` from a tool argument, or None.

    An unrecognised value degrades to "no filter" rather than raising: the JSON
    schema's ``enum`` steers the model but does not bind it, and a hallucinated
    status must not turn into a 500 inside the tool loop. This keeps
    ``_filters_from_args`` usable as-is by ``compare_to_series`` (whose schema
    does not even offer ``review_status`` — see ``_REVIEW_STATUS_PROPERTY``).
    ``query_documents`` additionally validates the raw value itself, via
    ``_invalid_review_status`` below, so a bad value there is surfaced to the
    model as an error instead of silently degrading like this.
    """
    text = _text_arg(value)
    if text is None:
        return None
    try:
        return ReviewStatus(text)
    except ValueError:
        logger.info("ask: ignoring unknown review_status %r", text)
        return None


def _invalid_review_status(value: object) -> str | None:
    """The offending text if ``value`` is a non-blank ``review_status`` that
    is not a valid :class:`ReviewStatus`, else ``None``.

    Only ``_run_query_documents`` calls this. Unlike ``_review_status_arg``
    (used by ``_filters_from_args`` for both structured tools), this does not
    swallow the bad value: ``query_documents`` has a `coverage` block that can
    describe what a filter removed, so it can also tell the model outright
    that ``review_status`` was not understood — silently returning "no
    filter" would hand back the entire archive under a filtered-sounding
    tool call, with only a server-side log line to show for it.
    """
    text = _text_arg(value)
    if text is None:
        return None
    try:
        ReviewStatus(text)
    except ValueError:
        return text
    return None


def _filters_from_args(args: dict[str, Any]) -> DocumentFilters:
    """The ``DocumentFilters`` for a structured tool call's ``_FILTER_PROPERTIES``."""
    return DocumentFilters(
        kind_slug=_text_arg(args.get("kind")),
        sender_contains=_text_arg(args.get("sender_contains")),
        recipient_contains=_text_arg(args.get("recipient_contains")),
        project_slugs=_slug_args(args.get("projects")),
        matter_slugs=_slug_args(args.get("matters")),
        tag_slugs=_slug_args(args.get("tags")),
        date_from=_parse_date(args.get("date_from")),
        date_to=_parse_date(args.get("date_to")),
        review_status=_review_status_arg(args.get("review_status")),
    )


async def _run_query_documents(
    session: AsyncSession, args: dict[str, Any], cited: set[int]
) -> dict[str, Any]:
    bad_review_status = _invalid_review_status(args.get("review_status"))
    if bad_review_status is not None:
        valid_values = ", ".join(status.value for status in ReviewStatus)
        return {
            "error": (
                f"unrecognised review_status {bad_review_status!r}; "
                f"valid values are: {valid_values}"
            )
        }
    filters = _filters_from_args(args)
    result = await query_documents(
        session,
        filters=filters,
        aggregate=args.get("aggregate", "list"),
        group_by=args.get("group_by"),
    )
    for row in result["rows"]:
        if isinstance(row.get("document_ids"), list):
            cited.update(row["document_ids"])
        elif "id" in row:
            cited.add(row["id"])
    # Widen the TypedDict to the plain mapping this function's error branch
    # above also returns: mypy treats a TypedDict as incompatible with
    # `dict[str, Any]` because a caller could insert a key the declaration
    # forbids.
    return dict(result)


async def _run_compare_to_series(
    session: AsyncSession, settings: Settings, args: dict[str, Any], cited: set[int]
) -> dict[str, Any]:
    filters = _filters_from_args(args)
    raw_reference = args.get("reference", "latest")
    reference: Decimal | Literal["latest"]
    if raw_reference in (None, "latest", ""):
        reference = "latest"
    else:
        try:
            reference = Decimal(str(raw_reference))
        except (InvalidOperation, ValueError):
            reference = "latest"
    summary = await summarize_series(
        session, filters=filters, settings=settings, reference=reference
    )
    cited.update(summary.document_ids)
    return serialise_summary(summary)


def _preview_current(document: Document, field: str) -> Any:
    """Human-readable current value of an editable field (names/slugs, not ids)."""
    if field == "sender":
        return document.sender.name if document.sender else None
    if field == "recipient":
        return document.recipient.name if document.recipient else None
    if field == "kind_slug":
        return document.kind.slug if document.kind else None
    if field == "tags":
        return sorted(tag.slug for tag in document.tags)
    if field == "projects":
        return sorted(project.slug for project in document.projects)
    if field == "matters":
        return sorted(matter.slug for matter in document.matters)
    # Scalars fall through. Any *relationship* field must get a branch above:
    # tool output is serialised with json.dumps(..., default=str), so a bare ORM
    # object does not fail loudly — it renders as "<Matter object at 0x...>" in
    # the preview the user is asked to approve.
    return getattr(document, field, None)


async def _run_update_document(
    session: AsyncSession,
    settings: Settings,
    args: dict[str, Any],
    editable_ids: set[int],
    previewed_ids: set[int],
) -> dict[str, Any]:
    """Propose-then-confirm write of a surfaced document's metadata.

    Guardrails: (1) refuses any ``document_id`` not surfaced by a read tool in
    this conversation; (2) ``confirmed=true`` is refused unless the same document
    was previewed in an EARLIER turn (``previewed_ids`` is seeded only from thread
    history, never from previews made in the current turn) — so the user has
    actually seen the proposal and replied before anything is written, enforced
    in code rather than only by the system prompt. ``confirmed=false`` returns a
    current-vs-proposed preview and writes nothing; ``confirmed=true`` applies the
    edit (edited_by="ask") and commits.
    """
    raw_id = args.get("document_id")
    try:
        document_id = int(raw_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"error": "document_id is required and must be an integer"}
    if document_id not in editable_ids:
        return {"error": "can only edit documents found in this conversation"}

    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        return {"error": f"document {document_id} not found"}

    fields = {name: args[name] for name in _WRITABLE_FIELDS if name in args}
    if not fields:
        return {"error": "no editable fields provided"}

    if not bool(args.get("confirmed", False)):
        preview = {
            name: {"current": _preview_current(document, name), "proposed": value}
            for name, value in fields.items()
        }
        # Deliberately does NOT record the id as previewed: a confirmed write is
        # only allowed once the preview has been shown to the user AND they have
        # replied — which only happens on a later question (the preview lands in
        # the thread history). Recording it here would let the model preview and
        # confirm in the same turn, before the user ever sees the proposal.
        return {
            "status": "preview",
            "document_id": document_id,
            "changes": preview,
            "note": (
                "Nothing was written. Tell the user this exact change and END your "
                "turn. Only if they reply agreeing, on a later message, call this "
                "again with confirmed=true."
            ),
        }

    if document_id not in previewed_ids:
        return {
            "error": (
                "preview required first: call with confirmed=false, show the user "
                "the proposed change, end your turn, and only confirm after they "
                "reply agreeing"
            )
        }

    try:
        update = DocumentUpdate(**fields)
    except ValidationError as exc:
        return {"error": "invalid field value", "detail": exc.errors(include_url=False)}

    try:
        edited = await apply_document_update(session, document, update, edited_by="ask")
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    # Recompute validation so an agent-applied fix clears its warning (and a bad
    # edit gets flagged) — same behaviour as the PATCH route (documents.py).
    await revalidate_after_edit(session, document, settings)
    await session.commit()
    # Same reasoning as the PATCH route (api/documents.py): a header-field edit
    # invalidates this document's stored chunk headers.
    if header_fields_changed(edited):
        await embed_document.defer_async(document_id=document_id)
    return {"status": "updated", "document_id": document_id, "updated_fields": edited}


async def _run_get_document(
    session: AsyncSession, settings: Settings, args: dict[str, Any]
) -> dict[str, Any]:
    """Read one document in full: structured fields, comments (queried
    explicitly — ``Document.comments``/``Document.pages`` are ``lazy="raise"``),
    and its text (joined markdown pages, falling back to ``ocr_text``),
    truncated to ``settings.ask_get_document_max_chars``."""
    raw_id = args.get("document_id")
    try:
        document_id = int(raw_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"error": "document_id is required and must be an integer"}

    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        return {"error": f"document {document_id} not found"}

    comment_rows = (
        (
            await session.execute(
                select(DocumentComment)
                .where(DocumentComment.document_id == document_id)
                .order_by(DocumentComment.created_at)
            )
        )
        .scalars()
        .all()
    )
    page_rows = (
        (
            await session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            )
        )
        .scalars()
        .all()
    )
    full_text = (
        "\n\n".join(page.markdown for page in page_rows) if page_rows else (document.ocr_text or "")
    )
    max_chars = settings.ask_get_document_max_chars
    text_truncated = len(full_text) > max_chars
    text = full_text[:max_chars] if text_truncated else full_text

    return {
        "document_id": document_id,
        "title": document.title,
        "sender": document.sender.name if document.sender else None,
        "recipient": document.recipient.name if document.recipient else None,
        "kind": document.kind.slug if document.kind else None,
        "document_date": document.document_date.isoformat() if document.document_date else None,
        "due_date": document.due_date.isoformat() if document.due_date else None,
        "expiry_date": document.expiry_date.isoformat() if document.expiry_date else None,
        "amount_total": float(document.amount_total) if document.amount_total is not None else None,
        "currency": document.currency,
        "language": document.language.value if document.language else None,
        "summary": document.summary,
        "topics": document.topics,
        "comments": [
            {"body": comment.body, "date": comment.created_at.isoformat()}
            for comment in comment_rows
        ],
        "text": text,
        "text_truncated": text_truncated,
    }


async def _dispatch_tool(
    session: AsyncSession,
    settings: Settings,
    name: str,
    args: dict[str, Any],
    cited: set[int],
    pages: dict[int, int],
    editable_ids: set[int],
    previewed_ids: set[int],
) -> dict[str, Any]:
    if name == "semantic_search":
        result = await _run_semantic_search(session, settings, args, cited, pages)
        editable_ids.update(cited)
        return result
    if name == "query_documents":
        query_result = await _run_query_documents(session, args, cited)
        editable_ids.update(cited)
        return query_result
    if name == "compare_to_series":
        result = await _run_compare_to_series(session, settings, args, cited)
        editable_ids.update(cited)
        return result
    if name == "update_document_metadata":
        return await _run_update_document(session, settings, args, editable_ids, previewed_ids)
    if name == "get_document":
        result = await _run_get_document(session, settings, args)
        if "document_id" in result:
            cited.add(result["document_id"])
            editable_ids.add(result["document_id"])
        return result
    return {"error": f"unknown tool {name}"}


def _collect_document_ids(value: Any, ids: set[int]) -> None:
    """Recursively gather document ids from a decoded tool_result payload."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("document_id", "id") and isinstance(item, int):
                ids.add(item)
            elif key == "document_ids" and isinstance(item, list):
                ids.update(i for i in item if isinstance(i, int))
            else:
                _collect_document_ids(item, ids)
    elif isinstance(value, list):
        for item in value:
            _collect_document_ids(item, ids)


def _is_content_block_list(value: Any) -> bool:
    """True if ``value`` is a non-empty list of ``{"type": "text", "text": str}``
    content blocks — the shape the ``subscription`` backend's SDK wraps a tool
    result in before it gets re-encoded as the outer ``tool_result`` string."""
    return (
        bool(value)
        and isinstance(value, list)
        and all(
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            for item in value
        )
    )


def _tool_result_payloads(history: list[dict[str, Any]]) -> Iterator[Any]:
    """Yield each decoded ``tool_result`` payload from replayed prior turns.

    The two backends shape a ``tool_result`` block's ``content`` string
    differently:

    - ``api`` backend (``_run_api_turn``): ``content`` is
      ``json.dumps(output, default=str)`` directly — one level of JSON.
    - ``subscription`` backend (``llm/subscription.py``): the SDK's tool
      result content is itself a list of content blocks
      (``[{"type": "text", "text": ...}]``), and since that list isn't a
      ``str`` it gets ``json.dumps``-ed *again* — two levels of JSON, with the
      real payload sitting inside the inner block's ``text``.

    Decode the outer JSON once, as before. If what comes back is a list of
    text content blocks, decode each block's ``text`` and yield those
    payloads instead of the block list itself; otherwise yield the decoded
    value as-is (the ``api`` shape). A malformed inner ``text`` is skipped
    rather than raised, matching the outer decode's existing tolerance for
    malformed history.
    """
    for message in history:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            raw = block.get("content")
            if not isinstance(raw, str):
                continue
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if _is_content_block_list(decoded):
                for inner_block in decoded:
                    try:
                        yield json.loads(inner_block["text"])
                    except (ValueError, TypeError):
                        continue
            else:
                yield decoded


def _ids_from_history(history: list[dict[str, Any]]) -> set[int]:
    """Document ids surfaced by tool results in replayed prior turns, so the
    write tool may edit documents cited earlier in the thread."""
    ids: set[int] = set()
    for payload in _tool_result_payloads(history):
        _collect_document_ids(payload, ids)
    return ids


def _previewed_ids_from_history(history: list[dict[str, Any]]) -> set[int]:
    """Document ids that were shown to the user as a write *preview* in replayed
    prior turns. A confirmed write is only allowed for an id that was previewed
    first, making propose-then-confirm a code invariant rather than a prompt
    contract the model could skip."""
    ids: set[int] = set()
    for payload in _tool_result_payloads(history):
        if (
            isinstance(payload, dict)
            and payload.get("status") == "preview"
            and isinstance(payload.get("document_id"), int)
        ):
            ids.add(payload["document_id"])
    return ids


async def _citations_for(
    session: AsyncSession, cited: set[int], pages: dict[int, int]
) -> list[AskCitation]:
    if not cited:
        return []
    rows = (
        await session.execute(
            select(Document.id, Document.title).where(Document.id.in_(cited)).order_by(Document.id)
        )
    ).all()
    return [
        AskCitation(document_id=did, title=title, page_number=pages.get(did)) for did, title in rows
    ]


def _text_of(content: list[Any]) -> str:
    return "\n".join(block.text for block in content if getattr(block, "type", None) == "text")


def _serialize_block(block: Any) -> dict[str, Any]:
    """Convert an Anthropic content block (SDK model or test fake) to a plain,
    JSON-serialisable dict suitable for re-sending and for JSONB storage."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": block.text}
    if block_type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    if block_type == "thinking":
        # Thinking blocks must be replayed byte-identical or the API rejects the
        # next call of the turn, and it is the `signature` that carries that
        # obligation — `thinking` itself is empty under the default omitted
        # display. The catch-all below would drop both, which fails as a 400 on
        # a later call rather than anywhere near this line.
        return {
            "type": "thinking",
            "thinking": getattr(block, "thinking", ""),
            "signature": getattr(block, "signature", ""),
        }
    # Unknown block type: keep the tag so the shape survives, but note that any
    # payload is lost. Real SDK blocks never reach here (they carry
    # `model_dump`); this is the hand-rolled/fake path.
    return {"type": block_type}


def _cached_usage(usage: Any) -> tuple[int, int]:
    """``(total_input_tokens, billable_input_tokens)`` for one API response.

    Anthropic reports cached tokens in fields *separate* from ``input_tokens``,
    so summing ``input_tokens`` alone silently under-reports every request the
    cache served — the better the cache works, the more the number lies. That
    matters most right after enabling caching, when spend appears to collapse
    partly because tokens stopped being counted rather than because they
    stopped being sent.

    Two different numbers are wanted, so both are returned:

    * **total** — how much context actually went in, cached or not. Comparable
      across cached and uncached turns, which is what makes it useful for
      "is this thread getting expensive?".
    * **billable** — the same tokens weighted by what they cost: cache reads
      bill at ~0.1x and cache writes at ~1.25x of the input rate. Feeding this
      to :func:`estimate_cost_usd` keeps the two-rate pricing table (which is
      validated at startup) untouched.
    """
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    uncached = int(usage.input_tokens)
    total = uncached + cache_read + cache_write
    billable = uncached + round(cache_read * 0.1) + round(cache_write * 1.25)
    return total, billable


def _apply_cache_control(messages: list[dict[str, Any]], history_len: int) -> None:
    """Mark the end of the rehydrated history prefix with an ephemeral cache
    breakpoint so re-sent prior turns hit the Anthropic prompt cache. Best
    effort: a no-op when there is no history or the boundary isn't block-form."""
    if history_len == 0:
        return
    boundary = messages[history_len - 1]
    content = boundary.get("content")
    if isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


async def _run_api_turn(
    *,
    settings: Settings,
    client: AsyncAnthropic,
    model: str,
    history: list[dict[str, Any]],
    question_msg: dict[str, Any],
    dispatch: _Dispatcher,
    result: AskResult,
    used: list[str],
    archive_context: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the turn against the metered Messages API.

    The bounded tool loop library has always used: we own the loop, so a turn is
    a sequence of ``messages.create`` calls and we decide when to stop. Returns
    ``(answer, turn_messages)``.
    """
    messages: list[dict[str, Any]] = [*history, question_msg]
    new_messages: list[dict[str, Any]] = [question_msg]
    _apply_cache_control(messages, len(history))

    system_prompt: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _system_prompt(date.today(), archive_context),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    answer = ""
    for _ in range(max(1, settings.ask_max_tool_turns)):
        response = await client.messages.create(
            model=model,
            max_tokens=settings.ask_max_answer_tokens,
            # Adaptive thinking, explicitly.
            #
            # On this model family, OMITTING `thinking` means the model runs
            # with no extended reasoning at all — the parameter's absence is not
            # a neutral default. Ask is a multi-hop task (retrieve, cross-check,
            # compare against a distribution), which is exactly the shape that
            # benefits, so it was the single largest accuracy lever available
            # and it costs one parameter.
            #
            # Two couplings worth keeping in view if this is ever tuned:
            #   * thinking tokens count against `max_tokens` — hence the raised
            #     `ask_max_answer_tokens` (see config.py);
            #   * thinking blocks come back in `response.content` and MUST be
            #     replayed unmodified on later calls. That already works:
            #     `_serialize_block` round-trips them via `model_dump` (keeping
            #     the signature) and `_text_of` filters to `type == "text"`, so
            #     reasoning never leaks into the answer.
            #
            # `display` is left at its default (omitted): nothing renders the
            # reasoning, so there is no reason to pay to transport or store it.
            thinking={"type": "adaptive"},
            # Cache the growing prefix on every iteration.
            #
            # This loop re-sends the WHOLE conversation each time round, so a
            # tool result fetched on pass 2 is paid for again on passes 3 and 4.
            # That is the dominant cost of a turn: measured on this archive, a
            # single turn shipped ~247k characters across four calls while its
            # stored transcript was only ~87k.
            #
            # Top-level `cache_control` caches the last cacheable block of the
            # request, which here is precisely that accumulated tool-result
            # tail. Re-reads then bill at ~0.1x instead of 1.0x. It is a
            # request-shaping hint only — same prompts, same answers.
            #
            # Breakpoint budget: the API allows four. We use the system prompt,
            # optionally the history boundary (`_apply_cache_control`, a no-op
            # on a thread's first turn), and this one.
            cache_control={"type": "ephemeral"},
            # Hand-built blocks crossing into the SDK's TypedDict unions, as in
            # extraction/judge.py. `messages` is appended to throughout the tool
            # loop below, so it stays a plain list and the assertion is made here.
            system=cast("list[TextBlockParam]", system_prompt),
            tools=cast("list[ToolParam]", TOOLS),
            messages=cast("list[MessageParam]", messages),
        )
        total_input, billable_input = _cached_usage(response.usage)
        result.input_tokens += total_input
        result.fresh_input_tokens += int(response.usage.input_tokens)
        result.cache_read_tokens += int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        result.cache_write_tokens += int(
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        )
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += estimate_cost_usd(model, billable_input, response.usage.output_tokens)

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": [_serialize_block(block) for block in response.content],
        }

        if response.stop_reason != "tool_use":
            answer = _text_of(response.content)
            new_messages.append(assistant_msg)
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            # `block.type`, not `getattr(block, "type", None)`: every member of
            # the SDK's content union carries `type`, and the literal comparison
            # is what narrows the union to `ToolUseBlock` for the accesses below.
            # `getattr` defeats that narrowing and was the sole cause of the 40
            # `union-attr` errors this module was once quarantined for.
            if block.type != "tool_use":
                continue
            used.append(block.name)
            output = await dispatch(block.name, dict(block.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                }
            )
        # stop_reason was tool_use but no tool_use blocks materialised: treat the
        # text as the answer rather than sending an empty user turn (a 400).
        if not tool_results:
            answer = _text_of(response.content)
            new_messages.append(assistant_msg)
            break
        tool_msg: dict[str, Any] = {"role": "user", "content": tool_results}
        messages.append(assistant_msg)
        messages.append(tool_msg)
        new_messages.append(assistant_msg)
        new_messages.append(tool_msg)
    else:
        logger.info("ask hit the tool-turn limit without a final answer")
        # The loop exhausted mid-tool-dance, so new_messages ends on a
        # tool_result (role "user"). Persisting that as the turn's history would
        # put two consecutive "user" turns when the next question is appended on
        # a follow-up — which the Anthropic API rejects (400). Close the turn
        # with the fallback answer as an assistant message so the stored history
        # alternates correctly and the tool_use/tool_result pair stays intact.
        answer = answer or _NO_ANSWER
        new_messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})

    return answer, new_messages


async def _run_subscription_turn(
    *,
    settings: Settings,
    model: str,
    question: str,
    history: list[dict[str, Any]],
    images: list[dict[str, str]] | None,
    question_msg: dict[str, Any],
    dispatch: _Dispatcher,
    result: AskResult,
    used: list[str],
    archive_context: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the turn against the Claude subscription via the Agent SDK.

    Here the SDK owns the loop, so ``ask_max_tool_turns`` becomes its turn cap
    rather than our own iteration count, and the transcript is reconstructed
    from the SDK's message stream. ``dispatch`` is library's same tool
    dispatcher — the SDK path adds a transport, never a second implementation.

    Cost is still estimated from ``MODEL_PRICING_USD_PER_MTOK``. Under a
    subscription no such dollars are billed, so ``cost_usd`` here means "what
    this turn would have cost on the API" — which is the number worth recording,
    both to measure the saving and to keep the ~32k-token harness overhead
    visible instead of free-looking.
    """
    loop = await subscription.tool_loop(
        config_dir=settings.claude_config_dir,
        model=model,
        system_prompt=_system_prompt(date.today(), archive_context),
        question=question,
        tools=TOOLS,
        dispatch=dispatch,
        max_turns=max(1, settings.ask_max_tool_turns),
        history_blocks=history,
        images=images,
    )

    used.extend(loop.used_tools)
    result.input_tokens += loop.usage.input_tokens
    result.fresh_input_tokens += loop.usage.fresh_input_tokens
    result.cache_read_tokens += loop.usage.cache_read_input_tokens
    result.cache_write_tokens += loop.usage.cache_creation_input_tokens
    result.output_tokens += loop.usage.output_tokens
    # Priced at the full input rate on purpose: this path is not metered, so the
    # figure is a conservative notional ceiling rather than a bill. Weighting the
    # cache components 0.1x/1.25x here (as the API path does) would change what
    # the number MEANS, which is a separate decision from exposing the split.
    result.cost_usd += estimate_cost_usd(model, loop.usage.input_tokens, loop.usage.output_tokens)

    answer = loop.answer
    if loop.hit_turn_limit and not answer:
        logger.info("ask hit the tool-turn limit without a final answer")
        answer = _NO_ANSWER

    # The question itself is not part of the SDK's reply stream, so prepend it:
    # a stored turn must open with the user's question for the thread to
    # rehydrate the same way it does on the API path.
    return answer, [question_msg, *loop.blocks]


async def run_ask(
    session: AsyncSession,
    *,
    question: str,
    settings: Settings,
    client: AsyncAnthropic,
    history_messages: list[dict[str, Any]] | None = None,
    images: list[dict[str, str]] | None = None,
    backend: LLMBackend = "api",
    archive_context: str | None = None,
) -> AskResult:
    """Answer ``question`` from the archive via a bounded Claude tool-use loop.

    ``history_messages`` is a rehydrated prefix of prior turns (already in block
    form); it is prepended so follow-ups can reason over earlier tool results.
    ``images`` are ``{"media_type", "data"}`` (base64) attachments rendered as
    image content blocks on the question turn for the multimodal model.

    ``backend`` selects the transport. It is resolved by the caller (see
    ``library.llm.backends.resolve_backend``) rather than read off ``settings``,
    because an admin can change it at runtime — and passed in rather than
    resolved here because the API route already needs it for its own checks, so
    resolving again would be a second database round-trip per turn.

    Both backends produce the same ``AskResult``, including ``turn_messages`` in
    Anthropic block form, so a thread stays readable and the write gate keeps
    working after the setting is flipped either way. ``client`` is only used by
    the ``api`` backend.

    ``archive_context`` is the rendered ``library.ask.context`` block — who the
    user is and the archive's slug vocabulary — appended to the system prompt on
    both backends. ``None`` (the default, used by tests) answers without it.
    """
    model = settings.ask_model
    result = AskResult(answer="", citations=[], used_tools=[], model=model)
    cited: set[int] = set()
    pages: dict[int, int] = {}
    used: list[str] = []

    history = list(history_messages or [])
    # Documents the write tool is allowed to edit: those surfaced by a read tool
    # earlier in the thread, plus any surfaced this turn (kept in sync below).
    editable_ids: set[int] = _ids_from_history(history)
    # Ids already shown to the user as a write preview earlier in the thread; a
    # confirmed write requires the id to be in here (preview-then-confirm gate).
    previewed_ids: set[int] = _previewed_ids_from_history(history)
    question_content: list[dict[str, Any]] = [{"type": "text", "text": question}]
    for image in images or []:
        question_content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["media_type"],
                    "data": image["data"],
                },
            }
        )
    question_msg: dict[str, Any] = {"role": "user", "content": question_content}

    async def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Bind this turn's mutable state to the shared tool dispatcher.

        ``cited``/``pages`` accumulate what to cite, and ``editable_ids``/
        ``previewed_ids`` carry the write gate. Closing over them means both
        backends drive identical tool semantics.
        """
        return await _dispatch_tool(
            session, settings, name, args, cited, pages, editable_ids, previewed_ids
        )

    if backend == "subscription":
        answer, new_messages = await _run_subscription_turn(
            settings=settings,
            model=model,
            question=question,
            history=history,
            images=images,
            question_msg=question_msg,
            dispatch=dispatch,
            result=result,
            used=used,
            archive_context=archive_context,
        )
    else:
        answer, new_messages = await _run_api_turn(
            settings=settings,
            client=client,
            model=model,
            history=history,
            question_msg=question_msg,
            dispatch=dispatch,
            result=result,
            used=used,
            archive_context=archive_context,
        )

    result.answer = answer or _NO_ANSWER
    # Prefer the documents Claude actually cited inline (#id); fall back to the
    # full retrieved set when the answer cited none explicitly.
    #
    # The fallback exists for a real case: an answer that names its sources in
    # prose rather than with the [#id] syntax. It must NOT fire for the
    # no-answer sentinel, because `cited` holds every candidate a read tool
    # surfaced — including the ones the model read and rejected. Falling back
    # there attaches a full source list to "I couldn't find an answer", which
    # reads as evidence for a non-answer.
    mentioned = {int(match) for match in re.findall(r"#(\d+)", answer)} & cited
    fallback: set[int] = set() if result.answer == _NO_ANSWER else cited
    result.citations = await _citations_for(session, mentioned or fallback, pages)
    # De-duplicate tool names, preserving first-use order.
    result.used_tools = list(dict.fromkeys(used))
    result.turn_messages = new_messages
    return result
