"""Natural-language /ask endpoint: answer questions about the archive.

Runs the agentic tool-use loop in ``library.ask`` (Claude orchestrating
semantic + structured retrieval) and records each ask's cost in ``ask_logs``.
Authentication is enforced at include level in app.py.
"""

from typing import Annotated

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from library.ask import run_ask
from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.models import AskLog, User

router: APIRouter = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    """Body of POST /api/ask."""

    question: str = Field(min_length=1, max_length=1000, description="The question to answer.")


class Citation(BaseModel):
    """A document the answer relies on."""

    document_id: int
    title: str | None


class AskResponse(BaseModel):
    """The answer, its citations, the tools used, and the answer cost."""

    answer: str
    citations: list[Citation]
    used_tools: list[str]
    cost_usd: float


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

    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
        result = await run_ask(session, question=request.question, settings=settings, client=client)

    session.add(
        AskLog(
            user_id=user.id,
            query=request.question,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            used_tools={"tools": result.used_tools},
        )
    )
    await session.commit()

    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(document_id=citation.document_id, title=citation.title)
            for citation in result.citations
        ],
        used_tools=result.used_tools,
        cost_usd=result.cost_usd,
    )
