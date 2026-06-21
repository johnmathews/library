"""Agentic /ask: Claude orchestrates retrieval tools to answer with citations.

Claude is given two tools — ``semantic_search`` (hybrid content retrieval) and
``query_documents`` (structured aggregation over metadata) — and decides which
to call for a question. It must answer only from tool results and cite the
document ids it used. The loop is bounded (``ask_max_tool_turns``); the
embedding and answer cost is summed for the audit log.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.embedding import EmbeddingError, embed_query
from library.extraction.extractor import estimate_cost_usd
from library.models import Document
from library.search import DocumentFilters, semantic_search
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
                        "sum_amount: total amounts. list: matching documents."
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
]


@dataclass(frozen=True, slots=True)
class AskCitation:
    """A document the answer relies on."""

    document_id: int
    title: str | None
    page_number: int | None = None


@dataclass(slots=True)
class AskResult:
    """The answer plus citations, tools used, and cost."""

    answer: str
    citations: list[AskCitation]
    used_tools: list[str]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


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
        session, query=query, query_embedding=embedding, top_k=settings.retrieve_top_k
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
                "document_date": (
                    hit.document.document_date.isoformat() if hit.document.document_date else None
                ),
                "excerpt": hit.chunk_text,
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


async def _dispatch_tool(
    session: AsyncSession,
    settings: Settings,
    name: str,
    args: dict[str, Any],
    cited: set[int],
    pages: dict[int, int],
) -> dict[str, Any]:
    if name == "semantic_search":
        return await _run_semantic_search(session, settings, args, cited, pages)
    if name == "query_documents":
        return await _run_query_documents(session, args, cited)
    return {"error": f"unknown tool {name}"}


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


async def run_ask(
    session: AsyncSession,
    *,
    question: str,
    settings: Settings,
    client: AsyncAnthropic,
) -> AskResult:
    """Answer ``question`` from the archive via a bounded Claude tool-use loop."""
    model = settings.ask_model
    result = AskResult(answer="", citations=[], used_tools=[], model=model)
    cited: set[int] = set()
    pages: dict[int, int] = {}
    used: list[str] = []
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    system_prompt = _system_prompt(date.today())

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

        if response.stop_reason != "tool_use":
            answer = _text_of(response.content)
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            used.append(block.name)
            output = await _dispatch_tool(
                session, settings, block.name, dict(block.input), cited, pages
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
            break
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
    else:
        logger.info("ask hit the tool-turn limit without a final answer")

    result.answer = answer or "I couldn't find an answer to that in the archive."
    # Prefer the documents Claude actually cited inline (#id); fall back to the
    # full retrieved set when the answer cited none explicitly.
    mentioned = {int(match) for match in re.findall(r"#(\d+)", answer)} & cited
    result.citations = await _citations_for(session, mentioned or cited, pages)
    # De-duplicate tool names, preserving first-use order.
    result.used_tools = list(dict.fromkeys(used))
    return result
