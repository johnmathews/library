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
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.api.documents import update_document
from library.api.spending import _commit_allocation
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.models import (
    AmountKind,
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
    database_url: str, *, sender: str, amount: str, day: date = MARCH
) -> tuple[int, int]:
    """A document from a named sender; returns `(document_id, sender_id)`.

    Extends `_seed_document`'s job rather than replacing it: `sender=` there
    resolves-or-creates the `Sender` row, so there is one definition of "seed a
    document for the spending API".
    """
    document_id = _seed_document(
        database_url, amount=amount, kind=AmountKind.PAYMENT_MADE, day=day, sender=sender
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


def test_a_non_numeric_split_value_resolves_to_itself_rather_than_500ing(
    api_client: TestClient, api_database_url: str
) -> None:
    """`split_value` is an unvalidated `/cell` query parameter. `int()` on it
    used to be an unhandled 500 for anything that was not a plain integer —
    the one surface a client actually controls."""
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-splits-junk-id", {}, default_split="sender")

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "sender", "split_value": "not-an-id"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["label"] == "not-an-id"


def test_an_out_of_range_split_value_resolves_to_itself_rather_than_500ing(
    api_client: TestClient, api_database_url: str
) -> None:
    """`senders.id` is Postgres `int4`; a numeric id too large for it used to
    reach asyncpg's own range check and 500 rather than resolving to itself."""
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-splits-oor-id", {}, default_split="sender")

    response = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={"period": "2026-03-01", "split": "sender", "split_value": "99999999999"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["label"] == "99999999999"


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
