"""Tests for the Ask query tools' recipient / project / matter / tag filters.

``DocumentFilters`` has carried these for the list API all along; these tests
pin that the Ask tools expose them to the model and forward them intact, so
the organisation the user curates by hand (matters, projects, tags) and the
recipient identity are reachable from a question.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.ask import engine as ask_engine
from library.ask.engine import TOOLS, _run_compare_to_series, _run_query_documents
from library.config import get_settings
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
    for name in ("query_documents", "compare_to_series"):
        properties = by_name[name]["input_schema"]["properties"]
        assert set(_FILTER_ARGS) <= set(properties), name


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


@pytest.mark.asyncio
async def test_compare_to_series_tool_forwards_the_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, DocumentFilters] = {}

    async def fake_summarize(session: Any, *, filters: DocumentFilters, **_: Any) -> Any:
        captured["filters"] = filters
        return SimpleNamespace(document_ids=[])

    monkeypatch.setattr(ask_engine, "summarize_series", fake_summarize)
    monkeypatch.setattr(ask_engine, "serialise_summary", lambda summary: {"status": "stub"})
    await _run_compare_to_series(cast(Any, None), get_settings(), dict(_FILTER_ARGS), set())

    filters = captured["filters"]
    assert filters.recipient_contains == "Ada"
    assert tuple(filters.project_slugs) == ("kitchen-renovation",)
    assert tuple(filters.matter_slugs) == ("car-insurance",)
    assert tuple(filters.tag_slugs) == ("tax-2025",)


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


def test_structured_tools_expose_review_status() -> None:
    """The archive already knows which extractions it distrusts; the model
    cannot act on that unless the tool schema offers it."""
    for tool_name in ("query_documents", "compare_to_series"):
        tool = next(tool for tool in TOOLS if tool["name"] == tool_name)
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
async def test_query_documents_ignores_an_unknown_review_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-invented value must degrade to 'no filter', never raise into the
    tool loop — the enum is a hint to the model, not a guarantee."""
    captured: dict[str, Any] = {}

    async def fake_query_documents(
        session: Any, *, filters: DocumentFilters, aggregate: Any, group_by: Any
    ) -> dict[str, Any]:
        captured["filters"] = filters
        return {"result_type": "list", "rows": [], "coverage": {}}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)

    await _run_query_documents(
        cast("AsyncSession", None), {"aggregate": "list", "review_status": "dubious"}, set()
    )

    assert captured["filters"].review_status is None
