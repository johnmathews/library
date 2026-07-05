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
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from anthropic import AsyncAnthropic
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.documents_service import apply_document_update, revalidate_after_edit
from library.embedding import EmbeddingError, embed_query
from library.extraction.extractor import estimate_cost_usd
from library.models import Document, DocumentComment, DocumentPage
from library.schemas import DocumentUpdate
from library.search import DocumentFilters, semantic_search
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
  who/how-many/how-much/which-over-time questions.
- compare_to_series: compare a recurring bill to its usual values / last year /
  trend (e.g. "is this electricity bill higher than usual?").
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

Rules:
- Answer ONLY from tool results. Never invent facts.
- If the tools return nothing relevant, say plainly that the archive does not
  appear to contain the answer.
- Cite the document id(s) your answer relies on, inline like [#42].
- Be concise and direct. Dutch terms may answer English questions and vice
  versa (e.g. "reiskostenvergoeding" = travel allowance).
"""


def _system_prompt(today: date) -> str:
    """Render the Ask system prompt with concrete dates so the model resolves
    relative references ("last year") against the real current date."""
    return ASK_SYSTEM_PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        year=today.year,
        last_year=today.year - 1,
    )


def _kind_hint() -> str:
    pairs = ", ".join(f"{concept}={slug}" for concept, slug in CONCEPT_TO_KIND.items())
    return f"Concept→kind hints: {pairs}."


TOOLS: list[dict[str, Any]] = [
    {
        "name": "semantic_search",
        "description": (
            "Hybrid full-text + semantic search over document contents. Returns "
            "the most relevant documents with a matching excerpt. Use for "
            "questions about what documents say."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of what to find.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_documents",
        "description": (
            "Aggregate over structured metadata (sender, kind, document_date, "
            "amount_total). Use for who/how-many/how-much/over-time questions. " + _kind_hint()
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
                "kind": {"type": "string", "description": "Kind slug filter, e.g. utility-bill."},
                "sender_contains": {"type": "string", "description": "Substring of sender name."},
                "date_from": {"type": "string", "description": "Inclusive ISO date lower bound."},
                "date_to": {"type": "string", "description": "Inclusive ISO date upper bound."},
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
            "and a year-over-year comparison. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Kind slug, e.g. utility-bill."},
                "sender_contains": {"type": "string", "description": "Substring of sender name."},
                "date_from": {"type": "string", "description": "Inclusive ISO date lower bound."},
                "date_to": {"type": "string", "description": "Inclusive ISO date upper bound."},
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

# Editable metadata fields the write tool forwards to DocumentUpdate. A safe
# subset of DocumentUpdate (no status/review fields) that mirrors the PATCH body.
_WRITABLE_FIELDS: tuple[str, ...] = (
    "title",
    "summary",
    "recipient",
    "sender",
    "kind_slug",
    "tags",
    "projects",
    "document_date",
    "due_date",
    "expiry_date",
    "amount_total",
    "currency",
    "language",
)


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
    cost_usd: float = 0.0
    turn_messages: list[dict[str, Any]] = field(default_factory=list)


def _parse_date(value: object) -> date | None:
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
    hits = await semantic_search(
        session,
        query=query,
        query_embedding=embedding,
        top_k=settings.retrieve_top_k,
        chunks_per_doc=settings.retrieve_chunks_per_doc,
    )
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
    return {"results": rows}


async def _run_query_documents(
    session: AsyncSession, args: dict[str, Any], cited: set[int]
) -> dict[str, Any]:
    filters = DocumentFilters(
        kind_slug=args.get("kind"),
        sender_contains=args.get("sender_contains"),
        date_from=_parse_date(args.get("date_from")),
        date_to=_parse_date(args.get("date_to")),
    )
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
    return result


async def _run_compare_to_series(
    session: AsyncSession, settings: Settings, args: dict[str, Any], cited: set[int]
) -> dict[str, Any]:
    filters = DocumentFilters(
        kind_slug=args.get("kind"),
        sender_contains=args.get("sender_contains"),
        date_from=_parse_date(args.get("date_from")),
        date_to=_parse_date(args.get("date_to")),
    )
    raw_reference = args.get("reference", "latest")
    reference: Decimal | str
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
        result = await _run_query_documents(session, args, cited)
        editable_ids.update(cited)
        return result
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


def _tool_result_payloads(history: list[dict[str, Any]]) -> Iterator[Any]:
    """Yield each decoded ``tool_result`` payload from replayed prior turns."""
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
                yield json.loads(raw)
            except (ValueError, TypeError):
                continue


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
    return {"type": block_type}


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


async def run_ask(
    session: AsyncSession,
    *,
    question: str,
    settings: Settings,
    client: AsyncAnthropic,
    history_messages: list[dict[str, Any]] | None = None,
    images: list[dict[str, str]] | None = None,
) -> AskResult:
    """Answer ``question`` from the archive via a bounded Claude tool-use loop.

    ``history_messages`` is a rehydrated prefix of prior turns (already in block
    form); it is prepended so follow-ups can reason over earlier tool results.
    ``images`` are ``{"media_type", "data"}`` (base64) attachments rendered as
    image content blocks on the question turn for the multimodal model.
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
    messages: list[dict[str, Any]] = [*history, question_msg]
    new_messages: list[dict[str, Any]] = [question_msg]
    _apply_cache_control(messages, len(history))

    system_prompt: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _system_prompt(date.today()),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    answer = ""
    for _ in range(max(1, settings.ask_max_tool_turns)):
        response = await client.messages.create(
            model=model,
            max_tokens=settings.ask_max_answer_tokens,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += estimate_cost_usd(
            model, response.usage.input_tokens, response.usage.output_tokens
        )

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
            if getattr(block, "type", None) != "tool_use":
                continue
            used.append(block.name)
            output = await _dispatch_tool(
                session,
                settings,
                block.name,
                dict(block.input),
                cited,
                pages,
                editable_ids,
                previewed_ids,
            )
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
        answer = answer or "I couldn't find an answer to that in the archive."
        new_messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})

    result.answer = answer or "I couldn't find an answer to that in the archive."
    # Prefer the documents Claude actually cited inline (#id); fall back to the
    # full retrieved set when the answer cited none explicitly.
    mentioned = {int(match) for match in re.findall(r"#(\d+)", answer)} & cited
    result.citations = await _citations_for(session, mentioned or cited, pages)
    # De-duplicate tool names, preserving first-use order.
    result.used_tools = list(dict.fromkeys(used))
    result.turn_messages = new_messages
    return result
