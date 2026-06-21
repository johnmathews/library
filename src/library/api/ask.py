"""Natural-language /ask endpoint: answer questions about the archive.

Runs the agentic tool-use loop in ``library.ask`` (Claude orchestrating
semantic + structured retrieval) and records each ask's cost in ``ask_turns``.
Authentication is enforced at include level in app.py.
"""

from typing import Annotated, Any

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.ask import run_ask
from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.models import AskThread, AskTurn, User

router: APIRouter = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    """Body of POST /api/ask."""

    question: str = Field(min_length=1, max_length=1000, description="The question to answer.")
    thread_id: int | None = Field(default=None, description="Continue an existing conversation.")


class Citation(BaseModel):
    """A document the answer relies on."""

    document_id: int
    title: str | None
    page_number: int | None = None


class AskResponse(BaseModel):
    """The answer, its citations, the tools used, and the answer cost."""

    answer: str
    citations: list[Citation]
    used_tools: list[str]
    cost_usd: float
    thread_id: int


def _thread_title(question: str) -> str:
    return question.strip()[:120]


async def _history_messages(
    session: AsyncSession, thread_id: int, turns: int
) -> list[dict[str, Any]]:
    """The last ``turns`` turns' message blocks, chronological, flattened."""
    if turns <= 0:
        return []
    rows = (
        (
            await session.execute(
                select(AskTurn.messages)
                .where(AskTurn.thread_id == thread_id)
                .order_by(AskTurn.created_at.desc(), AskTurn.id.desc())
                .limit(turns)
            )
        )
        .scalars()
        .all()
    )
    history: list[dict[str, Any]] = []
    for turn_messages in reversed(rows):
        history.extend(turn_messages)
    return history


@router.post("/ask", response_model=AskResponse, summary="Ask a question about your documents")
async def ask(
    request: AskRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AskResponse:
    """Answer a natural-language question from the document archive.

    Returns a prose answer grounded in retrieved documents plus the citations
    it used. Requires an Anthropic API key (503 otherwise). The answer cost is
    recorded but not budget-gated in this release.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise HTTPException(
            status_code=503, detail="Ask is unavailable: no Anthropic API key configured."
        )

    if request.thread_id is None:
        thread = AskThread(user_id=user.id, title=_thread_title(request.question))
        session.add(thread)
        await session.flush()
    else:
        thread = await session.get(AskThread, request.thread_id)
        if thread is None or thread.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found.")

    history = await _history_messages(session, thread.id, settings.ask_history_turns)

    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
        result = await run_ask(
            session,
            question=request.question,
            settings=settings,
            client=client,
            history_messages=history,
        )

    session.add(
        AskTurn(
            thread_id=thread.id,
            query=request.question,
            answer=result.answer,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            used_tools={"tools": result.used_tools},
            citations=[
                {"document_id": c.document_id, "title": c.title, "page_number": c.page_number}
                for c in result.citations
            ],
            messages=result.turn_messages,
        )
    )
    thread.updated_at = func.now()
    await session.commit()

    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(document_id=c.document_id, title=c.title, page_number=c.page_number)
            for c in result.citations
        ],
        used_tools=result.used_tools,
        cost_usd=result.cost_usd,
        thread_id=thread.id,
    )
