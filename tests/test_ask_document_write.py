"""Tests for the Ask agent's document-metadata write path.

Covers the reusable ``apply_document_update`` service and the
propose-then-confirm ``update_document_metadata`` tool in the Ask engine
(preview vs commit, the conversation-scope guardrail, and an engine-level
dispatch driven by a stubbed Anthropic client).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from procrastinate.testing import InMemoryConnector
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.ask import engine as ask_engine
from library.ask.engine import _run_update_document, run_ask
from library.config import get_settings
from library.documents_service import apply_document_update
from library.models import Document, IngestionEvent, ReviewStatus
from library.schemas import DocumentUpdate
from library.spend_lines import LineInput, replace_lines
from tests.conftest import DocumentFactory
from tests.test_api_ask import _FakeAnthropic, _Response, _TextBlock, _ToolUseBlock, _Usage
from tests.test_documents_api import _seed_document

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _open_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _load(session: AsyncSession, document_id: int) -> Document:
    document = await session.get(Document, document_id)
    assert document is not None
    return document


async def _events(session: AsyncSession, document_id: int) -> list[IngestionEvent]:
    rows = await session.execute(
        select(IngestionEvent)
        .where(IngestionEvent.document_id == document_id)
        .order_by(IngestionEvent.id)
    )
    return list(rows.scalars().all())


# --- apply_document_update service ------------------------------------------


@pytest.mark.asyncio
async def test_apply_document_update_upserts_recipient_replaces_tags(
    api_database_url: str,
) -> None:
    document_id = await _seed_document(
        api_database_url,
        "askw-service",
        tag_slugs=["askw-svc-old"],
        title="Before",
    )

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        edited = await apply_document_update(
            session,
            document,
            DocumentUpdate(recipient="Askw Recipient", tags=["askw-svc-new"]),
            edited_by="user",
        )
        await session.commit()

    assert set(edited) == {"recipient_id", "tags"}

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.recipient is not None
        assert document.recipient.name == "Askw Recipient"
        assert sorted(tag.slug for tag in document.tags) == ["askw-svc-new"]
        assert set(document.extra["user_edited_fields"]) == {"recipient_id", "tags"}
        events = await _events(session, document_id)
        user_edited = [event for event in events if event.event == "user_edited"]
        assert len(user_edited) == 1
        assert user_edited[0].detail["edited_by"] == "user"
        assert set(user_edited[0].detail["fields"]) == {"recipient_id", "tags"}


# --- Write tool: preview / commit / guardrail -------------------------------


@pytest.mark.asyncio
async def test_update_tool_preview_does_not_write(api_database_url: str) -> None:
    document_id = await _seed_document(api_database_url, "askw-preview", title="Original title")

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        previewed: set[int] = set()
        result = await _run_update_document(
            session,
            get_settings(),
            {"document_id": document_id, "title": "Proposed title", "confirmed": False},
            {document_id},
            previewed,
        )

    assert result["status"] == "preview"
    assert result["changes"]["title"] == {"current": "Original title", "proposed": "Proposed title"}
    # A preview must NOT authorise a same-turn confirm — the id is only trusted
    # once it reaches thread history (a later user turn). So previewed stays empty.
    assert document_id not in previewed

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "Original title"
        assert (await _events(session, document_id)) == []


@pytest.mark.asyncio
async def test_update_tool_commit_writes_with_ask_provenance(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
    document_id = await _seed_document(api_database_url, "askw-commit", title="Original title")

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        result = await _run_update_document(
            session,
            get_settings(),
            {"document_id": document_id, "title": "Confirmed title", "confirmed": True},
            {document_id},
            {document_id},  # previewed earlier in the thread
        )

    assert result["status"] == "updated"
    assert result["updated_fields"] == ["title"]

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "Confirmed title"
        assert "title" in document.extra["user_edited_fields"]
        user_edited = [e for e in await _events(session, document_id) if e.event == "user_edited"]
        assert len(user_edited) == 1
        assert user_edited[0].detail["edited_by"] == "ask"


@pytest.mark.asyncio
async def test_update_tool_writes_matters(api_database_url: str) -> None:
    """A confirmed Ask write of `matters` applies and persists.

    The end-to-end version of this unit: `matters` was absent from the writable
    set, so the key was dropped on the way to DocumentUpdate and the tool still
    reported `status: updated` with an empty `updated_fields` — a write that
    looked like it worked. Also checks the preview renders slugs rather than ORM
    reprs, since that is what the user is asked to approve.
    """
    document_id = await _seed_document(
        api_database_url,
        "askw-matters",
        matter_slugs=["askw-matter-before"],
    )

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert ask_engine._preview_current(document, "matters") == ["askw-matter-before"]
        result = await _run_update_document(
            session,
            get_settings(),
            {
                "document_id": document_id,
                "matters": ["askw-matter-after"],
                "confirmed": True,
            },
            {document_id},
            {document_id},  # previewed earlier in the thread
        )

    assert result["status"] == "updated"
    assert result["updated_fields"] == ["matters"]

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert sorted(matter.slug for matter in document.matters) == ["askw-matter-after"]
        user_edited = [e for e in await _events(session, document_id) if e.event == "user_edited"]
        assert len(user_edited) == 1
        assert user_edited[0].detail["edited_by"] == "ask"


@pytest.mark.asyncio
async def test_update_tool_revalidates_and_clears_finding(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
    """An Ask-confirmed edit recomputes validation just like the PATCH route, so
    fixing a flagged field clears its warning and review_status."""
    document_id = await _seed_document(
        api_database_url,
        "askw-reval",
        kind_slug="invoice",
        title="Invoice",
        # Real text: an invoice with no ocr_text would legitimately fire
        # no_text_extracted and stay in review, which this test is not about.
        ocr_text="Factuur 12-03-2041 totaal EUR 10,00",
        document_date=date(2041, 3, 12),  # future -> date_plausibility fires
        review_status=ReviewStatus.NEEDS_REVIEW,
        extra={
            "validation": {
                "findings": [
                    {
                        "rule": "date_plausibility",
                        "field": "document_date",
                        "severity": "warn",
                        "message": "document_date is in the future",
                    }
                ]
            }
        },
    )

    async with _open_session(api_database_url) as session:
        await _load(session, document_id)
        result = await _run_update_document(
            session,
            get_settings(),
            {"document_id": document_id, "document_date": "2024-03-12", "confirmed": True},
            {document_id},
            {document_id},
        )

    assert result["status"] == "updated"

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.review_status != ReviewStatus.NEEDS_REVIEW
        rules = [f["rule"] for f in document.extra["validation"]["findings"]]
        assert "date_plausibility" not in rules


@pytest.mark.asyncio
async def test_update_tool_guardrail_rejects_unsurfaced_document(
    api_database_url: str,
) -> None:
    document_id = await _seed_document(api_database_url, "askw-guardrail", title="Untouched")

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        # editable_ids does NOT contain document_id -> refuse, even with confirmed.
        result = await _run_update_document(
            session,
            get_settings(),
            {"document_id": document_id, "title": "Hacked", "confirmed": True},
            {document_id + 9999},
            {document_id},
        )

    assert "error" in result
    assert result["error"] == "can only edit documents found in this conversation"

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "Untouched"
        assert (await _events(session, document_id)) == []


@pytest.mark.asyncio
async def test_update_tool_confirm_without_preview_is_refused(api_database_url: str) -> None:
    """A confirmed write for a document that was never previewed is rejected, so
    the propose-then-confirm gate holds in code even if the model skips ahead."""
    document_id = await _seed_document(api_database_url, "askw-nopreview", title="Untouched")

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        # Surfaced (editable) but NOT previewed.
        result = await _run_update_document(
            session,
            get_settings(),
            {"document_id": document_id, "title": "Sneaky", "confirmed": True},
            {document_id},
            set(),
        )

    assert "error" in result
    assert "preview" in result["error"].lower()

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "Untouched"
        assert (await _events(session, document_id)) == []


@pytest.mark.asyncio
async def test_update_tool_refuses_an_allocated_documents_amount_edit(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """Ask is the fifth `amount_total` writer, and must translate 0035's refusal.

    A document whose spend lines are allocated against its current amount cannot
    have that amount changed: the mirror trigger refuses it, and because the
    trigger is DEFERRABLE INITIALLY DEFERRED the refusal arrives at COMMIT — as a
    bare `DBAPIError` under asyncpg — rather than at the UPDATE. Unguarded that
    escapes the whole Ask turn as a 500 with a poisoned session; guarded it is a
    refusal the owner can act on.

    So this drives the tool all the way through its own `commit`: a test that
    stopped at a flush, or asserted against an already rolled-back session, would
    pass against no guard and no trigger at all. `confirmed=true` **and** the id
    in `previewed_ids` are both required to reach that commit; without either the
    tool returns a preview or a "preview required first" error and the trigger is
    never touched.
    """
    # Invented amounts — this repository is public.
    doc = await document(amount_total=Decimal("100.00"))
    # Held as a plain int: the refusal rolls the session back, which expires
    # every ORM attribute, and reading `doc.id` afterwards would go to the
    # database on a sync attribute access and raise MissingGreenlet instead of
    # asserting anything.
    document_id = doc.id
    await replace_lines(
        session,
        document_id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await session.commit()

    result = await _run_update_document(
        session,
        get_settings(),
        {"document_id": document_id, "amount_total": "250.00", "confirmed": True},
        {document_id},
        {document_id},  # previewed earlier in the thread
    )

    assert "error" in result, f"expected a refusal, got {result!r}"
    assert "spend lines" in result["error"]
    # The session survived the refusal — a poisoned session is the actual damage
    # the unguarded 500 does — and the rejected edit did not land.
    still = await session.scalar(
        text("SELECT amount_total FROM documents WHERE id = :d"), {"d": document_id}
    )
    assert still == Decimal("100.00"), "the refused edit must not have landed"


# --- Engine-level dispatch --------------------------------------------------


def _surfaced_history(document_id: int) -> list[dict[str, Any]]:
    """History where a read tool surfaced document_id (makes it editable)."""
    return [
        {"role": "user", "content": [{"type": "text", "text": "find it"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "h1", "name": "semantic_search", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "h1",
                    "content": f'{{"results": [{{"document_id": {document_id}}}]}}',
                }
            ],
        },
    ]


@pytest.mark.asyncio
async def test_engine_confirm_after_prior_turn_preview_writes(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
    """A confirmed write succeeds when a PRIOR turn previewed the document (the
    preview tool_result is in the replayed history). This is the real
    cross-turn propose-then-confirm path."""
    document_id = await _seed_document(api_database_url, "askw-engine-ok", title="Old")

    history = _surfaced_history(document_id)
    # A prior turn already previewed this document — recorded in history.
    history += [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "p1", "name": "update_document_metadata", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "p1",
                    "content": f'{{"status": "preview", "document_id": {document_id}}}',
                }
            ],
        },
    ]

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="update_document_metadata",
                        input={"document_id": document_id, "title": "New", "confirmed": True},
                        id="w1",
                    )
                ],
                usage=_Usage(10, 5),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Updated [#{document_id}].")],
                usage=_Usage(6, 3),
            ),
        ]
    )

    settings = get_settings()
    async with _open_session(api_database_url) as session:
        result = await run_ask(
            session,
            question="yes, do it",
            settings=settings,
            client=cast(Any, client),
            history_messages=history,
        )

    assert "update_document_metadata" in result.used_tools

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "New"
        user_edited = [e for e in await _events(session, document_id) if e.event == "user_edited"]
        assert len(user_edited) == 1
        assert user_edited[0].detail["edited_by"] == "ask"


@pytest.mark.asyncio
async def test_engine_confirmed_header_field_edit_defers_a_reembed(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
    """The Ask write tool's re-embed hook (``ask/engine.py``, beside the
    ``header_fields_changed`` check after ``session.commit()``): editing a
    header field (``title``) through a confirmed ``update_document_metadata``
    call must defer exactly one ``embed_document`` job for the edited
    document, same as the PATCH route (``test_editing_a_header_field_defers_a_
    reembed`` in ``tests/test_chunk_context_header.py``). That route's hook is
    covered by a test; this one, proven by mutation, was not — deleting the
    two-line hook in ``ask/engine.py`` left the whole suite green."""
    document_id = await _seed_document(api_database_url, "askw-engine-reembed", title="Old")

    history = _surfaced_history(document_id)
    history += [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "p1", "name": "update_document_metadata", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "p1",
                    "content": f'{{"status": "preview", "document_id": {document_id}}}',
                }
            ],
        },
    ]

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="update_document_metadata",
                        input={"document_id": document_id, "title": "New", "confirmed": True},
                        id="w1",
                    )
                ],
                usage=_Usage(10, 5),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Updated [#{document_id}].")],
                usage=_Usage(6, 3),
            ),
        ]
    )

    settings = get_settings()
    async with _open_session(api_database_url) as session:
        await run_ask(
            session,
            question="yes, do it",
            settings=settings,
            client=cast(Any, client),
            history_messages=history,
        )

    embed_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.embed_document"
        and job["args"] == {"document_id": document_id}
    ]
    assert len(embed_jobs) == 1


@pytest.mark.asyncio
async def test_engine_confirmed_non_header_field_edit_defers_nothing(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
    """Companion to the test above: a confirmed edit that touches no header
    field (``summary``) must defer no ``embed_document`` job at all."""
    document_id = await _seed_document(api_database_url, "askw-engine-noembed", title="Old")

    history = _surfaced_history(document_id)
    history += [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "p1", "name": "update_document_metadata", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "p1",
                    "content": f'{{"status": "preview", "document_id": {document_id}}}',
                }
            ],
        },
    ]

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="update_document_metadata",
                        input={
                            "document_id": document_id,
                            "summary": "New summary",
                            "confirmed": True,
                        },
                        id="w1",
                    )
                ],
                usage=_Usage(10, 5),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text=f"Updated [#{document_id}].")],
                usage=_Usage(6, 3),
            ),
        ]
    )

    settings = get_settings()
    async with _open_session(api_database_url) as session:
        await run_ask(
            session,
            question="yes, do it",
            settings=settings,
            client=cast(Any, client),
            history_messages=history,
        )

    embed_jobs = [
        job
        for job in job_connector.jobs.values()
        if job["task_name"] == "library.jobs.embed_document"
        and job["args"] == {"document_id": document_id}
    ]
    assert embed_jobs == []


@pytest.mark.asyncio
async def test_engine_same_turn_preview_then_confirm_is_refused(
    api_database_url: str,
) -> None:
    """The gate must hold even if the model tries to preview AND confirm within a
    single turn (the user never saw the proposal). The confirm is refused and the
    document is not changed."""
    document_id = await _seed_document(api_database_url, "askw-engine-bypass", title="Untouched")

    client = _FakeAnthropic(
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="update_document_metadata",
                        input={"document_id": document_id, "title": "Sneaky", "confirmed": False},
                        id="w0",
                    ),
                    _ToolUseBlock(
                        name="update_document_metadata",
                        input={"document_id": document_id, "title": "Sneaky", "confirmed": True},
                        id="w1",
                    ),
                ],
                usage=_Usage(10, 5),
            ),
            _Response(
                stop_reason="end_turn",
                content=[_TextBlock(text="Done.")],
                usage=_Usage(6, 3),
            ),
        ]
    )

    settings = get_settings()
    async with _open_session(api_database_url) as session:
        await run_ask(
            session,
            question="change it and confirm right away",
            settings=settings,
            client=cast(Any, client),
            history_messages=_surfaced_history(document_id),
        )

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        assert document.title == "Untouched"
        assert (await _events(session, document_id)) == []


def test_write_tool_registered_in_tools() -> None:
    names = {tool["name"] for tool in ask_engine.TOOLS}
    assert "update_document_metadata" in names


def _write_tool_schema() -> dict[str, Any]:
    (tool,) = [t for t in ask_engine.TOOLS if t["name"] == "update_document_metadata"]
    return cast(dict[str, Any], tool["input_schema"])


def test_writable_fields_match_document_update() -> None:
    """The writable set is derived from DocumentUpdate, not restated.

    docs/ask.md documents this tool as "the same surface as
    PATCH /api/documents/{id}", so DocumentUpdate is the specification. The
    hand-written copy this replaced had drifted — `matters` was absent, so an
    Ask write of it was dropped and still reported as a success.
    """
    assert set(ask_engine._WRITABLE_FIELDS) == set(DocumentUpdate.model_fields)
    assert "matters" in ask_engine._WRITABLE_FIELDS


def test_write_tool_schema_declares_every_writable_field() -> None:
    """The model can never emit a field the tool schema does not advertise.

    This is the half that cannot be derived — each property carries a
    hand-authored description — so it needs its own guard. A field present in
    _WRITABLE_FIELDS but missing from the schema is unreachable: the model has no
    way to know it exists, which is how `matters` stayed broken even though the
    forwarding code would have accepted it.
    """
    declared = set(_write_tool_schema()["properties"])
    # document_id and confirmed are protocol fields, not document metadata.
    writable = set(ask_engine._WRITABLE_FIELDS)
    missing = writable - declared
    assert missing == set(), f"writable but undeclared in the tool schema: {sorted(missing)}"
    # And nothing is advertised that would be silently dropped on the way out.
    stray = declared - writable - {"document_id", "confirmed"}
    assert stray == set(), f"declared in the schema but not writable: {sorted(stray)}"


async def test_preview_current_is_json_primitive_for_every_writable_field(
    api_database_url: str,
) -> None:
    """Every writable field must preview as something JSON can render.

    Tool output is serialised with ``json.dumps(..., default=str)``, so a
    relationship returned as an ORM object does not raise — it renders as
    "<Matter object at 0x...>" in the very preview the user is asked to approve.
    Guards any future relationship field, not just `matters`.
    """
    document_id = await _seed_document(
        api_database_url,
        "askw-preview-primitives",
        kind_slug="invoice",
        title="Invoice",
        ocr_text="Factuur",
        tag_slugs=["askw-preview"],
        project_slugs=["askw-preview-project"],
        matter_slugs=["askw-preview-matter"],
        document_date=date(2026, 1, 2),
    )

    async with _open_session(api_database_url) as session:
        document = await _load(session, document_id)
        for field in ask_engine._WRITABLE_FIELDS:
            value = ask_engine._preview_current(document, field)
            assert _is_json_primitive(value), (
                f"{field} previews as {type(value).__name__} ({value!r}); "
                "a relationship needs a branch in _preview_current"
            )


def _is_json_primitive(value: Any) -> bool:
    """True when json.dumps renders this without falling back to default=str."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_primitive(item) for item in value)
    # Decimal and date are deliberately allowed: default=str renders them
    # correctly and readably ("12.00", "2026-01-02"), unlike an ORM object.
    return isinstance(value, Decimal | date)


# --- tool_result history decoding: both backends' shapes -------------------
#
# The write gate (editable_ids / previewed_ids) is only as good as the history
# it's read from. The `api` backend's tool_result.content is a single
# JSON-encoded string. The `subscription` backend — the production default —
# double-wraps it: content is a JSON-encoded LIST of SDK content blocks, whose
# inner "text" holds the real payload. Before the fix, `_tool_result_payloads`
# only undid one layer, so on the subscription backend both `_ids_from_history`
# and `_previewed_ids_from_history` silently returned `set()`: previews worked,
# but a confirmed write could never find its own preview, and a document
# surfaced by a read tool in an earlier turn could never be edited.


def _api_shaped_tool_result(tool_use_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A tool_result block shaped as the `api` backend stores it (`_run_api_turn`):
    content is `json.dumps(output, default=str)` directly — one level of JSON."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(payload),
    }


def _subscription_shaped_tool_result(tool_use_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A tool_result block shaped as the `subscription` backend stores it
    (`llm/subscription.py`): the SDK's tool result content is itself a list of
    content blocks (`[{"type": "text", "text": ...}]`), and since that list
    isn't a `str` it gets `json.dumps`-ed again on top — two levels of JSON."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps([{"type": "text", "text": json.dumps(payload)}]),
    }


def test_history_decoding_finds_ids_in_api_shaped_history() -> None:
    """Regression guard for the working path: the `api` backend's single-wrapped
    shape must keep decoding correctly after the subscription-shape fix."""
    document_id = 42
    history = [
        {
            "role": "user",
            "content": [
                _api_shaped_tool_result("a1", {"document_id": document_id}),
                _api_shaped_tool_result("a2", {"status": "preview", "document_id": document_id}),
            ],
        }
    ]

    assert ask_engine._ids_from_history(history) == {document_id}
    assert ask_engine._previewed_ids_from_history(history) == {document_id}


def test_history_decoding_finds_ids_in_subscription_shaped_history() -> None:
    """The bug: on subscription-shaped (double-wrapped) history, both helpers
    must still find the document id — this is what was silently broken in
    production (both returned `set()` before the fix)."""
    document_id = 42
    history = [
        {
            "role": "user",
            "content": [
                _subscription_shaped_tool_result("s1", {"document_id": document_id}),
                _subscription_shaped_tool_result(
                    "s2", {"status": "preview", "document_id": document_id}
                ),
            ],
        }
    ]

    assert ask_engine._ids_from_history(history) == {document_id}
    assert ask_engine._previewed_ids_from_history(history) == {document_id}


def test_history_decoding_tolerates_malformed_inner_text() -> None:
    """A subscription-shaped block whose inner `text` is not valid JSON is
    skipped, not raised — matching the outer decode's existing tolerance for
    malformed history (`except (ValueError, TypeError): continue`)."""
    history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "s1",
                    "content": json.dumps([{"type": "text", "text": "not valid json"}]),
                }
            ],
        }
    ]

    assert list(ask_engine._tool_result_payloads(history)) == []
    assert ask_engine._ids_from_history(history) == set()
    assert ask_engine._previewed_ids_from_history(history) == set()
