"""Natural-language /ask endpoint: answer questions about the archive.

Runs the agentic tool-use loop in ``library.ask`` (Claude orchestrating
semantic + structured retrieval) and records each ask's cost in ``ask_turns``.
Authentication is enforced at include level in app.py.
"""

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.ask import run_ask
from library.ask.engine import generate_thread_title
from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.models import AskThread, AskTurn, User

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["ask"])

# Cap attachments per question — bounds the request body and the multimodal
# token cost. The media types are the ones the Anthropic vision API accepts.
MAX_ASK_IMAGES = 5
# Per-image base64 ceiling (~15 MB decoded). Bounds memory before the bytes ever
# reach the model, in addition to any upstream proxy body limit.
MAX_ASK_IMAGE_BASE64 = 20_000_000
AskImageMediaType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class AskImage(BaseModel):
    """A base64 image attached to a question (no ``data:`` prefix)."""

    media_type: AskImageMediaType
    data: str = Field(
        min_length=1, max_length=MAX_ASK_IMAGE_BASE64, description="Base64-encoded image bytes."
    )


class AskRequest(BaseModel):
    """Body of POST /api/ask."""

    question: str = Field(min_length=1, max_length=1000, description="The question to answer.")
    thread_id: int | None = Field(default=None, description="Continue an existing conversation.")
    images: list[AskImage] = Field(
        default_factory=list,
        max_length=MAX_ASK_IMAGES,
        description="Optional image attachments for the multimodal model.",
    )


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


class ThreadSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    total_cost_usd: float


class TurnView(BaseModel):
    id: int
    query: str
    answer: str
    citations: list[Citation]
    used_tools: list[str]
    cost_usd: float
    created_at: datetime


class ThreadDetail(BaseModel):
    id: int
    title: str
    turns: list[TurnView]


class ThreadRenameRequest(BaseModel):
    """Body of PATCH /api/ask/threads/{id} — a user-supplied conversation title."""

    title: str = Field(min_length=1, max_length=120, description="New conversation title.")


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

    images = [{"media_type": image.media_type, "data": image.data} for image in request.images]
    turn_cost = 0.0
    async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
        result = await run_ask(
            session,
            question=request.question,
            settings=settings,
            client=client,
            history_messages=history,
            images=images,
        )
        turn_cost = result.cost_usd
        # A brand-new thread was seeded with the truncated question as a
        # placeholder title. Upgrade it to a concise generated title from the
        # first exchange. This must never fail or block the answer, which is
        # already rendered: on any error we keep the placeholder and move on.
        if request.thread_id is None:
            try:
                title = await generate_thread_title(
                    client,
                    model=settings.ask_title_model,
                    question=request.question,
                    answer=result.answer,
                )
                if title.title:
                    thread.title = title.title
                turn_cost += title.cost_usd
            except Exception:
                logger.warning(
                    "Ask thread title generation failed; keeping placeholder title",
                    exc_info=True,
                )

    session.add(
        AskTurn(
            thread_id=thread.id,
            query=request.question,
            answer=result.answer,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=turn_cost,
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
        cost_usd=turn_cost,
        thread_id=thread.id,
    )


@router.get("/ask/threads", response_model=list[ThreadSummary], summary="List Ask conversations")
async def list_threads(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ThreadSummary]:
    rows = (
        await session.execute(
            select(
                AskThread.id,
                AskThread.title,
                AskThread.created_at,
                AskThread.updated_at,
                func.count(AskTurn.id),
                func.coalesce(func.sum(AskTurn.cost_usd), 0.0),
            )
            .outerjoin(AskTurn, AskTurn.thread_id == AskThread.id)
            .where(AskThread.user_id == user.id)
            .group_by(AskThread.id)
            .order_by(AskThread.updated_at.desc())
        )
    ).all()
    return [
        ThreadSummary(
            id=tid,
            title=title,
            created_at=created,
            updated_at=updated,
            turn_count=count,
            total_cost_usd=float(cost),
        )
        for tid, title, created, updated, count, cost in rows
    ]


async def _owned_thread(session: AsyncSession, thread_id: int, user: User) -> AskThread:
    thread: AskThread | None = await session.get(AskThread, thread_id)
    if thread is None or thread.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return thread


@router.get("/ask/threads/{thread_id}", response_model=ThreadDetail, summary="Get one conversation")
async def get_thread(
    thread_id: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThreadDetail:
    thread: AskThread = await _owned_thread(session, thread_id, user)
    turns = (
        (
            await session.execute(
                select(AskTurn)
                .where(AskTurn.thread_id == thread_id)
                .order_by(AskTurn.created_at, AskTurn.id)
            )
        )
        .scalars()
        .all()
    )
    return ThreadDetail(
        id=thread.id,
        title=thread.title,
        turns=[
            TurnView(
                id=t.id,
                query=t.query,
                answer=t.answer,
                citations=[Citation(**c) for c in t.citations],
                used_tools=list(t.used_tools.get("tools", [])),
                cost_usd=t.cost_usd,
                created_at=t.created_at,
            )
            for t in turns
        ],
    )


@router.patch(
    "/ask/threads/{thread_id}", response_model=ThreadSummary, summary="Rename a conversation"
)
async def rename_thread(
    thread_id: int,
    request: ThreadRenameRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThreadSummary:
    """Set a user-chosen title on an owned conversation, overriding the
    auto-generated one. Rejects a blank (whitespace-only) title."""
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be blank.")
    thread: AskThread = await _owned_thread(session, thread_id, user)
    thread.title = title
    await session.commit()

    # Build the summary from a fresh query rather than the just-committed ORM
    # object. The aggregates (turn_count / total_cost_usd) need a query anyway,
    # and it sidesteps the onupdate trap: the sessionmaker keeps attributes on
    # commit (expire_on_commit=False), but `updated_at` (server-side
    # onupdate=func.now()) is still expired to fetch its new value, so reading it
    # off the ORM object would fault with a lazy reload in a sync context
    # (MissingGreenlet).
    tid, new_title, created, updated, count, cost = (
        await session.execute(
            select(
                AskThread.id,
                AskThread.title,
                AskThread.created_at,
                AskThread.updated_at,
                func.count(AskTurn.id),
                func.coalesce(func.sum(AskTurn.cost_usd), 0.0),
            )
            .outerjoin(AskTurn, AskTurn.thread_id == AskThread.id)
            .where(AskThread.id == thread_id)
            .group_by(AskThread.id)
        )
    ).one()
    return ThreadSummary(
        id=tid,
        title=new_title,
        created_at=created,
        updated_at=updated,
        turn_count=count,
        total_cost_usd=float(cost),
    )


@router.delete("/ask/threads/{thread_id}", status_code=204, summary="Delete a conversation")
async def delete_thread(
    thread_id: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    thread: AskThread = await _owned_thread(session, thread_id, user)
    await session.delete(thread)
    await session.commit()
