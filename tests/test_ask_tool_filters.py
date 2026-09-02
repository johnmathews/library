"""Tests for the Ask query tools' recipient / project / matter / tag filters.

``DocumentFilters`` has carried these for the list API all along; these tests
pin that the Ask tools expose them to the model and forward them intact, so
the organisation the user curates by hand (matters, projects, tags) and the
recipient identity are reachable from a question.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.ask import engine as ask_engine
from library.ask.engine import TOOLS, _run_query_documents
from library.models import ReviewStatus
from library.search import DocumentFilters
from tests.test_documents_api import _seed_document

_FILTER_ARGS: dict[str, Any] = {
    "recipient_contains": "Ada",
    "projects": ["kitchen-renovation"],
    "matters": ["car-insurance"],
    "tags": ["tax-2025"],
}


def test_query_tools_declare_the_filters_to_the_model() -> None:
    """A filter the schema does not declare is one the model can never use."""
    by_name = {tool["name"]: tool for tool in TOOLS}
    properties = by_name["query_documents"]["input_schema"]["properties"]
    assert set(_FILTER_ARGS) <= set(properties)


@pytest.mark.asyncio
async def test_query_documents_tool_forwards_the_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, DocumentFilters] = {}

    async def fake_query_documents(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return {"result_type": "list", "rows": []}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)
    await _run_query_documents(cast(Any, None), {"aggregate": "list", **_FILTER_ARGS}, set())

    filters = captured["filters"]
    assert filters.recipient_contains == "Ada"
    assert tuple(filters.project_slugs) == ("kitchen-renovation",)
    assert tuple(filters.matter_slugs) == ("car-insurance",)
    assert tuple(filters.tag_slugs) == ("tax-2025",)


@pytest.mark.asyncio
async def test_query_documents_tool_treats_blank_filters_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, DocumentFilters] = {}

    async def fake_query_documents(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return {"result_type": "list", "rows": []}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)
    await _run_query_documents(
        cast(Any, None),
        {"aggregate": "list", "recipient_contains": "  ", "projects": [], "matters": None},
        set(),
    )
    assert captured["filters"] == DocumentFilters()


# --- end to end against the database ----------------------------------------

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _open_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_query_documents_tool_filters_by_matter_and_recipient(
    api_database_url: str,
) -> None:
    mine = await _seed_document(
        api_database_url,
        "filt-mine",
        recipient_name="Ada Example",
        matter_slugs=["car-insurance"],
    )
    await _seed_document(
        api_database_url, "filt-theirs", recipient_name="Bo Example", matter_slugs=["car-insurance"]
    )
    await _seed_document(api_database_url, "filt-other-matter", recipient_name="Ada Example")

    cited: set[int] = set()
    async with _open_session(api_database_url) as session:
        result = await _run_query_documents(
            session,
            {"aggregate": "list", "matters": ["car-insurance"], "recipient_contains": "ada"},
            cited,
        )
    assert [row["id"] for row in result["rows"]] == [mine]
    assert cited == {mine}


def test_query_documents_exposes_review_status() -> None:
    """The archive already knows which extractions it distrusts; the model
    cannot act on that unless the tool schema offers it."""
    tool = next(tool for tool in TOOLS if tool["name"] == "query_documents")
    prop = tool["input_schema"]["properties"]["review_status"]
    assert prop["enum"] == ["verified", "needs_review", "unreviewed"]


@pytest.mark.asyncio
async def test_query_documents_forwards_review_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_query_documents(
        session: Any, *, filters: DocumentFilters, aggregate: Any, group_by: Any
    ) -> dict[str, Any]:
        captured["filters"] = filters
        return {"result_type": "list", "rows": [], "coverage": {}}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)

    await _run_query_documents(
        cast("AsyncSession", None),
        {"aggregate": "list", "review_status": "needs_review"},
        set(),
    )

    assert captured["filters"].review_status is ReviewStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_query_documents_reports_an_unknown_review_status_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-invented value must be surfaced to the model as an error, not
    silently degrade to 'no filter': that would hand back the ENTIRE archive
    under what reads as a filtered call, with only a server-side log to show
    for it. The tool loop must not crash either — an error payload, not a
    raised exception."""
    called = False

    async def fake_query_documents(
        session: Any, *, filters: DocumentFilters, aggregate: Any, group_by: Any
    ) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"result_type": "list", "rows": [], "coverage": {}}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)

    result = await _run_query_documents(
        cast("AsyncSession", None), {"aggregate": "list", "review_status": "dubious"}, set()
    )

    assert "error" in result
    assert "dubious" in result["error"]
    assert "verified" in result["error"]
    assert "needs_review" in result["error"]
    assert "unreviewed" in result["error"]
    assert called is False  # nothing was queried for a value that can't be honoured


# --- facet filters (#136) ----------------------------------------------------


def test_query_tools_declare_the_facet_filter() -> None:
    """`DocumentFilters.facets` has existed since the vocabulary shipped; until
    a tool schema declared it, the curated `category` vocabulary was unreachable
    from a question — the model had no way to express the filter at all."""
    by_name = {tool["name"]: tool for tool in TOOLS}
    for tool_name in ("query_documents", "semantic_search"):
        properties = by_name[tool_name]["input_schema"]["properties"]
        assert "facets" in properties, tool_name
        assert properties["facets"]["type"] == "object"


@pytest.mark.asyncio
async def test_query_documents_tool_forwards_a_facet_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, DocumentFilters] = {}

    async def fake_query_documents(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return {"result_type": "sum_amount", "rows": []}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)
    await _run_query_documents(
        cast(Any, None),
        {"aggregate": "sum_amount", "facets": {"category": "software"}},
        set(),
    )

    assert dict(captured["filters"].facets) == {"category": "software"}


@pytest.mark.asyncio
async def test_query_documents_tool_drops_unusable_facet_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank or non-string facet entry becomes *no filter for that facet*,
    never a filter on the empty string.

    The model writes this object freehand. An entry coerced to `""` would match
    no document and silently narrow a total to zero, which reads exactly like a
    real answer of nothing — the failure the `coverage` block cannot describe,
    because the documents were never matched in the first place.
    """
    captured: dict[str, DocumentFilters] = {}

    async def fake_query_documents(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return {"result_type": "sum_amount", "rows": []}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)
    await _run_query_documents(
        cast(Any, None),
        {
            "aggregate": "sum_amount",
            "facets": {"category": "  ", "scope": None, "cost_type": " usage ", "": "software"},
        },
        set(),
    )

    assert dict(captured["filters"].facets) == {"cost_type": "usage"}


@pytest.mark.asyncio
async def test_query_documents_tool_tolerates_a_non_object_facets_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hallucinated shape degrades to "no facet filter", never to a 500 inside
    the tool loop — the same contract `_slug_args` holds for the list filters."""
    captured: dict[str, DocumentFilters] = {}

    async def fake_query_documents(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return {"result_type": "sum_amount", "rows": []}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)
    await _run_query_documents(
        cast(Any, None), {"aggregate": "sum_amount", "facets": ["category=software"]}, set()
    )

    assert dict(captured["filters"].facets) == {}
