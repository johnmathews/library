"""MCP write tools beyond ingest: ``update_document_metadata`` and ``add_comment``.

Kept out of tests/test_mcp.py, which is past 900 lines and covers the read
tools, auth and connection handling; these tests share its helpers but need
their own fixtures (two users with their own tokens, direct service edits
between two tool calls, spend-line allocations).

The edit tool is preview-then-apply: a preview writes nothing and returns a
``preview_token`` hashed over the document id and, for each field, its
current value and the value applying would store (read back from a
rolled-back dry run, so rounded and resolved, not the input); an apply must
carry a token that still matches. The adversarial cases are the household ones: someone else edits
the same document between the preview and the apply, someone allocates its
amount across spend lines in between, the applying user is not the uploader.

As in tests/test_mcp.py, the app lifespan and the MCP session are entered
inside each test body (anyio cancel scopes must exit in the task that
entered them).
"""

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast, get_args

import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from procrastinate import PsycopgConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library import mcp_server
from library.config import get_settings
from library.documents_service import edit_document
from library.jobs import job_app, procrastinate_conninfo
from library.models import Document
from library.schemas import DocumentUpdate
from library.spend_lines import AMOUNT_ALLOCATED_REFUSAL, LineInput, replace_lines
from tests.conftest import AuthUser, _fetch_all, _insert_user
from tests.test_documents_api import _seed_document
from tests.test_mcp import MCP_URL, _asgi_client, _insert_token, error_text, payload

pytestmark = pytest.mark.integration

type Connector = Callable[[str], AbstractAsyncContextManager[ClientSession]]


# --- fixtures / helpers -------------------------------------------------------


@pytest.fixture
def connect(api_app: FastAPI, api_database_url: str) -> Connector:
    """Factory: the app's lifespan (real Procrastinate connector, so deferred
    jobs land in ``procrastinate_jobs``) plus an initialized MCP session
    authenticated with ``token``."""

    @asynccontextmanager
    async def run(token: str) -> AsyncIterator[ClientSession]:
        connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
        with job_app.replace_connector(connector):
            async with (
                api_app.router.lifespan_context(api_app),
                _asgi_client(api_app, token) as http_client,
                streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session

    return run


@pytest.fixture
async def owner_token(api_database_url: str, auth_user: AuthUser) -> str:
    raw, _ = await _insert_token(api_database_url, auth_user.id, "mcp-edit-owner")
    return raw


@pytest.fixture
async def spouse(api_database_url: str) -> tuple[int, str]:
    """A second household member with her own token: (user id, raw token)."""
    user_id = await _insert_user(
        api_database_url, f"mcp-edit-spouse-{secrets.token_hex(4)}", "irrelevant"
    )
    raw, _ = await _insert_token(api_database_url, user_id, "mcp-edit-spouse")
    return user_id, raw


async def _edit_events(database_url: str, document_id: int) -> list[dict[str, Any]]:
    rows = await _fetch_all(
        database_url,
        "SELECT detail FROM ingestion_events "
        "WHERE document_id = :id AND event = 'user_edited' ORDER BY id",
        id=document_id,
    )
    return [cast(dict[str, Any], row[0]) for row in rows]


async def _row(database_url: str, document_id: int, columns: str) -> tuple[object, ...]:
    (row,) = await _fetch_all(
        database_url, f"SELECT {columns} FROM documents WHERE id = :id", id=document_id
    )
    return row


async def _service_edit(
    database_url: str, document_id: int, update: DocumentUpdate, user_id: int
) -> None:
    """Someone else's edit through the web app's path (the shared service)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = await session.get(Document, document_id)
            assert document is not None
            await edit_document(
                session,
                document,
                update,
                settings=get_settings(),
                edited_by="user",
                user_id=user_id,
            )
    finally:
        await engine.dispose()


async def _allocate(database_url: str, document_id: int, amounts: list[str]) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await replace_lines(
                session, document_id, [LineInput(amount=Decimal(a)) for a in amounts]
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _call(session: ClientSession, arguments: dict[str, Any]) -> Any:
    return await session.call_tool("update_document_metadata", arguments)


# --- the tool's surface -------------------------------------------------------


async def test_edit_tool_parameters_mirror_document_update(
    connect: Connector, owner_token: str
) -> None:
    """Every editable field is a typed parameter, and nothing else is.

    ``DocumentUpdate`` is the specification of what can be edited (the PATCH
    route and Ask use it too), so a field added there and not here would be
    unreachable over MCP, and a parameter with no field behind it would be
    dropped on the way to the service. ``clear_fields`` offers exactly the
    nullable scalar fields: lists clear with ``[]`` and ``language`` cannot
    be cleared, both refused by the service.
    """
    async with connect(owner_token) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    properties = set(tools["update_document_metadata"].inputSchema["properties"])
    protocol = {"document_id", "clear_fields", "apply", "preview_token"}
    assert properties - protocol == set(DocumentUpdate.model_fields)

    clearable = set(get_args(mcp_server.ClearableField))
    assert clearable == set(DocumentUpdate.model_fields) - {
        "tags",
        "projects",
        "matters",
        "language",
    }


# --- preview ------------------------------------------------------------------


async def test_edit_preview_writes_nothing(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """A preview leaves the document, its audit trail and the taxonomy as
    they were — including the sender and tag rows the apply path would
    create, which the preview's dry run makes and rolls back."""
    marker = f"mcp-edit-preview-{secrets.token_hex(4)}"
    document_id = await _seed_document(
        api_database_url,
        marker,
        title="Polis 2024",
        summary="Old summary",
        sender_name=f"{marker} Old Insurer",
        tag_slugs=[f"{marker}-old"],
        amount_total=Decimal("12.50"),
        currency="EUR",
    )
    new_sender = f"{marker} New Insurer"
    new_tag = f"{marker}-new"
    before = await _row(api_database_url, document_id, "title, sender_id, amount_total, extra")

    async with connect(owner_token) as session:
        result = payload(
            await _call(
                session,
                {
                    "document_id": document_id,
                    "title": "Polis 2025",
                    "sender": new_sender,
                    "tags": [new_tag],
                    "amount_total": "99.90",
                    "currency": "usd",
                    "clear_fields": ["summary"],
                },
            )
        )

    assert result["document_id"] == document_id
    assert result["applied"] is False
    assert result["changes"] == {
        "title": {"current": "Polis 2024", "proposed": "Polis 2025"},
        "summary": {"current": "Old summary", "proposed": None},
        "sender": {"current": f"{marker} Old Insurer", "proposed": new_sender},
        "tags": {"current": [f"{marker}-old"], "proposed": [new_tag]},
        "amount_total": {"current": "12.50", "proposed": "99.90"},
        # The stored form, not the input: currency is upper-cased.
        "currency": {"current": "EUR", "proposed": "USD"},
    }
    assert isinstance(result["preview_token"], str) and len(result["preview_token"]) == 64

    assert await _row(api_database_url, document_id, "title, sender_id, amount_total, extra") == (
        before
    )
    assert await _edit_events(api_database_url, document_id) == []
    assert (
        await _fetch_all(api_database_url, "SELECT id FROM senders WHERE name = :n", n=new_sender)
        == []
    )
    assert (
        await _fetch_all(api_database_url, "SELECT id FROM tags WHERE slug = :s", s=new_tag) == []
    )


async def _preview_then_apply(
    connect: Connector, token: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Preview ``arguments``, then apply them with the preview's own token."""
    async with connect(token) as session:
        preview = payload(await _call(session, arguments))
        applied = payload(
            await _call(
                session, {**arguments, "apply": True, "preview_token": preview["preview_token"]}
            )
        )
    return preview, applied


async def test_preview_shows_the_stored_amount(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """``amount_total`` is ``Numeric(14, 2)``: 12.345 is stored as 12.35, so
    that is the value the user is shown to approve, not the input."""
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-round-{secrets.token_hex(4)}", title="T"
    )
    preview, applied = await _preview_then_apply(
        connect, owner_token, {"document_id": document_id, "amount_total": "12.345"}
    )

    assert preview["changes"] == {"amount_total": {"current": None, "proposed": "12.35"}}
    assert applied["applied"] is True
    assert await _row(api_database_url, document_id, "amount_total") == (Decimal("12.35"),)


async def test_preview_shows_the_normalised_sender(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """The sender upsert collapses whitespace; the preview shows the
    collapsed name, which is the sender the document ends up with."""
    marker = f"mcp-edit-ws-{secrets.token_hex(4)}"
    document_id = await _seed_document(api_database_url, marker, title="T")
    preview, _ = await _preview_then_apply(
        connect, owner_token, {"document_id": document_id, "sender": f"  {marker}   Sender  "}
    )

    assert preview["changes"] == {"sender": {"current": None, "proposed": f"{marker} Sender"}}
    stored = await _fetch_all(
        api_database_url,
        "SELECT s.name FROM documents d JOIN senders s ON s.id = d.sender_id WHERE d.id = :id",
        id=document_id,
    )
    assert stored == [(f"{marker} Sender",)]


async def test_preview_shows_the_resolved_recipient(
    connect: Connector, owner_token: str, spouse: tuple[int, str], api_database_url: str
) -> None:
    """A household member's username resolves to the recipient linked to
    her, named by her display name; the preview shows that name, not the
    username typed."""
    spouse_id, _ = spouse
    display_name = f"Spouse {secrets.token_hex(4)}"
    engine = create_async_engine(api_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            username = (
                await connection.execute(
                    text("UPDATE users SET display_name = :d WHERE id = :id RETURNING username"),
                    {"d": display_name, "id": spouse_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-recipient-{secrets.token_hex(4)}", title="T"
    )
    preview, applied = await _preview_then_apply(
        connect, owner_token, {"document_id": document_id, "recipient": username}
    )

    assert preview["changes"] == {"recipient": {"current": None, "proposed": display_name}}
    assert applied["current"] == {"recipient": display_name}


async def test_apply_with_the_preview_token_succeeds_after_normalisation(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """Apply recomputes the token from the same stored values the preview
    showed, so a change that normalises still applies with the preview's own
    token, and what is stored is exactly what was approved."""
    marker = f"mcp-edit-normtoken-{secrets.token_hex(4)}"
    document_id = await _seed_document(api_database_url, marker, title="T")
    preview, applied = await _preview_then_apply(
        connect,
        owner_token,
        {
            "document_id": document_id,
            "amount_total": "7.005",
            "currency": "eur",
            "sender": f" {marker}  Shop ",
        },
    )

    assert applied["applied"] is True
    approved = {name: change["proposed"] for name, change in preview["changes"].items()}
    assert approved == {"amount_total": "7.01", "currency": "EUR", "sender": f"{marker} Shop"}
    assert applied["current"] == approved


async def test_edit_database_refusal_at_preview_is_masked(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """A database error in the preview's dry run that is not the allocation
    refusal is re-raised, so the client sees the generic masked error (never
    SQL) and nothing is kept. A tag slug longer than ``tags.slug``'s 64
    characters is one such error."""
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-dberror-{secrets.token_hex(4)}", title="Unchanged"
    )
    long_slug = "x" * 65
    async with connect(owner_token) as session:
        result = await _call(session, {"document_id": document_id, "tags": [long_slug]})
    assert error_text(result) == "Error calling tool 'update_document_metadata'"
    assert await _edit_events(api_database_url, document_id) == []
    assert (
        await _fetch_all(api_database_url, "SELECT id FROM tags WHERE slug = :s", s=long_slug) == []
    )


async def test_edit_invalid_value_fails_at_preview(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """A change apply would refuse is refused at preview, with apply's words.

    An unknown kind slug is only detectable against the database; a malformed
    currency is refused by the parameter's own type (the same validator as
    ``DocumentUpdate``). Neither is ever shown to the user as a change to
    approve, and neither writes anything.
    """
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-invalid-{secrets.token_hex(4)}", title="Unchanged"
    )
    async with connect(owner_token) as session:
        unknown_kind = await _call(
            session, {"document_id": document_id, "kind_slug": "no-such-kind"}
        )
        bad_currency = await _call(session, {"document_id": document_id, "currency": "EURO"})
        both = await _call(
            session,
            {"document_id": document_id, "title": "New", "clear_fields": ["title"]},
        )
        nothing = await _call(session, {"document_id": document_id})

    assert error_text(unknown_kind) == "unknown kind slug: 'no-such-kind'"
    assert "currency" in error_text(bad_currency)
    assert error_text(both) == "title is both set and listed in clear_fields; pick one"
    assert "no changes given" in error_text(nothing)
    assert await _row(api_database_url, document_id, "title, kind_id, currency") == (
        "Unchanged",
        None,
        None,
    )
    assert await _edit_events(api_database_url, document_id) == []


async def test_edit_unknown_field_is_rejected_by_the_tool_schema(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """``topics`` is extracted, not editable: naming it is an argument error,
    not a silently dropped field reported as a success."""
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-topics-{secrets.token_hex(4)}", topics=["water"]
    )
    async with connect(owner_token) as session:
        result = await _call(session, {"document_id": document_id, "title": "x", "topics": ["gas"]})
    message = error_text(result)
    assert "topics" in message
    assert "Unexpected keyword argument" in message
    assert await _edit_events(api_database_url, document_id) == []


async def test_edit_unknown_document_errors(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """Unknown and trashed documents are refused at preview and at apply."""
    deleted_id = await _seed_document(
        api_database_url,
        f"mcp-edit-deleted-{secrets.token_hex(4)}",
        deleted_at=datetime.now(UTC),
    )
    async with connect(owner_token) as session:
        outcomes = [
            await _call(session, {"document_id": document_id, "title": "x", **extra})
            for document_id in (999_999_999, deleted_id)
            for extra in ({}, {"apply": True, "preview_token": "0" * 64})
        ]
    assert [error_text(result) for result in outcomes] == [
        "document 999999999 not found",
        "document 999999999 not found",
        f"document {deleted_id} not found",
        f"document {deleted_id} not found",
    ]


# --- apply --------------------------------------------------------------------


async def test_edit_apply_requires_preview_token(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url, f"mcp-edit-notoken-{secrets.token_hex(4)}", title="Original"
    )
    async with connect(owner_token) as session:
        result = await _call(session, {"document_id": document_id, "title": "x", "apply": True})
    assert error_text(result).startswith("preview first: call update_document_metadata without")
    assert await _row(api_database_url, document_id, "title") == ("Original",)
    assert await _edit_events(api_database_url, document_id) == []


async def test_edit_apply_with_matching_token_records_mcp_and_user(
    connect: Connector,
    auth_user: AuthUser,
    spouse: tuple[int, str],
    api_database_url: str,
) -> None:
    """The applying user — not the uploader — is recorded, as an MCP edit.

    The document was uploaded by the owner; the spouse previews and applies
    from her own connection, so a ``user_id`` taken from the wrong place
    (uploader, a default user) would show up here.
    """
    spouse_id, spouse_token = spouse
    marker = f"mcp-edit-apply-{secrets.token_hex(4)}"
    document_id = await _seed_document(
        api_database_url,
        marker,
        title="Factuur",
        sender_name=f"{marker} Sender",
        uploader_id=auth_user.id,
    )
    arguments = {
        "document_id": document_id,
        "title": "Factuur maart",
        "kind_slug": "invoice",
        "clear_fields": ["sender"],
    }
    async with connect(spouse_token) as session:
        preview = payload(await _call(session, arguments))
        applied = payload(
            await _call(
                session, {**arguments, "apply": True, "preview_token": preview["preview_token"]}
            )
        )

    assert applied["document_id"] == document_id
    assert applied["applied"] is True
    assert sorted(applied["updated_fields"]) == ["kind_id", "sender_id", "title"]
    assert applied["current"] == {
        "title": "Factuur maart",
        "kind_slug": "invoice",
        "sender": None,
    }
    assert applied["review_status"] in {"unreviewed", "needs_review", "verified"}

    title, sender_id = await _row(api_database_url, document_id, "title, sender_id")
    assert (title, sender_id) == ("Factuur maart", None)
    (event,) = await _edit_events(api_database_url, document_id)
    assert event["edited_by"] == "mcp"
    assert event["user_id"] == spouse_id != auth_user.id
    # A title edit makes the chunk headers stale: the re-embed is queued.
    jobs = await _fetch_all(
        api_database_url,
        "SELECT task_name FROM procrastinate_jobs WHERE (args->>'document_id')::bigint = :id",
        id=document_id,
    )
    assert jobs == [("library.jobs.embed_document",)]


async def test_edit_apply_refused_after_concurrent_change(
    connect: Connector,
    auth_user: AuthUser,
    spouse: tuple[int, str],
    api_database_url: str,
) -> None:
    """The owner previews a summary; the spouse changes that summary in the
    web app; the owner's apply would overwrite her edit unseen, so it is
    refused and her value stands."""
    spouse_id, _ = spouse
    document_id = await _seed_document(
        api_database_url,
        f"mcp-edit-race-{secrets.token_hex(4)}",
        summary="first",
    )
    owner_token, _ = await _insert_token(api_database_url, auth_user.id, "mcp-edit-race")
    arguments = {"document_id": document_id, "summary": "owner's summary"}
    async with connect(owner_token) as session:
        preview = payload(await _call(session, arguments))
        await _service_edit(
            api_database_url, document_id, DocumentUpdate(summary="spouse's summary"), spouse_id
        )
        result = await _call(
            session, {**arguments, "apply": True, "preview_token": preview["preview_token"]}
        )

    assert error_text(result).startswith("the document changed since the preview")
    assert await _row(api_database_url, document_id, "summary") == ("spouse's summary",)
    events = await _edit_events(api_database_url, document_id)
    assert [(e["edited_by"], e["user_id"]) for e in events] == [("user", spouse_id)]


async def test_edit_apply_with_different_fields_refused(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """A token approves one exact change: a different value, an extra field
    or a different document all need a preview of their own."""
    marker = f"mcp-edit-fields-{secrets.token_hex(4)}"
    document_id = await _seed_document(api_database_url, marker, title="Original")
    other_id = await _seed_document(api_database_url, f"{marker}-other", title="Original")
    async with connect(owner_token) as session:
        token = payload(await _call(session, {"document_id": document_id, "title": "Approved"}))[
            "preview_token"
        ]
        attempts = [
            {"document_id": document_id, "title": "Something else"},
            {"document_id": document_id, "title": "Approved", "summary": "sneaked in"},
            {"document_id": other_id, "title": "Approved"},
        ]
        results = [
            await _call(session, {**attempt, "apply": True, "preview_token": token})
            for attempt in attempts
        ]

    for result in results:
        assert error_text(result).startswith("the document changed since the preview")
    for doc in (document_id, other_id):
        assert await _row(api_database_url, doc, "title, summary") == ("Original", None)
        assert await _edit_events(api_database_url, doc) == []


async def test_edit_apply_allocated_amount_refused(
    connect: Connector,
    owner_token: str,
    api_database_url: str,
) -> None:
    """An amount allocated across spend lines cannot be changed.

    Previewed while the amount was free, then allocated (60 + 40) before the
    apply: the token would still match — the amount itself did not change —
    but apply reruns the preview's dry run under the row lock, and that
    refuses with the commit guard's words before anything is written. A fresh
    preview refuses the same way. Amounts are invented (this repository is
    public).
    """
    document_id = await _seed_document(
        api_database_url,
        f"mcp-edit-allocated-{secrets.token_hex(4)}",
        amount_total=Decimal("100.00"),
        currency="EUR",
    )
    arguments = {"document_id": document_id, "amount_total": "250.00"}
    async with connect(owner_token) as session:
        preview = payload(await _call(session, arguments))
        await _allocate(api_database_url, document_id, ["60.00", "40.00"])
        applied = await _call(
            session, {**arguments, "apply": True, "preview_token": preview["preview_token"]}
        )
        again = await _call(session, arguments)

    assert error_text(applied) == AMOUNT_ALLOCATED_REFUSAL
    assert error_text(again) == AMOUNT_ALLOCATED_REFUSAL
    assert await _row(api_database_url, document_id, "amount_total") == (Decimal("100.00"),)
    assert await _edit_events(api_database_url, document_id) == []


async def test_edit_apply_kind_removed_after_preview_refused(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    """A kind that existed at preview and is gone at apply is refused with
    the service's message, and nothing is written. The token cannot see it
    (it hashes the document's values, not the taxonomy)."""
    slug = f"mcp-edit-kind-{secrets.token_hex(4)}"
    engine = create_async_engine(api_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO kinds (slug, name) VALUES (:s, :s)"), {"s": slug}
            )
        document_id = await _seed_document(api_database_url, slug, title="Kindless")
        arguments = {"document_id": document_id, "kind_slug": slug}
        async with connect(owner_token) as session:
            preview = payload(await _call(session, arguments))
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM kinds WHERE slug = :s"), {"s": slug})
            result = await _call(
                session, {**arguments, "apply": True, "preview_token": preview["preview_token"]}
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM kinds WHERE slug = :s"), {"s": slug})
        await engine.dispose()

    assert error_text(result) == f"unknown kind slug: {slug!r}"
    assert await _row(api_database_url, document_id, "kind_id") == (None,)
    assert await _edit_events(api_database_url, document_id) == []


# --- add_comment --------------------------------------------------------------


async def test_add_comment_attributed_and_reembedded(
    connect: Connector,
    auth_user: AuthUser,
    spouse: tuple[int, str],
    api_database_url: str,
) -> None:
    """The comment's author is the connecting user (here the spouse, on a
    document the owner uploaded), and the document is queued for re-embedding
    so the comment becomes searchable."""
    spouse_id, spouse_token = spouse
    document_id = await _seed_document(
        api_database_url, f"mcp-comment-{secrets.token_hex(4)}", uploader_id=auth_user.id
    )
    async with connect(spouse_token) as session:
        result = payload(
            await session.call_tool(
                "add_comment", {"document_id": document_id, "body": "  This is our old house.  "}
            )
        )

    assert result["document_id"] == document_id
    assert result["author_id"] == spouse_id
    assert result["body"] == "This is our old house."
    rows = await _fetch_all(
        api_database_url,
        "SELECT id, author_id, body FROM document_comments WHERE document_id = :id",
        id=document_id,
    )
    assert rows == [(result["id"], spouse_id, "This is our old house.")]
    events = await _fetch_all(
        api_database_url,
        "SELECT event, (detail->>'comment_id')::bigint FROM ingestion_events "
        "WHERE document_id = :id",
        id=document_id,
    )
    assert events == [("comment_added", result["id"])]
    jobs = await _fetch_all(
        api_database_url,
        "SELECT task_name FROM procrastinate_jobs WHERE (args->>'document_id')::bigint = :id",
        id=document_id,
    )
    assert jobs == [("library.jobs.embed_document",)]


async def test_add_comment_empty_body_rejected(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    document_id = await _seed_document(
        api_database_url, f"mcp-comment-empty-{secrets.token_hex(4)}"
    )
    async with connect(owner_token) as session:
        blank = await session.call_tool("add_comment", {"document_id": document_id, "body": "   "})
        empty = await session.call_tool("add_comment", {"document_id": document_id, "body": ""})
    assert error_text(blank) == "comment body must not be blank"
    assert "at least 1 character" in error_text(empty)
    assert (
        await _fetch_all(
            api_database_url,
            "SELECT id FROM document_comments WHERE document_id = :id",
            id=document_id,
        )
        == []
    )


async def test_add_comment_unknown_document_errors(
    connect: Connector, owner_token: str, api_database_url: str
) -> None:
    deleted_id = await _seed_document(
        api_database_url,
        f"mcp-comment-deleted-{secrets.token_hex(4)}",
        deleted_at=datetime.now(UTC),
    )
    async with connect(owner_token) as session:
        unknown = await session.call_tool(
            "add_comment", {"document_id": 999_999_999, "body": "hello"}
        )
        deleted = await session.call_tool("add_comment", {"document_id": deleted_id, "body": "hi"})
    assert error_text(unknown) == "document 999999999 not found"
    assert error_text(deleted) == f"document {deleted_id} not found"
    assert (
        await _fetch_all(
            api_database_url,
            "SELECT id FROM document_comments WHERE document_id = :id",
            id=deleted_id,
        )
        == []
    )
