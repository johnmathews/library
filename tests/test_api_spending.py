"""The routes. Thin by design — the behaviour is tested in the modules
underneath, so what is asserted here is status codes, shape, and the mapping
from a named error to a status a client can act on.

Three things are only assertable *here*, because nothing below the router can
see them, and each has a mutation named in its docstring:

* the footer's **eight** fields reaching the response body;
* `facets_in_rule` being derived from the rule (`chart_footer` trusts its
  caller, so an empty set silently stops reporting uncategorised money);
* `/cell` being asked `/data`'s exact question (`chart_series` and `chart_cell`
  share a predicate, but only when they are given the same arguments).

Every chart is named uniquely per test: the suite shares one database and list
endpoints default to 25 rows, so a list assertion is scoped by name, never by a
count. Senders, amounts and currencies are invented — this repository is public.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.api.documents import update_document
from library.api.spending import _commit_allocation
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.models import (
    AmountKind,
    Chart,
    Document,
    DocumentSource,
    DocumentStatus,
    FacetValue,
    FxRate,
    Sender,
    SpendLine,
)
from library.schemas import DocumentUpdate
from tests.conftest import DocumentFactory, StubAnthropic

pytestmark = pytest.mark.integration

#: A currency with no rate anywhere in the archive (ISO 4217 reserves XTS for
#: testing), so an amount in it is genuinely unconvertible.
UNCONVERTIBLE_CURRENCY = "XTS"

#: The window the seeded documents live in. Invented, like every amount here.
MARCH = date(2026, 3, 1)


def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run one unit of seeding on its own engine, committed.

    Sync, like the helpers in `test_api_payments.py`: `api_client` is a
    `TestClient` driving the app on its own loop, so the seeding cannot share
    the test's.
    """

    async def _go() -> T:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _seed_vocabulary(
    database_url: str, facet: str = "category", values: tuple[str, ...] = ("software", "services")
) -> None:
    async def work(session: AsyncSession) -> None:
        await create_facet(session, facet, facet.replace("_", " ").title(), 0)
        for value in values:
            await create_value(session, facet, value, value.title())

    _run(database_url, work)


def _seed_document(
    database_url: str,
    *,
    amount: str | None = None,
    kind: AmountKind | None = None,
    day: date | None = MARCH,
    currency: str | None = "EUR",
    labels: Mapping[str, str] | None = None,
    sender: str | None = None,
) -> int:
    async def work(session: AsyncSession) -> int:
        marker = f"spending-api:{uuid.uuid4()}"
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            status=DocumentStatus.INDEXED,
            title=marker,
            document_date=day,
            amount_total=Decimal(amount) if amount is not None else None,
            currency=currency,
            amount_kind=kind,
        )
        if sender is not None:
            existing = (
                await session.execute(select(Sender.id).where(Sender.name == sender))
            ).scalar_one_or_none()
            if existing is None:
                sender_row = Sender(name=sender)
                session.add(sender_row)
                await session.flush()
                existing = sender_row.id
            document.sender_id = existing
        session.add(document)
        await session.flush()
        for facet_key, value_key in (labels or {}).items():
            await set_document_label(session, document.id, facet_key, value_key)
        return document.id

    return _run(database_url, work)


def _seed_sender_document(
    database_url: str,
    *,
    sender: str,
    amount: str,
    day: date = MARCH,
    labels: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    """A document from a named sender; returns `(document_id, sender_id)`.

    Extends `_seed_document`'s job rather than replacing it: `sender=` there
    resolves-or-creates the `Sender` row, so there is one definition of "seed a
    document for the spending API".
    """
    document_id = _seed_document(
        database_url,
        amount=amount,
        kind=AmountKind.PAYMENT_MADE,
        day=day,
        sender=sender,
        labels=labels,
    )

    async def read_sender_id(session: AsyncSession) -> int:
        return (await session.execute(select(Sender.id).where(Sender.name == sender))).scalar_one()

    return document_id, _run(database_url, read_sender_id)


def _save_chart(
    api_client: TestClient,
    name: str,
    rule: dict[str, object],
    *,
    default_split: str | None = None,
) -> int:
    """Create a chart and return its id. Names are unique per test so list
    assertions can be scoped."""
    response = api_client.post(
        "/api/spending",
        json={
            "name": name,
            "question_text": f"question for {name}",
            "rule": rule,
            "default_grain": "month",
            "default_split": default_split,
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


SOFTWARE_RULE: dict[str, object] = {
    "all": [{"facet": "category", "op": "in", "values": ["software"]}]
}

#: Invented. Nothing here corresponds to a real sender.
VENDOR_A = "Corvus Test Assurance"


#: Two invented currencies, one worth exactly half the other, so an amount of
#: `20.01` converts to `10.005` — a value with a sub-cent tail, which is what
#: makes rounding observable. `fx.convert` does not round, and `fx_rates` is a
#: seeded table the autouse truncation deliberately leaves alone, so these rows
#: are removed again by the fixture.
HALF_RATE_FROM = "XTA"
HALF_RATE_TO = "XTB"


@pytest.fixture
def half_rate_currencies(api_database_url: str) -> Iterator[tuple[str, str]]:
    async def add(session: AsyncSession) -> None:
        session.add(
            FxRate(currency=HALF_RATE_FROM, as_of=date(2026, 1, 1), rate_to_base=Decimal("1.00"))
        )
        session.add(
            FxRate(currency=HALF_RATE_TO, as_of=date(2026, 1, 1), rate_to_base=Decimal("2.00"))
        )

    async def remove(session: AsyncSession) -> None:
        await session.execute(
            delete(FxRate).where(FxRate.currency.in_((HALF_RATE_FROM, HALF_RATE_TO)))
        )

    _run(api_database_url, add)
    yield HALF_RATE_FROM, HALF_RATE_TO
    _run(api_database_url, remove)


@pytest.fixture
def api_document_id(api_database_url: str) -> int:
    """One ready document with `amount_total = 100.00`, for the allocation routes."""
    return _seed_document(api_database_url, amount="100.00", kind=AmountKind.PAYMENT_MADE)


# --- charts ------------------------------------------------------------------


def test_listing_charts_rejects_a_limit_over_one_hundred(api_client: TestClient) -> None:
    assert api_client.get("/api/spending?limit=101").status_code == 422


def test_a_saved_chart_appears_in_the_list(api_client: TestClient) -> None:
    _save_chart(api_client, "api-list-one", {"all": []})
    body = api_client.get("/api/spending?limit=100").json()
    assert [chart["name"] for chart in body["charts"] if chart["name"] == "api-list-one"] == [
        "api-list-one"
    ]


def test_saving_a_chart_with_a_duplicate_name_is_a_409(api_client: TestClient) -> None:
    _save_chart(api_client, "api-dup", {"all": []})
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-dup",
            "question_text": "again",
            "rule": {"all": []},
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 409


def test_renaming_a_chart_onto_another_name_is_a_409(api_client: TestClient) -> None:
    _save_chart(api_client, "api-rename-a", {"all": []})
    other = _save_chart(api_client, "api-rename-b", {"all": []})
    assert (
        api_client.patch(f"/api/spending/{other}", json={"name": "api-rename-a"}).status_code == 409
    )


def test_editing_and_deleting_a_chart(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-edit", {"all": []})
    patched = api_client.patch(
        f"/api/spending/{chart_id}", json={"name": "api-edited", "default_grain": "quarter"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "api-edited"
    assert patched.json()["default_grain"] == "quarter"
    assert api_client.delete(f"/api/spending/{chart_id}").status_code == 204
    assert api_client.get(f"/api/spending/{chart_id}/data").status_code == 404


# --- PATCH: the rule branch --------------------------------------------------
#
# `PATCH /api/spending/{id}` has accepted a `rule` since the route was written,
# but nothing exercised that branch: before these tests the file's only two
# chart PATCHes sent `{"name": ...}` and `{"name", "default_grain"}`. It is the
# rule editor's primary write path, so it is characterised here first.


def test_patching_a_rule_changes_the_answer(api_client: TestClient, api_database_url: str) -> None:
    """The write path end to end: the new rule must reach `/data`, not only the
    stored row. Asserting the PATCH response alone would pass even if `/data`
    kept answering the old rule."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    _seed_document(
        api_database_url,
        amount="25.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "services"},
    )
    chart_id = _save_chart(api_client, "api-patch-rule-answer", SOFTWARE_RULE)
    assert api_client.get(f"/api/spending/{chart_id}/data").json()["total"] == "10.00"

    services: dict[str, object] = {
        "all": [{"facet": "category", "op": "in", "values": ["services"]}]
    }
    patched = api_client.patch(f"/api/spending/{chart_id}", json={"rule": services})

    assert patched.status_code == 200, patched.text
    assert patched.json()["rule"] == services
    assert api_client.get(f"/api/spending/{chart_id}").json()["rule"] == services
    assert api_client.get(f"/api/spending/{chart_id}/data").json()["total"] == "25.00"


def test_patching_a_rule_with_an_unknown_facet_is_a_422_naming_it(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-patch-unknown-facet", {"all": []})
    response = api_client.patch(
        f"/api/spending/{chart_id}",
        json={"rule": {"all": [{"facet": "no_such_facet", "op": "in", "values": ["x"]}]}},
    )
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_patching_a_rule_with_an_unknown_value_is_a_422_naming_it(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-patch-unknown-value", {"all": []})
    response = api_client.patch(
        f"/api/spending/{chart_id}",
        json={"rule": {"all": [{"facet": "category", "op": "in", "values": ["no_such_value"]}]}},
    )
    assert response.status_code == 422
    assert "no_such_value" in response.text


def test_a_refused_rule_patch_leaves_the_chart_unchanged(
    api_client: TestClient, api_database_url: str
) -> None:
    """The 422 is raised BEFORE `chart.rule` is assigned. A reordering that
    assigned first would leave a half-applied edit on the session, and the next
    read would serve a rule the API had just refused."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-patch-refused-intact", SOFTWARE_RULE)

    refused = api_client.patch(
        f"/api/spending/{chart_id}",
        json={"rule": {"all": [{"facet": "category", "op": "in", "values": ["ghost"]}]}},
    )

    assert refused.status_code == 422
    assert api_client.get(f"/api/spending/{chart_id}").json()["rule"] == SOFTWARE_RULE


def test_patching_a_rule_with_an_empty_value_list_is_a_422(api_client: TestClient) -> None:
    """The PATCH sibling of `test_saving_a_rule_with_an_empty_value_list_is_a_422`.
    `rule_predicate` raises `RuleError` on it; unreported it would be a 500 the
    first time the edited chart was drawn."""
    chart_id = _save_chart(api_client, "api-patch-empty-values", {"all": []})
    response = api_client.patch(
        f"/api/spending/{chart_id}",
        json={"rule": {"all": [{"facet": "category", "op": "in", "values": []}]}},
    )
    assert response.status_code == 422
    assert "category" in response.text


def test_patching_to_an_empty_rule_widens_the_chart_to_everything(
    api_client: TestClient, api_database_url: str
) -> None:
    """`{"all": []}` is a legitimate saved state, not a mistake: it is what the
    seeded "All spending" card stores. So the API accepts it, and the guard
    against reaching it by accident is a confirmation in the editor rather than
    a refusal here. A later blanket 422 has to delete this test deliberately."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    _seed_document(api_database_url, amount="7.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-patch-widen", SOFTWARE_RULE)
    narrow = Decimal(api_client.get(f"/api/spending/{chart_id}/data").json()["total"])

    patched = api_client.patch(f"/api/spending/{chart_id}", json={"rule": {"all": []}})

    assert patched.status_code == 200, patched.text
    widened = Decimal(api_client.get(f"/api/spending/{chart_id}/data").json()["total"])
    assert widened > narrow


def test_patching_a_chart_with_its_own_unchanged_name_is_a_200_not_a_409(
    api_client: TestClient,
) -> None:
    """A form-style editor PATCHes the whole object, including the name it did
    not change. `_require_free_name` excludes the chart itself, but that
    exclusion was only ever asserted in its conflicting direction."""
    name = f"api-patch-self-name-{uuid.uuid4().hex[:8]}"
    chart_id = _save_chart(api_client, name, {"all": []})

    response = api_client.patch(
        f"/api/spending/{chart_id}", json={"name": name, "default_grain": "quarter"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == name


def test_patching_a_rule_leaves_question_text_untouched(
    api_client: TestClient, api_database_url: str
) -> None:
    """A deliberate product decision, pinned: the rule editor does not rewrite
    the plain-language question a chart is headed by. The clause rows are the
    authoritative statement of what the chart matches, and only the owner can
    say whether a reworded rule still answers the same question. Changing this
    means deleting this test on purpose."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-patch-question-text", SOFTWARE_RULE)
    before = api_client.get(f"/api/spending/{chart_id}").json()["question_text"]

    services = {"all": [{"facet": "category", "op": "in", "values": ["services"]}]}
    patched = api_client.patch(f"/api/spending/{chart_id}", json={"rule": services})

    assert patched.status_code == 200, patched.text
    assert patched.json()["question_text"] == before


def test_patching_default_split_onto_a_chart_created_without_one(
    api_client: TestClient, api_database_url: str
) -> None:
    """A chart saved with `default_split: null` can be given a split axis. The
    UI had no path to this, which is the same gap as the uneditable rule in a
    stronger form; the API always supported it."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    chart_id = _save_chart(api_client, "api-patch-gain-split", SOFTWARE_RULE, default_split=None)
    assert api_client.get(f"/api/spending/{chart_id}/data").json()["splits"] == []

    patched = api_client.patch(f"/api/spending/{chart_id}", json={"default_split": "category"})

    assert patched.status_code == 200, patched.text
    assert patched.json()["default_split"] == "category"
    assert api_client.get(f"/api/spending/{chart_id}/data").json()["splits"] != []


def test_patching_default_split_to_null_clears_it(
    api_client: TestClient, api_database_url: str
) -> None:
    """The other side of the sentinel: an explicit `null` clears the axis, an
    absent key leaves it alone. A client that omitted the key to mean "clear"
    would silently keep the old split."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(
        api_client, "api-patch-clear-split", SOFTWARE_RULE, default_split="category"
    )

    kept = api_client.patch(f"/api/spending/{chart_id}", json={"default_grain": "year"})
    assert kept.json()["default_split"] == "category"

    cleared = api_client.patch(f"/api/spending/{chart_id}", json={"default_split": None})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["default_split"] is None


def test_patching_an_unknown_default_split_is_a_422_naming_it(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-patch-bad-split", {"all": []})
    response = api_client.patch(
        f"/api/spending/{chart_id}", json={"default_split": "no_such_facet"}
    )
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_one_chart_can_be_read_by_id(api_client: TestClient) -> None:
    """The workspace loads one chart. Without this it has to page the list
    looking for a row, which breaks the moment there are more than `limit`."""
    chart_id = _save_chart(api_client, "api-read-by-id", {"all": []})

    response = api_client.get(f"/api/spending/{chart_id}")

    assert response.status_code == 200, response.text
    listed = api_client.get("/api/spending?limit=100").json()["charts"]
    assert response.json() == next(c for c in listed if c["id"] == chart_id)


def test_reading_an_unknown_chart_is_a_404(api_client: TestClient) -> None:
    response = api_client.get("/api/spending/999999")
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_saving_a_rule_with_an_empty_value_list_is_a_422(api_client: TestClient) -> None:
    """`rule_predicate` raises `RuleError` on it; unreported it would be a 500
    the first time the chart was drawn."""
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-empty-values",
            "question_text": "q",
            "rule": {"all": [{"facet": "category", "op": "in", "values": []}]},
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 422
    assert "category" in response.text


#: Two `in` clauses on one facet. A document carries at most one value per
#: facet, so this can never match anything — the chart reads "you spent
#: nothing", which is the one answer this feature must never give by accident.
_SAME_FACET_TWICE: dict[str, object] = {
    "all": [
        {"facet": "category", "op": "in", "values": ["software"]},
        {"facet": "category", "op": "in", "values": ["services"]},
    ]
}


def test_saving_two_in_clauses_on_one_facet_is_a_422_naming_the_facet(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-same-facet-post",
            "question_text": "q",
            "rule": _SAME_FACET_TWICE,
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 422
    assert "category" in response.text


def test_patching_to_two_in_clauses_on_one_facet_is_a_422(
    api_client: TestClient, api_database_url: str
) -> None:
    """The editor's own path: an "add filter" button makes this shape reachable
    in two clicks, where previously only a drafted rule could produce it."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-same-facet-patch", {"all": []})
    response = api_client.patch(f"/api/spending/{chart_id}", json={"rule": _SAME_FACET_TWICE})
    assert response.status_code == 422
    assert "category" in response.text


def test_previewing_two_in_clauses_on_one_facet_is_a_422(
    api_client: TestClient, api_database_url: str
) -> None:
    """So the editor learns about it before applying, not after."""
    _seed_vocabulary(api_database_url)
    response = _preview(api_client, _SAME_FACET_TWICE)
    assert response.status_code == 422
    assert "category" in response.text


def test_two_not_in_clauses_on_one_facet_are_accepted(
    api_client: TestClient, api_database_url: str
) -> None:
    """The check is deliberately narrow. Two exclusions on one facet are a
    legitimate intersection — "not software and not services" is answerable and
    common. A broader shape check reddens here, which is the point."""
    _seed_vocabulary(api_database_url)
    rule = {
        "all": [
            {"facet": "category", "op": "not_in", "values": ["software"]},
            {"facet": "category", "op": "not_in", "values": ["services"]},
        ]
    }
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-same-facet-not-in",
            "question_text": "q",
            "rule": rule,
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text


def test_a_chart_with_two_in_clauses_on_one_facet_still_loads_by_id(
    api_client: TestClient, api_database_url: str
) -> None:
    """The repairability guarantee, and the reason the refusal lives in
    `_validate_rule` rather than in a `Rule` model validator.

    A chart saved before the check existed must still be *loadable*, because the
    rule editor is the tool for fixing it. `GET /api/spending/{id}` runs only
    `Rule.model_validate`, never `_validate_rule`, so it survives.

    MUTATION: move the check into a `Rule` validator and this reddens — the
    editor can no longer open the chart it exists to repair."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-same-facet-legacy", {"all": []})

    async def write_bad_rule(session: AsyncSession) -> None:
        await session.execute(
            update(Chart).where(Chart.id == chart_id).values(rule=_SAME_FACET_TWICE)
        )

    _run(api_database_url, write_bad_rule)

    response = api_client.get(f"/api/spending/{chart_id}")

    assert response.status_code == 200, response.text
    assert response.json()["rule"] == _SAME_FACET_TWICE


def test_saving_a_rule_with_an_unknown_clause_key_is_a_422(api_client: TestClient) -> None:
    """`Clause` forbids extras so a mis-named `op` cannot be read as the default
    `in`. This asserts the refusal reaches the client as a 422 it can act on,
    rather than a 500."""
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-unknown-clause-key",
            "question_text": "q",
            "rule": {"all": [{"facet": "category", "operator": "not_in", "values": ["software"]}]},
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 422


def test_a_missing_chart_is_a_404(api_client: TestClient) -> None:
    assert api_client.get("/api/spending/999999/data").status_code == 404
    assert api_client.get("/api/spending/999999/cell?period=2026-03-01").status_code == 404
    assert api_client.delete("/api/spending/999999").status_code == 404


# --- data and the footer -----------------------------------------------------


def test_a_saved_chart_returns_data_with_its_footer(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/data?grain=month")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "cells" in body and "total" in body
    # Present even when empty: an absent footer and an empty one are different
    # claims, and only one of them is "nothing was excluded".
    assert body["footer"]["excluded"] == []
    assert body["footer"]["netted_refunds"] == "0.00"


def test_the_footer_carries_all_eight_fields_including_unclassified(
    api_client: TestClient, api_database_url: str
) -> None:
    """MUTATION: drop `unclassified` (or `unaccounted`) from `_footer_out` and
    this reddens.

    `unclassified` is money with an amount and an *undecided* kind — the class
    of document the archive has most of. Before it existed such a document
    appeared in no footer line at all, which is precisely what §9.4 forbids.
    """
    _seed_document(api_database_url, amount="40.00", kind=None)
    _seed_document(api_database_url, amount="5000.00", kind=AmountKind.COVERAGE_LIMIT)
    _seed_document(api_database_url, amount="15.00", kind=AmountKind.PAYMENT_MADE, day=None)
    chart_id = _save_chart(api_client, "api-eight", {"all": []})
    footer = api_client.get(f"/api/spending/{chart_id}/data").json()["footer"]
    assert set(footer) == {
        "netted_refunds",
        "refund_count",
        "excluded",
        "unclassified",
        "uncategorised",
        "undated",
        "unaccounted",
        "unconvertible",
    }
    assert footer["unclassified"] == {
        "amount_kind": "unclassified",
        "amount": "40.00",
        "documents": 1,
    }
    assert footer["excluded"] == [
        {"amount_kind": "coverage_limit", "amount": "5000.00", "documents": 1}
    ]
    # Three groups carrying DIFFERENT money, so a transposed wiring
    # (`undated=_group(footer.unclassified)`) fails here rather than passing on
    # the strength of the keys existing.
    assert footer["undated"] == {"amount_kind": "undated", "amount": "15.00", "documents": 1}
    assert footer["uncategorised"] is None
    assert footer["unaccounted"] is None


def test_the_footer_reports_uncategorised_money_for_a_facet_bearing_rule(
    api_client: TestClient, api_database_url: str
) -> None:
    """MUTATION: pass `facets_in_rule=set()` to `chart_footer` and this reddens.

    `chart_footer` trusts its caller for that set, so an empty one turns §9.4's
    headline guarantee off silently: the unlabelled document below stops being
    reported anywhere, which is exactly the disappearance the footer exists to
    prevent.
    """
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="60.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    _seed_document(api_database_url, amount="25.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-uncategorised", SOFTWARE_RULE)
    body = api_client.get(f"/api/spending/{chart_id}/data").json()
    assert body["total"] == "60.00"
    assert body["footer"]["uncategorised"] == {
        "amount_kind": "uncategorised",
        "amount": "25.00",
        "documents": 1,
    }


def test_a_refund_is_netted_into_the_total_and_reported_in_the_header(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_document(api_database_url, amount="50.00", kind=AmountKind.PAYMENT_MADE)
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.REFUND)
    chart_id = _save_chart(api_client, "api-refund", {"all": []})
    body = api_client.get(f"/api/spending/{chart_id}/data").json()
    assert body["total"] == "40.00"
    assert body["footer"]["netted_refunds"] == "10.00"
    assert body["footer"]["refund_count"] == 1
    # A refund is IN the total; reporting it under "excluded" would read as
    # money the chart ignored.
    assert body["footer"]["excluded"] == []


def test_unconvertible_money_is_merged_into_one_line_per_currency(
    api_client: TestClient, api_database_url: str
) -> None:
    """`query.py` and `footer.py` report different rows in the same currency.

    Concatenating the two lists would show one currency as two separate
    problems; this asserts the merge, and that `documents` is reported beside
    the amount (a payment and an equal refund net to zero while two documents
    are still unrepresented).
    """
    _seed_document(
        api_database_url,
        amount="30.00",
        kind=AmountKind.PAYMENT_MADE,
        currency=UNCONVERTIBLE_CURRENCY,
    )
    _seed_document(
        api_database_url,
        amount="80.00",
        kind=AmountKind.COVERAGE_LIMIT,
        currency=UNCONVERTIBLE_CURRENCY,
    )
    chart_id = _save_chart(api_client, "api-unconvertible", {"all": []})
    body = api_client.get(f"/api/spending/{chart_id}/data").json()
    assert body["total"] == "0.00"
    assert body["footer"]["unconvertible"] == [
        {"currency": UNCONVERTIBLE_CURRENCY, "amount": "110.00", "documents": 2}
    ]


def test_the_data_endpoint_names_an_unknown_split_axis_rather_than_500ing(
    api_client: TestClient,
) -> None:
    """A facet deleted after the chart was saved. §12: the chart renders an
    error NAMING the value, never an empty chart — an empty chart is
    indistinguishable from "you spent nothing on that"."""
    chart_id = _save_chart(api_client, "api-split", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/data?split=no_such_facet")
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_the_data_endpoint_names_a_facet_value_deleted_after_the_chart_was_saved(
    api_client: TestClient, api_database_url: str
) -> None:
    """Spec §12, the row above the split axis: a rule referencing a deleted
    value renders an error naming the value, not an empty chart."""
    _seed_vocabulary(api_database_url)
    chart_id = _save_chart(api_client, "api-deleted-value", SOFTWARE_RULE)

    async def drop_value(session: AsyncSession) -> None:
        await session.execute(delete(FacetValue).where(FacetValue.key == "software"))

    _run(api_database_url, drop_value)
    response = api_client.get(f"/api/spending/{chart_id}/data")
    assert response.status_code == 422
    assert "software" in response.text


def test_an_unknown_grain_is_a_422_naming_it(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-grain", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/data?grain=fortnight")
    assert response.status_code == 422
    assert "fortnight" in response.text


# --- split value resolution (labels and colours) -----------------------------


def test_a_facet_split_resolves_value_keys_to_display_labels(
    api_client: TestClient, api_database_url: str
) -> None:
    """`spend_facts.labels` maps a facet key to a value *key*, so an unresolved
    legend reads `software`. §2.3: the legend carries names."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    chart_id = _save_chart(api_client, "api-splits-facet", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    assert {s["value"]: s["label"] for s in body["splits"]} == {"software": "Software"}


def test_a_sender_split_resolves_ids_to_names(
    api_client: TestClient, api_database_url: str
) -> None:
    """The engine emits `CAST(sf.sender_id AS text)`, so without resolution the
    legend reads `41`. The id stays as `value` because `/cell` round-trips it."""
    name = f"{VENDOR_A} {uuid.uuid4()}"
    _seed_sender_document(api_database_url, sender=name, amount="10.00")
    chart_id = _save_chart(api_client, "api-splits-sender", {}, default_split="sender")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    named = [s for s in body["splits"] if s["label"] == name]
    assert len(named) == 1
    assert named[0]["value"].isdigit(), "value stays the id /cell must be sent back"


def test_the_unlabelled_bucket_is_named_by_the_axis(
    api_client: TestClient, api_database_url: str
) -> None:
    """`split_value` is null both for "no value for this facet" and for "no
    sender". A client cannot invent either name; the API supplies it."""
    _seed_vocabulary(api_database_url)
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    facet_chart = _save_chart(api_client, "api-splits-null-facet", {}, default_split="category")
    sender_chart = _save_chart(api_client, "api-splits-null-sender", {}, default_split="sender")

    by_facet = api_client.get(f"/api/spending/{facet_chart}/data").json()
    by_sender = api_client.get(f"/api/spending/{sender_chart}/data").json()

    # Without this, an empty result (e.g. the seed silently failing) would
    # satisfy the `in` checks below vacuously: `splits == []` contains
    # neither pair, but so would a `splits` list that happened to omit them.
    # Asserting the seeded document actually produced one cell each pins the
    # test to real data.
    assert len(by_facet["cells"]) == 1
    assert len(by_sender["cells"]) == 1
    assert (None, "Uncategorised") in [(s["value"], s["label"]) for s in by_facet["splits"]]
    assert (None, "No sender") in [(s["value"], s["label"]) for s in by_sender["splits"]]


def test_a_split_value_carries_its_stored_colour_and_null_when_unset(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    for value in ("software", "services"):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={"category": value},
        )

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
        )

    _run(api_database_url, paint)
    chart_id = _save_chart(api_client, "api-splits-colour", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    colours = {s["value"]: s["colour"] for s in body["splits"]}
    assert colours["software"] == "#1f77b4"
    assert colours["services"] is None


def test_a_split_value_with_no_sender_row_falls_back_to_the_raw_value(
    api_client: TestClient, api_database_url: str
) -> None:
    """A sender referenced by a saved chart's data can be deleted after the
    fact (a document's `sender_id` then becomes `NULL`, but `/cell`'s
    `split_value` is a client-supplied query parameter that is never
    validated against `spend_facts` at all — so this needs no fixture
    surgery to reach: nothing has to have ever named 999999 for a client to
    ask for it.
    """
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-splits-orphan", {}, default_split="sender")

    body = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "sender", "split_value": "999999"},
    ).json()

    assert body["label"] == "999999"


#: Sender-split `split_value`s that are not a `senders.id`-shaped integer, and
#: are therefore now **refused** rather than answered with `0.00`. These
#: previously came back 200 with the raw string as their label; that assertion
#: was rewritten deliberately, not deleted, when
#: `_refuse_unbucketable_split_value` landed. What has NOT changed is the
#: property this list was built for — none of them may 500 — and a 422 honours
#: it as fully as the old 200 did.
#:
#: The classes, each of which has crashed `_resolve_splits`'s sender branch or
#: would slip past a guard narrower than `int()`:
#: - a Unicode "digit" `str.isdigit()` accepts but `int()` rejects (superscript
#:   two, category No) — the guard `isdigit() and int(value) <= MAX` still
#:   raises on this one;
#: - the empty string, an edge case of `int()`'s own grammar;
#: - a non-integer string;
#: - a value out of `senders.id`'s `int4` range.
_REFUSED_SPLIT_VALUES = ["²", "", "not-an-id", "99999999999"]

#: The other half, and the reason the guard **parses** rather than pattern-
#: matches. `int()` accepts every one of these, so each names a sender id that
#: could genuinely exist, and refusing them would be a false refusal: a
#: `+`-prefixed value, and fullwidth and Arabic-indic decimal digits — forms
#: `senders.id` never renders on the wire but `int()` reads. Kept so a fix that
#: over-corrects to ASCII-only parsing reds here instead of quietly refusing
#: cells the chart drew.
_PARSEABLE_SPLIT_VALUES = ["+5", "１２３", "٣"]  # noqa: RUF001


@pytest.mark.parametrize("split_value", _REFUSED_SPLIT_VALUES)
def test_a_split_value_that_is_not_a_sender_id_is_a_422_naming_it(
    api_client: TestClient, api_database_url: str, split_value: str
) -> None:
    """`split_value` is the one `/cell` argument a client fully controls, and an
    unbucketable one used to answer `total: "0.00"` with no payments — "you
    spent nothing here", the same silence the `period` check removed from this
    very route.

    The refusal names the offending value, as `period`'s does, so the caller can
    see what it sent. Never a 500: that is the property this input list was
    originally written to pin, and it still holds.
    """
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(
        api_client, f"api-splits-refused-{uuid.uuid4()}", {}, default_split="sender"
    )

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "sender", "split_value": split_value},
    )

    assert response.status_code == 422, response.text
    if split_value:
        assert split_value in response.text


@pytest.mark.parametrize("split_value", _PARSEABLE_SPLIT_VALUES)
def test_a_split_value_int_can_parse_is_still_answered(
    api_client: TestClient, api_database_url: str, split_value: str
) -> None:
    """The accepted half of the pair, and the guard's narrowness check.

    Each of these parses to an in-range integer, so each names a sender id that
    could exist. The guard judges the *shape* and never whether the bucket was
    drawn, so it must let them through and answer with the raw value as the
    label — a guard tightened to ASCII digits reds here, which is the point.
    """
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(
        api_client, f"api-splits-parseable-{uuid.uuid4()}", {}, default_split="sender"
    )

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "sender", "split_value": split_value},
    )

    assert response.status_code == 200, response.text
    assert response.json()["label"] == split_value


def test_a_split_value_on_a_chart_with_no_split_axis_is_a_422(
    api_client: TestClient, api_database_url: str
) -> None:
    """The cheapest exact check, and one issue #127 did not mention.

    An unsplit chart's split expression is `_SPLIT_NONE` — `CAST(NULL AS text)`
    — so `IS NOT DISTINCT FROM` is false for *any* non-null value, whatever it
    says. No query is needed to know that, and no false refusal is possible.

    MUTATION: delete the `query.split is None` branch and this reddens with a
    200 and `total: "0.00"` — a drill reporting the chart's own money as nothing.
    """
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, f"api-splits-unsplit-{uuid.uuid4()}", {})

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split_value": "anything-at-all"},
    )

    assert response.status_code == 422, response.text
    assert "no split axis" in response.text


def test_an_empty_split_value_is_a_422_on_a_facet_split_too(
    api_client: TestClient, api_database_url: str
) -> None:
    """`?split_value=` is the empty string, not an omitted argument.

    The sender branch catches it incidentally, via `int("")`. The facet branch
    did not, so it reached the SQL and produced the `0.00` silence — a facet
    value key is a slug and the "no value" bucket is NULL, so `""` can bucket
    nothing on any axis. Checked ahead of the axis branches for that reason.

    MUTATION: move the check inside the sender branch and this reddens with a
    200 and an empty panel.
    """
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    chart_id = _save_chart(
        api_client, f"api-splits-empty-{uuid.uuid4()}", {}, default_split="category"
    )

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "category", "split_value": ""},
    )

    assert response.status_code == 422, response.text
    assert "empty" in response.text


def test_an_unsplit_chart_still_opens_its_cell_when_no_split_value_is_sent(
    api_client: TestClient, api_database_url: str
) -> None:
    """The accepted half of the check above: omitting `split_value` is how an
    unsplit chart's single bucket is addressed, and it must keep working."""
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, f"api-splits-unsplit-ok-{uuid.uuid4()}", {})

    response = api_client.get(f"/api/spending/{chart_id}/cell", params={"period": "2026-03-01"})

    assert response.status_code == 200, response.text
    assert response.json()["total"] == "10.00"


def test_a_split_value_in_non_canonical_form_still_resolves_to_the_senders_name(
    api_client: TestClient, api_database_url: str
) -> None:
    """`int()` accepts forms `senders.id` never renders on the wire — a
    leading zero, here. Resolution must key on the *parsed* id, not the raw
    string: a lookup keyed on the string would only ever match `str(row.id)`
    exactly, so a real sender sent in a non-canonical form would mislabel to
    the raw input instead of the sender's name."""
    name = f"{VENDOR_A} {uuid.uuid4()}"
    _document_id, sender_id = _seed_sender_document(api_database_url, sender=name, amount="10.00")
    chart_id = _save_chart(
        api_client, f"api-splits-canonical-{uuid.uuid4()}", {}, default_split="sender"
    )

    body = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={
            "period": "2026-03-01",
            "split": "sender",
            "split_value": f"00{sender_id}",
        },
    ).json()

    assert body["label"] == name


def test_a_cell_carries_its_own_label_and_colour(
    api_client: TestClient, api_database_url: str
) -> None:
    """So a drilled panel can title itself without re-reading /data."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
        )

    _run(api_database_url, paint)
    chart_id = _save_chart(api_client, "api-cell-label", SOFTWARE_RULE, default_split="category")
    data = api_client.get(f"/api/spending/{chart_id}/data").json()
    cell = next(c for c in data["cells"] if c["split_value"] == "software")

    body = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={
            "period": cell["period"],
            "split_value": cell["split_value"],
            "grain": data["grain"],
            "split": data["split"],
            "currency": data["currency"],
        },
    ).json()

    assert body["label"] == "Software"
    assert body["colour"] == "#1f77b4"


def test_an_unsplit_chart_has_no_split_values(
    api_client: TestClient, api_database_url: str
) -> None:
    """`split_value` is null for an unsplit chart too, and that is not a bucket
    needing a name — it is the absence of an axis."""
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-splits-none", {})

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    assert body["split"] is None
    assert body["splits"] == []


#: Five buckets rather than two: a two-element `set()` agrees with insertion
#: order about half the time, so a mutation to `set()` there would only be
#: caught by chance. Five gives 1-in-120 accidental agreement instead.
_LEGEND_ORDER_VALUES = ("software", "services", "hardware", "travel", "office")


def test_the_legend_order_matches_the_cells(api_client: TestClient, api_database_url: str) -> None:
    """De-duplication preserves the engine's ordering. A `set()` would not, and
    a legend ordered differently from the chart is a legend that mislabels it."""
    _seed_vocabulary(api_database_url, values=_LEGEND_ORDER_VALUES)
    for value in _LEGEND_ORDER_VALUES:
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={"category": value},
        )
    chart_id = _save_chart(api_client, "api-splits-order", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    first_seen: list[str | None] = []
    for cell in body["cells"]:
        if cell["split_value"] not in first_seen:
            first_seen.append(cell["split_value"])
    # Without this, an empty `cells`/`splits` pair (e.g. the seed silently
    # failing) would satisfy the equality below vacuously.
    assert len(first_seen) == len(_LEGEND_ORDER_VALUES)
    assert [s["value"] for s in body["splits"]] == first_seen


# --- the drill-through -------------------------------------------------------


def test_a_cell_lists_exactly_the_payments_the_bar_summed(
    api_client: TestClient, api_database_url: str
) -> None:
    """MUTATION: change any argument `/cell` forwards — drop the `since` bound,
    or the split — and this reddens.

    The two documents sit in the same month, so a `from` bound the panel
    ignored would list the second one too and the panel would stop matching the
    bar. That is the drift `query.py` cannot catch: it shares its predicate
    between the two functions, but only when they are asked the same question.
    """
    _seed_vocabulary(api_database_url)
    for amount, day in (("60.00", date(2026, 3, 5)), ("40.00", date(2026, 3, 20))):
        _seed_document(
            api_database_url,
            amount=amount,
            kind=AmountKind.PAYMENT_MADE,
            day=day,
            labels={"category": "software"},
        )
    chart_id = _save_chart(api_client, "api-cell", SOFTWARE_RULE)
    window = "split=category&from=2026-03-10"
    data = api_client.get(f"/api/spending/{chart_id}/data?{window}").json()
    assert data["cells"] == [
        {"period": "2026-03-01", "split_value": "software", "total": "40.00", "payments": 1}
    ]
    # The response echoes the RESOLVED arguments; a client drills with those.
    assert (data["since"], data["split"], data["grain"], data["currency"]) == (
        "2026-03-10",
        "category",
        "month",
        "EUR",
    )
    cell = api_client.get(
        f"/api/spending/{chart_id}/cell?period=2026-03-01&split_value=software&{window}"
    )
    assert cell.status_code == 200, cell.text
    assert cell.json()["total"] == data["cells"][0]["total"]
    payments = cell.json()["payments"]
    assert len(payments) == data["cells"][0]["payments"]
    assert sum(Decimal(payment["total"]) for payment in payments) == Decimal(
        data["cells"][0]["total"]
    )
    assert [document["is_canonical"] for document in payments[0]["documents"]] == [True]


def test_the_headline_is_the_sum_of_the_cells_the_client_reads(
    api_client: TestClient, api_database_url: str, half_rate_currencies: tuple[str, str]
) -> None:
    """§2.5 is a promise about what the CLIENT reads, not about the engine.

    Each document converts to `10.005`, so each month's cell rounds up to
    `10.01` while the exact total is `20.01`. Quantising the cells and the total
    independently prints a headline a cent below its own bars; building the
    total from the rendered cells makes them agree by construction.
    """
    source, display = half_rate_currencies
    for day in (date(2026, 3, 5), date(2026, 4, 5)):
        _seed_document(
            api_database_url, amount="20.01", kind=AmountKind.PAYMENT_MADE, day=day, currency=source
        )
    chart_id = _save_chart(api_client, "api-rounding", {"all": []})
    body = api_client.get(f"/api/spending/{chart_id}/data?currency={display}").json()
    assert [cell["total"] for cell in body["cells"]] == ["10.01", "10.01"]
    assert body["total"] == "20.02"
    assert Decimal(body["total"]) == sum(Decimal(cell["total"]) for cell in body["cells"])


def test_a_cells_payments_still_sum_to_the_bar_after_rounding(
    api_client: TestClient, api_database_url: str, half_rate_currencies: tuple[str, str]
) -> None:
    """Two payments of `10.005` in one cell: rounded on their own they print
    `10.01` twice under a bar of `20.01`, and the panel stops adding up to the
    thing it was opened from."""
    source, display = half_rate_currencies
    for _ in range(2):
        _seed_document(
            api_database_url,
            amount="20.01",
            kind=AmountKind.PAYMENT_MADE,
            day=date(2026, 3, 5),
            currency=source,
        )
    chart_id = _save_chart(api_client, "api-cell-rounding", {"all": []})
    data = api_client.get(f"/api/spending/{chart_id}/data?currency={display}").json()
    assert data["cells"][0]["total"] == "20.01"
    cell = api_client.get(
        f"/api/spending/{chart_id}/cell?period=2026-03-01&currency={display}"
    ).json()
    assert sorted(payment["total"] for payment in cell["payments"]) == ["10.00", "10.01"]
    assert cell["total"] == data["cells"][0]["total"]
    assert sum(Decimal(payment["total"]) for payment in cell["payments"]) == Decimal(cell["total"])


def test_a_malformed_currency_is_a_422_not_an_empty_looking_chart(
    api_client: TestClient,
) -> None:
    """`currency=1x2` has no rate, so without the pattern check every amount
    reports as unconvertible — an empty-looking chart where §12 wants an error."""
    chart_id = _save_chart(api_client, "api-currency", {"all": []})
    assert api_client.get(f"/api/spending/{chart_id}/data?currency=1x2").status_code == 422
    assert (
        api_client.get(f"/api/spending/{chart_id}/cell?period=2026-03-01&currency=1x2").status_code
        == 422
    )


@pytest.mark.parametrize(
    ("grain", "boundary"),
    [
        # 2026-03-15 is a Sunday, so its ISO week began on the 9th; the four
        # boundaries are all different, and the message has to name the one
        # belonging to the grain that was asked for.
        ("week", "2026-03-09"),
        ("month", "2026-03-01"),
        ("quarter", "2026-01-01"),
        ("year", "2026-01-01"),
    ],
)
def test_a_period_off_the_grain_boundary_is_a_422_naming_the_right_one(
    api_client: TestClient, grain: str, boundary: str
) -> None:
    """`chart_cell` filters `date_trunc(grain, date) = period`, so a mid-bucket
    period matches nothing — and an empty panel under a non-empty bar reads as
    "you spent nothing here".

    All four grains: the boundary comes from `charts.query.period_start`, which
    asks Postgres, and a single-grain test would leave three of the four
    answers unexercised.
    """
    chart_id = _save_chart(api_client, f"api-period-{grain}", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/cell?period=2026-03-15&grain={grain}")
    assert response.status_code == 422
    assert boundary in response.text
    assert grain in response.text


@pytest.mark.parametrize(
    ("grain", "boundary"),
    [
        ("week", "2026-03-09"),
        ("month", "2026-03-01"),
        ("quarter", "2026-01-01"),
        ("year", "2026-01-01"),
    ],
)
def test_a_period_on_the_grain_boundary_is_accepted(
    api_client: TestClient, grain: str, boundary: str
) -> None:
    """The other half of the check: a boundary the chart really draws must not
    be refused. A validator that answered 422 for everything would satisfy the
    test above on its own."""
    chart_id = _save_chart(api_client, f"api-period-ok-{grain}", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/cell?period={boundary}&grain={grain}")
    assert response.status_code == 200, response.text


def test_a_cell_of_an_unsplit_chart_opens_its_unlabelled_bucket(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_document(api_database_url, amount="12.50", kind=AmountKind.PAYMENT_DUE)
    chart_id = _save_chart(api_client, "api-cell-null", {"all": []})
    body = api_client.get(f"/api/spending/{chart_id}/cell?period=2026-03-01").json()
    assert [payment["total"] for payment in body["payments"]] == ["12.50"]


# --- preview -----------------------------------------------------------------
#
# `/spending/preview` answers a rule the caller already has. Its sibling
# `/spending/draft` answers a *question*, which costs a model call — so the two
# differ in exactly the way the rule editor needs, and the tests below pin both
# halves of that: the answer is real, and no model was asked for it.


def _preview(api_client: TestClient, rule: dict[str, object], **kwargs: object) -> httpx.Response:
    body: dict[str, object] = {"rule": rule, "display_currency": "EUR"}
    body.update(kwargs)
    return api_client.post("/api/spending/preview", json=body)


def test_previewing_a_rule_answers_it_without_saving_a_chart(
    api_client: TestClient, api_database_url: str
) -> None:
    """The route's whole purpose: a real answer with no `charts` row behind it.
    `chart_id: null` is the wire-level proof, and the list assertion is the
    storage-level one."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="60.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    before = {c["id"] for c in api_client.get("/api/spending?limit=100").json()["charts"]}

    response = _preview(api_client, SOFTWARE_RULE)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == "60.00"
    assert body["chart_id"] is None
    after = {c["id"] for c in api_client.get("/api/spending?limit=100").json()["charts"]}
    assert after == before


def test_previewing_an_unknown_facet_is_a_422_naming_it(api_client: TestClient) -> None:
    """MUTATION: drop the `_validate_rule` call from the handler and this reddens
    with a 200 and an empty-looking chart. `/spending/draft` legitimately skips
    that call — `filter_drafted_rule` guarantees vocabulary membership by
    construction — so a handler copy-pasted from it inherits the omission and
    answers "you spent nothing" to a rule the API should have refused."""
    response = _preview(
        api_client, {"all": [{"facet": "no_such_facet", "op": "in", "values": ["x"]}]}
    )
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_previewing_a_value_deleted_after_the_editor_loaded_is_a_422_naming_it(
    api_client: TestClient, api_database_url: str
) -> None:
    """The editor is the repair tool for a rotted rule, so it will preview one.
    Naming the value is what tells the owner which chip to fix."""
    _seed_vocabulary(api_database_url)

    async def drop_value(session: AsyncSession) -> None:
        await session.execute(delete(FacetValue).where(FacetValue.key == "software"))

    _run(api_database_url, drop_value)

    response = _preview(api_client, SOFTWARE_RULE)

    assert response.status_code == 422
    assert "software" in response.text


def test_previewing_an_unknown_split_axis_is_a_422_naming_it(api_client: TestClient) -> None:
    response = _preview(api_client, {"all": []}, split="no_such_facet")
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_previewing_an_empty_rule_answers_with_everything(
    api_client: TestClient, api_database_url: str
) -> None:
    """Deliberately a 200, not a 422. `PATCH` accepts an empty rule — it is the
    seeded "All spending" chart's own saved state — so a preview that refused it
    would be useless at the one moment the owner most needs it: just after
    removing their last clause, about to widen the chart to the whole archive.
    Showing them that number IS the warning."""
    _seed_document(api_database_url, amount="12.00", kind=AmountKind.PAYMENT_MADE)

    response = _preview(api_client, {"all": []})

    assert response.status_code == 200, response.text
    assert Decimal(response.json()["total"]) > 0


def test_previewing_with_an_empty_split_string_means_no_split_axis(
    api_client: TestClient, api_database_url: str
) -> None:
    """The client sends `split` on every request so an absent key can never be
    mistaken for a default. `""` is how it says "no axis"; without the
    normalisation it reaches `_validate_split` and 422s as an unknown facet."""
    _seed_vocabulary(api_database_url)

    response = _preview(api_client, SOFTWARE_RULE, split="")

    assert response.status_code == 200, response.text
    assert response.json()["split"] is None
    assert response.json()["splits"] == []


def test_previewing_honours_the_window_the_client_sends(
    api_client: TestClient, api_database_url: str
) -> None:
    """The editor forwards the workspace toolbar's `from`/`to`, so the wire
    names have to be `from`/`to` — `/data` spells this window that way on the
    query string and the two must not diverge.

    This asserts the window reaches the ANSWER, not merely that the request was
    accepted. That distinction is the whole point: a body whose date fields are
    named something the model does not bind is silently ignored, and the route
    still returns 200 with a plausible chart — one answering "all time" instead
    of the range the owner is looking at. A test that only checked the status
    code, or that mocked the client and asserted the argument object, would pass
    against exactly that bug.
    """
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        day=MARCH,
        labels={"category": "software"},
    )
    _seed_document(
        api_database_url,
        amount="500.00",
        kind=AmountKind.PAYMENT_MADE,
        day=date(2020, 1, 15),
        labels={"category": "software"},
    )

    unwindowed = _preview(api_client, SOFTWARE_RULE)
    windowed = _preview(api_client, SOFTWARE_RULE, **{"from": "2026-01-01", "to": "2026-12-31"})

    assert windowed.status_code == 200, windowed.text
    assert windowed.json()["since"] == "2026-01-01"
    assert windowed.json()["until"] == "2026-12-31"
    assert Decimal(windowed.json()["total"]) < Decimal(unwindowed.json()["total"])


def test_previewing_rejects_an_unknown_body_field(api_client: TestClient) -> None:
    """`PreviewIn` forbids extras, so a renamed or misspelled field is a 422
    rather than a silently-dropped one. Without this the window fields could go
    missing again and the route would keep answering 200."""
    response = _preview(api_client, {"all": []}, no_such_field="x")
    assert response.status_code == 422


def test_previewing_with_from_after_to_is_a_422(api_client: TestClient) -> None:
    """`/data` refuses a reversed window; `/spending/draft` does not, which is a
    small pre-existing gap. Preview follows `/data`, not its sibling."""
    response = _preview(api_client, {"all": []}, **{"from": "2026-06-01", "to": "2026-03-01"})
    assert response.status_code == 422


def test_a_preview_carries_the_full_footer(api_client: TestClient, api_database_url: str) -> None:
    """`facets_in_rule` has to reach `chart_footer` through this path too.
    `chart_footer` trusts its caller for that set, so an empty one silently
    stops reporting uncategorised money — the money the rule should have caught
    and did not, which is exactly what an owner editing a rule is looking for."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    _seed_document(api_database_url, amount="9.00", kind=AmountKind.PAYMENT_MADE)

    footer = _preview(api_client, SOFTWARE_RULE).json()["footer"]

    assert set(footer) == {
        "netted_refunds",
        "refund_count",
        "excluded",
        "unclassified",
        "uncategorised",
        "undated",
        "unaccounted",
        "unconvertible",
    }
    assert footer["uncategorised"] is not None


def test_previewing_does_not_call_the_model(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    """The reason this route exists rather than reusing `/spending/draft`, which
    calls the model on every request. `stub_anthropic` is left unconfigured, so
    any call through it raises — a preview that reached the model would 500 here
    rather than passing quietly."""
    _seed_vocabulary(api_database_url)

    response = _preview(api_client, SOFTWARE_RULE)

    assert response.status_code == 200, response.text


# --- drafting ----------------------------------------------------------------


def _draft(api_client: TestClient, question: str) -> httpx.Response:
    return api_client.post(
        "/api/spending/draft", json={"question": question, "display_currency": "EUR"}
    )


def test_drafting_a_question_the_vocabulary_can_express_previews_it(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="60.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    stub_anthropic.returns(rule=SOFTWARE_RULE, proposed_split="category")
    response = _draft(api_client, "money I spend on software")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expressible"] is True
    assert body["unknown_terms"] == []
    assert body["rule"] == SOFTWARE_RULE
    assert body["proposed_split"] == "category"
    assert body["preview"]["total"] == "60.00"


def test_a_drafted_same_facet_pair_narrows_and_says_so_instead_of_previewing_zero(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    """The end-to-end shape of issue #127's live remnant.

    `/spending/draft` is the one rule-taking path that does not call
    `_validate_rule`, and rightly so — `filter_drafted_rule` guarantees
    vocabulary membership by construction. But that guarantee says nothing about
    whether the surviving clauses can be *combined*, so before this fix a model
    drafting two `in` clauses on one facet produced `expressible: true`, a 200
    preview of an all-zero chart — "you spent nothing", the reading §12 exists
    to remove — and then a 422 the instant the owner pressed Save.

    Now the second clause is dropped and reported: the preview answers the
    narrower question truthfully, `expressible` is false so the client labels it
    an approximation, and the message gives the real reason rather than the
    vocabulary one (every value here is in the vocabulary).
    """
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="60.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    stub_anthropic.returns(rule=_SAME_FACET_TWICE, proposed_split=None)

    response = _draft(api_client, "software and services spending")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rule"] == {"all": [{"facet": "category", "op": "in", "values": ["software"]}]}
    # The point of the whole fix: a real number, not the 0.00 an unmatchable
    # conjunction would have drawn.
    assert body["preview"]["total"] == "60.00"
    assert body["expressible"] is False, "a narrowed rule is an approximation, and must say so"
    assert body["unknown_terms"] == ["category in [services]"]
    assert "could not be combined" in body["message"]
    assert "not in the vocabulary" not in body["message"], (
        "every value here IS in the vocabulary; the old wording would explain a "
        "real drop with a false reason"
    )


def test_a_drafted_same_facet_pair_saves_without_the_422_it_used_to_hit(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    """The other half, and the part a preview assertion cannot reach: the rule
    the draft proposes must be one the save path accepts.

    `_refuse_unmatchable_conjunction` runs on `POST`, so a draft that handed
    back the unmatchable pair sent the owner into a refusal they had no way to
    anticipate from the preview they were shown."""
    _seed_vocabulary(api_database_url)
    stub_anthropic.returns(rule=_SAME_FACET_TWICE, proposed_split=None)

    drafted = _draft(api_client, "software and services spending").json()["rule"]
    saved = api_client.post(
        "/api/spending",
        json={
            "name": f"drafted-narrowed-{uuid.uuid4()}",
            "rule": drafted,
            "display_currency": "EUR",
        },
    )

    assert saved.status_code == 201, saved.text


def test_the_reported_drop_list_is_capped_across_BOTH_causes(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    """`MAX_UNKNOWN_TERMS` bounds the response field, not each internal list.

    The two causes are collected separately and concatenated into one wire
    field. Capping each at `MAX_UNKNOWN_TERMS` before the join would let the
    field carry twice the constant — and the constant is what
    `QuestionDraft.vue`'s own docstring promises the client. This is
    model-authored text that gets rendered, which is why there is a cap at all.

    MUTATION: apply the slice to `unknown` and `unmatchable` separately and this
    reddens with 30 terms.
    """
    from library.api.spending import MAX_UNKNOWN_TERMS

    # Four values, not the default two: the same-facet run needs three DISTINCT
    # surviving-vocabulary values to produce three unmatchable terms, and a
    # value the vocabulary lacks would be reported as unknown instead, quietly
    # testing the wrong cause.
    _seed_vocabulary(api_database_url, values=("software", "services", "supplies", "accountancy"))
    # 18 facets the vocabulary does not contain, plus a same-facet run naming
    # values it does. Both lists are de-duplicated, so the counts are exactly
    # 18 + 3 = 21 distinct drops — one more than the cap, with both causes
    # represented, which is the only shape that can tell a union cap from two
    # separate ones.
    unknown_clauses = [
        {"facet": f"no_such_facet_{i}", "op": "in", "values": ["x"]} for i in range(18)
    ]
    same_facet = [
        {"facet": "category", "op": "in", "values": [v]}
        for v in ("software", "services", "supplies", "accountancy")
    ]
    stub_anthropic.returns(rule={"all": unknown_clauses + same_facet}, proposed_split=None)

    body = _draft(api_client, "a question naming far too many things").json()

    assert len(body["unknown_terms"]) == MAX_UNKNOWN_TERMS, (
        f"the response field must be bounded by the constant, got {len(body['unknown_terms'])}"
    )
    # Vocabulary misses keep priority: they are the actionable report, since the
    # owner can add the missing value. So all 18 survive and the unmatchable
    # list is what gets truncated.
    assert body["unknown_terms"][0] == "no_such_facet_0"
    assert sum(1 for term in body["unknown_terms"] if term.startswith("no_such_facet")) == 18
    assert sum(1 for term in body["unknown_terms"] if term.startswith("category in")) == 2


def test_a_draft_that_expresses_nothing_previews_nothing(
    api_client: TestClient, api_database_url: str, stub_anthropic: StubAnthropic
) -> None:
    """A fully-dropped draft is `rule.all == []`, which matches EVERY row.

    MUTATION: preview `result.rule` without branching on `unknown_terms` and
    this reddens — "money I spend on good vibes" comes back with the whole
    archive's total, the most confidently wrong answer the feature can give.
    """
    _seed_vocabulary(api_database_url)
    _seed_document(api_database_url, amount="61.11", kind=AmountKind.PAYMENT_MADE)
    stub_anthropic.returns(
        rule={"all": [{"facet": "good_vibes", "op": "in", "values": ["excellent"]}]}
    )
    response = _draft(api_client, "money I spend on good vibes")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expressible"] is False
    assert body["rule"] is None
    assert body["preview"] is None
    assert body["unknown_terms"] == ["good_vibes"]
    assert "good_vibes" in body["message"]
    # The archive's total must appear nowhere: no rule, no preview, no number.
    assert "61.11" not in response.text


def test_a_question_longer_than_the_cap_is_a_422_not_a_silent_trim(
    api_client: TestClient, stub_anthropic: StubAnthropic
) -> None:
    """`draft.py` truncates at 500 characters silently. A question is the
    owner's intent, not evidence, so it is refused rather than shortened."""
    stub_anthropic.returns(rule={"all": []})
    assert _draft(api_client, "x" * 501).status_code == 422


def test_drafting_without_a_model_is_a_503_not_an_empty_rule(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DraftError` must never degrade to `Rule(all=[])` — that is all spending."""
    from library.charts import draft as draft_module
    from library.config import Settings

    monkeypatch.setattr(draft_module, "get_settings", lambda: Settings(anthropic_api_key=None))
    response = _draft(api_client, "money I spend on software")
    assert response.status_code == 503


# --- spend lines (§8.4) ------------------------------------------------------


def test_replacing_an_allocation_that_does_not_sum_is_a_400_not_a_500(
    api_client: TestClient, api_document_id: int
) -> None:
    """`api_document_id` is a document with amount_total = 100.00."""
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}]},
    )
    assert response.status_code == 400
    assert "sum" in response.text.lower()


def test_replacing_an_allocation_that_sums_is_accepted(
    api_client: TestClient, api_document_id: int
) -> None:
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}, {"amount": "40.00"}]},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["lines"]) == 2


def test_a_line_label_reaches_the_read_back(
    api_client: TestClient, api_document_id: int, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={
            "lines": [
                {"amount": "60.00", "labels": {"category": "software"}},
                {"amount": "40.00", "note": "the rest"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = api_client.get(f"/api/documents/{api_document_id}/spend-lines").json()
    assert [line["labels"] for line in body["lines"]] == [{"category": "software"}, {}]
    assert body["amount_total"] == "100.00"


def test_a_line_label_outside_the_vocabulary_is_a_400_naming_it(
    api_client: TestClient, api_document_id: int, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "100.00", "labels": {"category": "nope"}}]},
    )
    assert response.status_code == 400
    assert "nope" in response.text


def test_clearing_an_allocation_returns_the_document_to_unsplit(
    api_client: TestClient, api_document_id: int
) -> None:
    api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}, {"amount": "40.00"}]},
    )
    assert api_client.delete(f"/api/documents/{api_document_id}/spend-lines").status_code == 204
    body = api_client.get(f"/api/documents/{api_document_id}/spend-lines").json()
    assert body["lines"] == []


def test_an_allocation_on_a_missing_document_is_a_404(api_client: TestClient) -> None:
    assert api_client.get("/api/documents/999999/spend-lines").status_code == 404
    assert (
        api_client.put(
            "/api/documents/999999/spend-lines", json={"lines": [{"amount": "1.00"}]}
        ).status_code
        == 404
    )


def test_a_split_documents_parts_reach_the_api_as_two_cells_of_one_total(
    api_client: TestClient, api_database_url: str, api_document_id: int
) -> None:
    """The branch's headline shape, end to end: allocate, then chart.

    `replace_lines` was reachable from the API and `chart_series` was reachable
    from the API, and nothing asked whether an allocation actually arrives in
    the chart. Split by `scope` the 100.00 document is two cells; unsplit it is
    one; either way it is one payment from one document.
    """
    _seed_vocabulary(api_database_url, "scope", ("business", "personal"))
    allocated = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={
            "lines": [
                {"amount": "60.00", "labels": {"scope": "business"}},
                {"amount": "40.00", "labels": {"scope": "personal"}},
            ]
        },
    )
    assert allocated.status_code == 200, allocated.text

    chart_id = _save_chart(api_client, "api-split-lines", {"all": []}, default_split="scope")
    body = api_client.get(f"/api/spending/{chart_id}/data").json()
    assert sorted((cell["split_value"], cell["total"]) for cell in body["cells"]) == [
        ("business", "60.00"),
        ("personal", "40.00"),
    ]
    assert body["total"] == "100.00"
    assert (body["payments"], body["documents"]) == (1, 1)

    # `split=` (the empty string) is how a client asks for no axis at all.
    flat = api_client.get(f"/api/spending/{chart_id}/data?split=").json()
    assert [(cell["split_value"], cell["total"]) for cell in flat["cells"]] == [(None, "100.00")]
    assert flat["total"] == body["total"]
    assert (flat["payments"], flat["documents"]) == (1, 1)


def test_a_split_document_can_be_counted_twice_in_the_unconvertible_documents(
    api_client: TestClient, api_database_url: str
) -> None:
    """`UnconvertibleOut.documents` is an upper bound — here is the one shape.

    A rateless document split across two lines, one matching the rule (reported
    by `query.py`, which lists rows that would have entered the total) and one
    missing the rule's label (reported by `footer.py`). The API merges the two
    lists by currency, and the same document is behind both — so `documents`
    reads 2 over one document, which the model's docstring states and nothing
    exercised. Pinned so the bound cannot quietly become an *under*statement,
    which is the direction that would matter.
    """
    _seed_vocabulary(api_database_url, "scope", ("business", "personal"))
    document_id = _seed_document(
        api_database_url,
        amount="100.00",
        kind=AmountKind.PAYMENT_MADE,
        currency=UNCONVERTIBLE_CURRENCY,
    )
    allocated = api_client.put(
        f"/api/documents/{document_id}/spend-lines",
        json={
            "lines": [
                {"amount": "60.00", "labels": {"scope": "business"}},
                {"amount": "40.00"},
            ]
        },
    )
    assert allocated.status_code == 200, allocated.text
    chart_id = _save_chart(
        api_client,
        "api-split-unconvertible",
        {"all": [{"facet": "scope", "op": "in", "values": ["business"]}]},
    )
    body = api_client.get(f"/api/spending/{chart_id}/data").json()
    assert body["total"] == "0.00", "nothing convertible reached the total"
    unconvertible = body["footer"]["unconvertible"]
    assert [(row["currency"], row["amount"]) for row in unconvertible] == [
        (UNCONVERTIBLE_CURRENCY, "100.00")
    ]
    # One document, counted once by each list. An upper bound, never a miss.
    assert unconvertible[0]["documents"] == 2


def test_an_amount_edit_that_would_orphan_an_allocation_is_a_400_naming_it(
    api_client: TestClient, api_document_id: int
) -> None:
    """The other side of 0035's mirror trigger.

    The refusal is deliberate — an amount that no longer matches its lines
    makes every chart total for the document quietly wrong — but until the
    shared commit helper it reached the owner as an unexplained 500 from
    `PATCH /api/documents/{id}`, on a path with no allocation vocabulary at all.
    """
    api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}, {"amount": "40.00"}]},
    )
    response = api_client.patch(
        f"/api/documents/{api_document_id}", json={"amount_total": "200.00"}
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "spend lines" in detail
    # The owner is told what to do, and Postgres' own text is not echoed back.
    assert "clear or replace" in detail
    assert "P0001" not in detail and "spend_lines_sum" not in detail
    # And the edit did not take.
    assert api_client.get(f"/api/documents/{api_document_id}").json()["amount_total"] == "100.00"


def test_an_amount_edit_on_an_unallocated_document_is_untouched(
    api_client: TestClient, api_document_id: int
) -> None:
    """The guard must not refuse the ordinary edit it now wraps."""
    response = api_client.patch(
        f"/api/documents/{api_document_id}", json={"amount_total": "200.00"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["amount_total"] == "200.00"


async def test_a_database_error_on_the_document_edit_path_is_not_a_400(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The narrowing has to hold on the new path too.

    Provoked the same honest way as the allocation route's own case: a deferred
    UNIQUE fails at exactly the commit `update_document` performs, with SQLSTATE
    `23505` rather than plpgsql's `P0001`. It must propagate — nothing in
    `src/library/` registers an exception handler, so a `DBAPIError` out of a
    route is a 5xx, which is where a deadlock or a dropped connection belongs.
    Reporting it as "your spend lines" would be a wrong diagnosis under a status
    code that says the owner caused it.
    """
    row = await document(amount_total="100.00", amount_kind=AmountKind.PAYMENT_MADE)
    await session.execute(
        text(
            "CREATE TEMPORARY TABLE document_edit_probe (id int, "
            "CONSTRAINT document_edit_probe_unique UNIQUE (id) DEFERRABLE INITIALLY DEFERRED) "
            "ON COMMIT DROP"
        )
    )
    await session.execute(text("INSERT INTO document_edit_probe VALUES (1), (1)"))
    with pytest.raises(DBAPIError) as caught:
        await update_document(row.id, DocumentUpdate(amount_total=Decimal("200.00")), session)
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"
    await session.rollback()


async def test_the_deferred_sum_trigger_arrives_as_a_400_not_a_500(
    session: AsyncSession, document: DocumentFactory
) -> None:
    """The other half of the allocation contract: the Python pre-check is a
    backstop's error *message*, and the backstop itself fires at COMMIT.

    `replace_lines` flushes and never commits, and 0035's triggers are
    DEFERRABLE INITIALLY DEFERRED, so a violation arrives under asyncpg as a
    bare `DBAPIError` — a 500 unless the router translates it. Exercised
    against the real trigger rather than a stub.
    """
    row = await document(amount_total="100.00", amount_kind=AmountKind.PAYMENT_MADE)
    session.add(SpendLine(document_id=row.id, amount=Decimal("60.00")))
    await session.flush()
    with pytest.raises(HTTPException) as caught:
        await _commit_allocation(session)
    assert caught.value.status_code == 400
    assert "sum" in str(caught.value.detail).lower()


async def test_a_database_error_that_is_not_the_sum_trigger_is_not_a_400(
    session: AsyncSession,
) -> None:
    """A deadlock, a lock timeout, a dropped connection or a label FK violation
    must not reach the owner as "the lines do not sum".

    Provoked honestly: a deferred UNIQUE on a temporary table fails at COMMIT
    exactly where the sum trigger does, but with SQLSTATE `23505` instead of
    plpgsql's `P0001`. It must propagate — a wrong diagnosis under a 400 would
    hide a real defect that belongs in a 5xx.
    """
    await session.execute(
        text(
            "CREATE TEMPORARY TABLE allocation_probe (id int, "
            "CONSTRAINT allocation_probe_unique UNIQUE (id) DEFERRABLE INITIALLY DEFERRED) "
            "ON COMMIT DROP"
        )
    )
    await session.execute(text("INSERT INTO allocation_probe VALUES (1), (1)"))
    with pytest.raises(DBAPIError) as caught:
        await _commit_allocation(session)
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"
    await session.rollback()


# --- the footer drill route ---------------------------------------------------


def test_the_footer_route_lists_the_documents_behind_a_count(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    document_id = _seed_document(api_database_url, amount="89.20", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-footer-drill", SOFTWARE_RULE)
    data = api_client.get(f"/api/spending/{chart_id}/data").json()
    counted = data["footer"]["uncategorised"]["documents"]

    body = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised",
        params={"currency": data["currency"]},
    ).json()

    assert len(body["documents"]) == counted
    listed = {d["id"]: d for d in body["documents"]}
    assert document_id in listed
    assert listed[document_id]["amount"] == "89.20"


def test_an_unknown_footer_bucket_is_a_422_naming_it(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer-bucket-422", {})
    response = api_client.get(f"/api/spending/{chart_id}/footer/nonsense")
    assert response.status_code == 422
    assert "nonsense" in response.json()["detail"]


def test_excluded_without_amount_kind_is_a_422_not_an_empty_page(
    api_client: TestClient,
) -> None:
    """`row.amount_kind == amount_kind` can never match an `excluded` row when
    `amount_kind` is omitted — every `excluded` row carries a kind — so a
    missing `amount_kind` and an unrecognised one would both otherwise render
    as an empty page indistinguishable from "that group is genuinely empty"."""
    chart_id = _save_chart(api_client, "api-footer-excluded-422", {})
    response = api_client.get(f"/api/spending/{chart_id}/footer/excluded")
    assert response.status_code == 422
    assert "amount_kind" in response.json()["detail"]


def test_the_footer_route_caps_its_limit_at_100(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer-limit", {})
    response = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised", params={"limit": 101}
    )
    assert response.status_code == 422


def test_the_footer_route_reports_the_buckets_full_size_before_paging(
    api_client: TestClient, api_database_url: str
) -> None:
    """A bucket bigger than one page must say so.

    `uncategorised` on a real archive is exactly this shape (§9.4 calls it "a
    visible task" because it tends to be large), so a page of `limit` items
    beside a `total` that only counts the page would make a bucket of 340 look
    complete at 100 — indistinguishable from the footer's own count.
    """
    _seed_vocabulary(api_database_url)
    for _ in range(3):
        _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-footer-paging", SOFTWARE_RULE)
    body = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised", params={"limit": 2}
    ).json()
    assert body["total"] == 3
    assert len(body["documents"]) == 2
    assert body["total"] > len(body["documents"])


def test_the_footer_route_and_the_footer_count_agree_after_a_window_narrows(
    api_client: TestClient, api_database_url: str
) -> None:
    """The route takes /data's window arguments and must resolve them the same
    way, or the list answers a different question from the count above it."""
    _seed_vocabulary(api_database_url)
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE, day=MARCH)
    _seed_document(
        api_database_url,
        amount="20.00",
        kind=AmountKind.PAYMENT_MADE,
        day=date(2026, 6, 1),
    )
    chart_id = _save_chart(api_client, "api-footer-window", SOFTWARE_RULE)
    window = {"from": "2026-03-01", "to": "2026-03-31"}

    data = api_client.get(f"/api/spending/{chart_id}/data", params=window).json()
    body = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised",
        params={**window, "currency": data["currency"]},
    ).json()

    assert len(body["documents"]) == data["footer"]["uncategorised"]["documents"]


# --- facet counts -------------------------------------------------------------


def test_counts_are_ordered_by_document_count(
    api_client: TestClient, api_database_url: str
) -> None:
    """The empty state proposes questions worth asking, so the busiest values
    come first (§10.4)."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha", "beta"))
    for _ in range(2):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={facet: "alpha"},
        )
    _seed_document(
        api_database_url, amount="30.00", kind=AmountKind.PAYMENT_MADE, labels={facet: "beta"}
    )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    mine = [c for c in counts if c["facet_key"] == facet]
    assert [(c["value_key"], c["documents"]) for c in mine] == [("alpha", 2), ("beta", 1)]


def test_counts_carry_the_date_span(api_client: TestClient, api_database_url: str) -> None:
    """ "15 documents in `software` over 3 months" needs both ends."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha",))
    for day in (date(2026, 1, 5), date(2026, 3, 9)):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            day=day,
            labels={facet: "alpha"},
        )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    alpha = next(c for c in counts if c["facet_key"] == facet)
    assert alpha["first_date"] == "2026-01-05"
    assert alpha["last_date"] == "2026-03-09"


def test_a_value_with_no_money_behind_it_is_absent(
    api_client: TestClient, api_database_url: str
) -> None:
    """Reading `spend_facts` rather than `document_labels` does this for free:
    the view requires `amount_total IS NOT NULL` and its join to `payments`
    excludes soft-deleted documents. Proposing a chart of a value the archive
    has no amounts for is exactly the noise §10.4 replaces.

    A bare "the excluded values are absent" assertion is true whether the
    filtering works or nothing under this uuid4 facet key was ever seeded, so
    a third, money-bearing, non-deleted value under the SAME facet is seeded
    too and asserted present — that ties the negative claim to the positive
    one and makes the test discriminate real filtering from an empty-by-
    coincidence result (review finding on this task)."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("amountless", "deleted", "present"))
    _seed_document(api_database_url, amount=None, labels={facet: "amountless"})
    deleted_id = _seed_document(
        api_database_url,
        amount="99.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "deleted"},
    )
    _seed_document(
        api_database_url,
        amount="12.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "present"},
    )

    async def soft_delete(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": deleted_id}
        )

    _run(api_database_url, soft_delete)

    counts = api_client.get("/api/facets/counts").json()["counts"]

    mine = {c["value_key"] for c in counts if c["facet_key"] == facet}
    assert mine == {"present"}


def test_a_merged_pair_counts_once(api_client: TestClient, api_database_url: str) -> None:
    """`is_canonical` is the one filter reading `spend_facts` does not give for
    free: a merged twin is a second row for money already counted once."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha",))
    name = f"Corvus Test Assurance {uuid.uuid4()}"
    document_ids = [
        _seed_sender_document(
            api_database_url, sender=name, amount="10.00", labels={facet: "alpha"}
        )[0]
        for _ in range(2)
    ]

    async def read_payment_ids(session: AsyncSession) -> list[int]:
        rows = await session.execute(
            text("SELECT DISTINCT payment_id FROM payments WHERE document_id = ANY(:ids)"),
            {"ids": document_ids},
        )
        return [row[0] for row in rows]

    payment_ids = _run(api_database_url, read_payment_ids)
    assert payment_ids == [payment_ids[0]], (
        "same sender, amount, currency and day (R1) must merge the pair into one "
        "payment, or the count below proves nothing"
    )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    alpha = next(c for c in counts if c["facet_key"] == facet)
    assert alpha["documents"] == 1, "two documents, one payment, one canonical row"


def test_a_split_document_counts_once_in_facet_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    """`count(DISTINCT sf.document_id)` guards a *different* overcounting
    mechanism than `is_canonical` does: one canonical document split across
    spend lines emits one `spend_facts` row per line, and both lines here
    carry the same label (inherited from the document, per migration 0035's
    `doc_labels || line_labels`) — two identical `(facet_key, value_key)`
    pairs from one document. Deleting `DISTINCT` from `_FACET_COUNTS_SQL`
    leaves this document counted twice while every other test in this file
    stays green, which is exactly the regression this test exists to catch."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha",))
    document_id = _seed_document(
        api_database_url,
        amount="20.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "alpha"},
    )
    response = api_client.put(
        f"/api/documents/{document_id}/spend-lines",
        json={"lines": [{"amount": "10.00"}, {"amount": "10.00"}]},
    )
    assert response.status_code == 200, response.text

    counts = api_client.get("/api/facets/counts").json()["counts"]

    alpha = next(c for c in counts if c["facet_key"] == facet)
    assert alpha["documents"] == 1, "one document split into two lines, one canonical row"


# --- label counts ---------------------------------------------------------
#
# `/api/facets/label-counts` reads `document_labels` directly rather than
# `spend_facts`, so it counts every value a document carries, including ones
# `/api/facets/counts` was built to leave out. Each test below is a case
# where the two routes must *disagree* — a fixture where they agree proves
# nothing about which table label-counts actually reads.
#
# These tests live here rather than in `tests/test_api_facets.py` because the
# seeding helpers they need (`_seed_vocabulary`, `_seed_document`, `_run`,
# `AmountKind`) live in this file; `test_api_facets.py` defines its own,
# differently-behaved `_run` (callers commit inside the op, rather than `_run`
# committing after it returns), so importing this module's `_run` under the
# same name there would shadow one helper with another that has different
# transaction semantics — a second copy of `_run`, not a reuse of one.


def test_label_counts_include_a_value_with_no_money_behind_it(
    api_client: TestClient, api_database_url: str
) -> None:
    """The whole reason this route exists. `/api/facets/counts` reads
    `spend_facts`, whose `eligible` CTE requires `amount_total IS NOT NULL`, so
    a value carried only by amountless documents has no row there at all — it
    renders as unused and then 409s on delete."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("amountless", "monied"))
    _seed_document(api_database_url, amount=None, labels={facet: "amountless"})
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "monied"},
    )

    labelled = api_client.get("/api/facets/label-counts").json()["counts"]
    money = api_client.get("/api/facets/counts").json()["counts"]

    mine = {c["value_key"]: c["labelled"] for c in labelled if c["facet_key"] == facet}
    assert mine == {"amountless": 1, "monied": 1}
    assert {c["value_key"] for c in money if c["facet_key"] == facet} == {"monied"}, (
        "the money route must be unchanged — if this fails, plan 4b's empty "
        "state has been altered underneath it"
    )


def test_label_counts_count_a_soft_deleted_document(
    api_client: TestClient, api_database_url: str
) -> None:
    """`document_labels` rows survive a soft delete and still block a delete,
    so the number shown must include them or it is not the number enforced."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("gone",))
    doc_id = _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "gone"},
    )

    async def soft_delete(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": doc_id}
        )

    _run(api_database_url, soft_delete)

    counts = api_client.get("/api/facets/label-counts").json()["counts"]

    gone = next(c for c in counts if c["facet_key"] == facet)
    assert gone["labelled"] == 1
    money = api_client.get("/api/facets/counts").json()["counts"]
    assert not [c for c in money if c["facet_key"] == facet], "excluded from the money route"


def test_a_value_no_document_carries_is_absent_from_label_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    """An unused value has no row, which is what makes it deletable. Paired with
    a carried value under the same facet so the assertion cannot pass by the
    facet being empty."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("unused", "carried"))
    _seed_document(api_database_url, amount=None, labels={facet: "carried"})

    counts = api_client.get("/api/facets/label-counts").json()["counts"]

    assert {c["value_key"] for c in counts if c["facet_key"] == facet} == {"carried"}


def test_a_split_documents_line_only_label_is_absent_from_label_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    """The divergence between the two routes runs in BOTH directions. The
    three tests above are all one direction — a value `label-counts` carries
    that `counts` (money) does not. This is the mirror: a **split** document
    whose `line_labels` name a value that its `document_labels` do not.

    Migration 0035's `spend_facts` view inherits a line's label onto the
    document via `doc_labels || line_labels` (jsonb `||` takes the RIGHT
    operand on a key collision), so `/api/facets/counts` — which reads
    `spend_facts` — sees the value. But `label_counts` (`vocabulary.py`)
    reads `document_labels` directly, and no row is ever written there for a
    label that arrives only through `PUT .../spend-lines`'s per-line
    `labels` (`replace_lines` in `src/library/spend_lines.py` writes
    `line_labels`, never `document_labels`) — so the value must be absent
    from `label-counts` even though the money route reports it."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("line-only",))
    document_id = _seed_document(
        api_database_url,
        amount="20.00",
        kind=AmountKind.PAYMENT_MADE,
        # No document-level label: the value is named only on one line below.
    )
    response = api_client.put(
        f"/api/documents/{document_id}/spend-lines",
        json={
            "lines": [
                {"amount": "10.00", "labels": {facet: "line-only"}},
                {"amount": "10.00"},
            ]
        },
    )
    assert response.status_code == 200, response.text

    money = api_client.get("/api/facets/counts").json()["counts"]
    labelled = api_client.get("/api/facets/label-counts").json()["counts"]

    assert {c["value_key"] for c in money if c["facet_key"] == facet} == {"line-only"}, (
        "the money route inherits the line label via spend_facts's doc_labels || line_labels"
    )
    assert not [c for c in labelled if c["facet_key"] == facet], (
        "label-counts reads document_labels directly, and no row was ever written there"
    )


def test_the_displayed_count_is_the_count_delete_enforces(
    api_client: TestClient, api_database_url: str
) -> None:
    """The route's entire claim, tied to the operation in one test: the number
    the panel shows and the number the 409 names must be the same number."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("busy",))
    for _ in range(3):
        _seed_document(api_database_url, amount=None, labels={facet: "busy"})

    counts = api_client.get("/api/facets/label-counts").json()["counts"]
    shown = next(c for c in counts if c["facet_key"] == facet)["labelled"]

    response = api_client.delete(f"/api/facets/{facet}/values/busy")

    assert response.status_code == 409
    assert f"is on {shown} documents" in response.json()["detail"]
    assert shown == 3
