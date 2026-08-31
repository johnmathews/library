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

from library import telemetry
from library.ask import run_ask
from library.ask.context import load_archive_context, render_archive_context
from library.ask.engine import _NO_ANSWER, generate_thread_title
from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.llm.backends import resolve_backend
from library.llm.subscription import SubscriptionBackendError
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
    it used. Requires an Anthropic API key on the ``api`` backend (503
    otherwise); the ``subscription`` backend authenticates through the Claude
    CLI instead. The answer cost is recorded but not budget-gated in this
    release — under a subscription it is notional (see docs/llm-backends.md).
    """
    settings = get_settings()
    ask_backend = await resolve_backend(session, "ask", settings)
    if ask_backend == "api" and settings.anthropic_api_key is None:
        raise HTTPException(
            status_code=503, detail="Ask is unavailable: no Anthropic API key configured."
        )

    if request.thread_id is None:
        thread = AskThread(user_id=user.id, title=_thread_title(request.question))
        session.add(thread)
        await session.flush()
    else:
        # Bound through a separate name so the `is None` check narrows: assigning
        # the Optional straight back into `thread` widens the branch above.
        existing = await session.get(AskThread, request.thread_id)
        if existing is None or existing.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        thread = existing

    # Held as a plain int for the rest of the request. The write tool can roll
    # the session back — an allocated document's `amount_total` edit is refused
    # at COMMIT (`ask/engine.py`, docs/charts.md §10.1) — and a rollback expires
    # every ORM object this request holds, `thread` included. Reading
    # `thread.id` after that is a sync attribute load outside the greenlet
    # context, which raises MissingGreenlet: it would turn a refusal the model
    # has already explained to the user back into the 500 the guard exists to
    # remove.
    thread_id = thread.id

    history = await _history_messages(session, thread_id, settings.ask_history_turns)
    # Who is asking and what the archive calls things — see library.ask.context.
    archive_context = render_archive_context(await load_archive_context(session, user))

    images = [{"media_type": image.media_type, "data": image.data} for image in request.images]
    turn_cost = 0.0
    # The subscription backend authenticates through the Claude CLI and never
    # touches this client, but ``run_ask`` and ``generate_thread_title`` still
    # take one. The placeholder cannot reach the wire: the guard above proves a
    # real key exists whenever the backend is "api", and the subscription path
    # issues no request through it.
    api_key = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key is not None
        else "unused-by-subscription-backend"
    )
    # Instrumented HERE rather than inside `run_ask` because this is where the
    # two backends converge: whatever `ask_backend` is, the result has the same
    # shape by this line, so one call site covers both and they stay comparable.
    async with AsyncAnthropic(api_key=api_key) as client:
        with telemetry.timed() as clock:
            try:
                result = await run_ask(
                    session,
                    question=request.question,
                    settings=settings,
                    client=client,
                    history_messages=history,
                    images=images,
                    backend=ask_backend,
                    archive_context=archive_context,
                )
            except SubscriptionBackendError as exc:
                # 503, not 500: this is a configuration/credential problem an
                # operator can fix, not a bug. Without this the caller gets a bare
                # "Internal Server Error" and the actual reason — which names the
                # command to run — stays buried in the container log.
                telemetry.record_error(backend=ask_backend, kind="subscription_auth")
                logger.warning("Ask failed on the subscription backend: %s", exc)
                raise HTTPException(status_code=503, detail=f"Ask is unavailable: {exc}") from exc
            except Exception:
                # Anything else is a real fault. Recorded before re-raising so a
                # spike in 500s is visible as a metric and not only in the logs.
                telemetry.record_error(backend=ask_backend, kind="upstream")
                raise

        telemetry.record_tokens(
            backend=ask_backend,
            model=result.model,
            fresh=result.fresh_input_tokens,
            cache_read=result.cache_read_tokens,
            cache_write=result.cache_write_tokens,
            output=result.output_tokens,
        )
        telemetry.record_turn(
            backend=ask_backend,
            model=result.model,
            duration_s=clock["elapsed"],
            tool_calls=len(result.used_tools),
            citations=len(result.citations),
            # The engine substitutes a fixed apology when the tool loop runs out
            # of turns without an answer. Distinguishing that from a real answer
            # is the whole point of raising the ceiling from 4 to 8 — without it
            # the metric cannot show whether the new headroom is enough.
            outcome="no_answer" if result.answer == _NO_ANSWER else "ok",
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
                    settings=settings,
                    backend=ask_backend,
                )
                if title.title:
                    thread.title = title.title
                turn_cost += title.cost_usd
            except Exception:
                logger.warning(
                    "Ask thread title generation failed; keeping placeholder title",
                    exc_info=True,
                )

    # After the title call, so the recorded cost matches `ask_turns.cost_usd`
    # exactly. Two numbers that are supposed to be the same thing but are
    # computed at different points is how they drift.
    telemetry.record_cost(backend=ask_backend, model=result.model, usd=turn_cost)

    session.add(
        AskTurn(
            thread_id=thread_id,
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
        thread_id=thread_id,
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
