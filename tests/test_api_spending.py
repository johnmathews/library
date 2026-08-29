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
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.api.spending import _commit_allocation
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.models import (
    AmountKind,
    Document,
    DocumentSource,
    DocumentStatus,
    FacetValue,
    FxRate,
    SpendLine,
)
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
        session.add(document)
        await session.flush()
        for facet_key, value_key in (labels or {}).items():
            await set_document_label(session, document.id, facet_key, value_key)
        return document.id

    return _run(database_url, work)


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


def test_a_period_off_the_grain_boundary_is_a_422_naming_the_right_one(
    api_client: TestClient,
) -> None:
    """`chart_cell` filters `date_trunc(grain, date) = period`, so a mid-month
    period matches nothing — and an empty panel under a non-empty bar reads as
    "you spent nothing here"."""
    chart_id = _save_chart(api_client, "api-period", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/cell?period=2026-03-15&grain=month")
    assert response.status_code == 422
    assert "2026-03-01" in response.text


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
