"""MCP ``money_query`` (W10): totals over ``structured_query.query_documents``.

Kept out of tests/test_mcp.py, which covers the keyword read tools, auth and
connection handling; these tests share its helpers.

The fixture is adversarial on purpose (a favourable one proves nothing): a
refund (the one negative sign), one payment documented as an invoice AND a
receipt (same sender, amount, reference and date, so which of the two is
canonical is a tie the view settles), an insurance cover limit (a real amount
that is not spending), a soft-deleted payment, a payment just outside a
February date window, a leap-day document on the window's inclusive edge, and
a payment another household member uploaded (the archive is shared, so it
counts). Every wrong way to add these up gives a different number.

Scoping: the test database is shared across suites, so every call carries a
unique tag.
"""

import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from procrastinate import PsycopgConnector

from library.jobs import job_app, procrastinate_conninfo
from library.models import AmountKind, ReviewStatus
from tests.conftest import AuthUser, _insert_user
from tests.test_documents_api import _seed_document
from tests.test_mcp import MCP_URL, _asgi_client, _insert_token, _label, error_text, payload

pytestmark = pytest.mark.integration

type Connector = Callable[[str], AbstractAsyncContextManager[ClientSession]]

#: The February window every call uses: 2024 is a leap year, so the 29th is
#: in it and the inclusive upper bound is that day, not the 28th.
FEBRUARY: dict[str, str] = {"date_from": "2024-02-01", "date_to": "2024-02-29"}


@pytest.fixture
def connect(api_app: FastAPI, api_database_url: str) -> Connector:
    """Factory: the app's lifespan plus an MCP session authenticated with ``token``."""

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
async def token(api_database_url: str, auth_user: AuthUser) -> str:
    raw, _ = await _insert_token(api_database_url, auth_user.id, "mcp-money")
    return raw


@dataclass(frozen=True)
class MoneyFixture:
    tag: str
    sender: str
    other_sender: str
    insurer: str
    invoice: int
    receipt: int
    refund: int
    cover_limit: int
    other_users_payment: int
    other_user_id: int


async def _seed_money(database_url: str, auth_user: AuthUser) -> MoneyFixture:
    """The adversarial fixture described in the module docstring."""
    tag = f"mcp-money-{secrets.token_hex(4)}"
    sender = f"Example Energy {tag}"
    other_sender = f"Example Shop {tag}"
    insurer = f"Example Insurance {tag}"
    other_user_id = await _insert_user(database_url, f"{tag}-partner", "pw-partner-1234")

    async def seed(marker: str, **fields: Any) -> int:
        fields.setdefault("currency", "EUR")
        fields.setdefault("uploader_id", auth_user.id)
        return await _seed_document(database_url, f"{tag}-{marker}", tag_slugs=[tag], **fields)

    # One payment, two documents: same sender, amount, reference and date.
    invoice = await seed(
        "invoice",
        kind_slug="invoice",
        sender_name=sender,
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference="EX-0001",
        document_date=date(2024, 2, 1),
    )
    receipt = await seed(
        "receipt",
        kind_slug="receipt",
        sender_name=sender,
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference="EX-0001",
        document_date=date(2024, 2, 1),
    )
    # A partial refund from the same sender quoting the same reference, on the
    # leap day: the window's inclusive upper edge.
    refund = await seed(
        "refund",
        kind_slug="receipt",
        sender_name=sender,
        amount_total=Decimal("15.25"),
        amount_kind=AmountKind.REFUND,
        reference="EX-0001",
        document_date=date(2024, 2, 29),
    )
    cover_limit = await seed(
        "cover",
        kind_slug="certificate",
        sender_name=insurer,
        amount_total=Decimal("5000.00"),
        amount_kind=AmountKind.COVERAGE_LIMIT,
        document_date=date(2024, 2, 10),
    )
    # Uploaded by the other household member, and flagged by validation: it
    # still counts, and the flag is reported.
    other_users_payment = await seed(
        "partner",
        kind_slug="receipt",
        sender_name=other_sender,
        amount_total=Decimal("7.10"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2024, 2, 10),
        uploader_id=other_user_id,
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    # Outside the result entirely: in the trash, and one day past February.
    await seed(
        "deleted",
        kind_slug="receipt",
        sender_name=other_sender,
        amount_total=Decimal("40.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2024, 2, 15),
        deleted_at=datetime.now(UTC),
    )
    await seed(
        "march",
        kind_slug="receipt",
        sender_name=other_sender,
        amount_total=Decimal("1000.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2024, 3, 1),
    )
    return MoneyFixture(
        tag=tag,
        sender=sender,
        other_sender=other_sender,
        insurer=insurer,
        invoice=invoice,
        receipt=receipt,
        refund=refund,
        cover_limit=cover_limit,
        other_users_payment=other_users_payment,
        other_user_id=other_user_id,
    )


async def test_money_query_nets_refund_and_dedups_payment(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """The total the model reads is the household's real net spend."""
    money = await _seed_money(api_database_url, auth_user)
    other_token, _ = await _insert_token(api_database_url, money.other_user_id, "mcp-money-2")
    arguments = {"aggregate": "sum_amount", "tags": [money.tag], **FEBRUARY}
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", arguments))
    async with connect(other_token) as session:
        as_partner = payload(await session.call_tool("money_query", arguments))

    # Hand-computed, EUR, February 2024:
    #   invoice + receipt, one payment   +100.00  (counted once)
    #   partial refund                    -15.25  (money back)
    #   the partner's payment              +7.10  (shared archive)
    #   cover limit 5000.00               excluded (not spending)
    #   deleted 40.00, March 1000.00      not matched
    #   net                               = 91.85
    # The wrong answers are all different: summing amount_total 5222.35,
    # refund added 122.35, payment twice 191.85, partner's dropped 84.75.
    assert result["result_type"] == "sum_amount"
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert (row["total"], row["currency"], row["key"]) == ("91.85", "EUR", None)
    assert row["document_count"] == 3
    assert len(set(row["document_ids"]) & {money.invoice, money.receipt}) == 1
    assert {money.refund, money.other_users_payment} <= set(row["document_ids"])
    assert money.cover_limit not in row["document_ids"]
    # The archive is shared: the other member gets the same answer.
    assert as_partner == result


async def test_money_query_reports_excluded_reasons(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Coverage names what the total leaves out and why, unchanged from Ask's
    service: the second document of the payment and the cover limit."""
    money = await _seed_money(api_database_url, auth_user)
    arguments: dict[str, Any] = {"aggregate": "sum_amount", "tags": [money.tag], **FEBRUARY}
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", arguments))
        unreviewed = payload(
            await session.call_tool("money_query", {**arguments, "review_status": "unreviewed"})
        )

    assert result["coverage"] == {
        "matched": 5,
        "included": 3,
        "excluded": {"duplicate_payment": 1, "not_summable_kind": 1},
        "needs_review": 1,
    }
    # Filtering on trust is reported as its own exclusion, never as a smaller
    # `matched`: the flagged payment is dropped and the total says so.
    assert unreviewed["coverage"] == {
        "matched": 5,
        "included": 2,
        "excluded": {
            "filtered_review_status": 1,
            "duplicate_payment": 1,
            "not_summable_kind": 1,
        },
        "needs_review": 0,
    }
    assert [row["total"] for row in unreviewed["rows"]] == ["84.75"]


async def test_money_query_groups_by_sender(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """group_by splits the same net total per sender, with the refund netted
    inside its own sender's group, and a document with no sender reported."""
    money = await _seed_money(api_database_url, auth_user)
    await _seed_document(
        api_database_url,
        f"{money.tag}-no-sender",
        tag_slugs=[money.tag],
        kind_slug="receipt",
        amount_total=Decimal("3.00"),
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2024, 2, 20),
    )
    async with connect(token) as session:
        result = payload(
            await session.call_tool(
                "money_query",
                {"aggregate": "sum_amount", "group_by": "sender", "tags": [money.tag], **FEBRUARY},
            )
        )
    # 100.00 - 15.25 = 84.75 for the energy sender; 7.10 for the shop. The
    # insurer's cover limit makes no group, and the sender-less 3.00 is out.
    totals = {row["key"]: (row["total"], row["currency"]) for row in result["rows"]}
    assert totals == {money.sender: ("84.75", "EUR"), money.other_sender: ("7.10", "EUR")}
    assert result["coverage"]["excluded"] == {
        "duplicate_payment": 1,
        "not_summable_kind": 1,
        "no_sender": 1,
    }
    assert result["coverage"]["matched"] == 6


async def test_money_query_lists_and_counts_senders(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """The other two aggregates come with the same coverage block."""
    money = await _seed_money(api_database_url, auth_user)
    async with connect(token) as session:
        senders = payload(
            await session.call_tool(
                "money_query", {"aggregate": "distinct_senders", "tags": [money.tag], **FEBRUARY}
            )
        )
        listed = payload(
            await session.call_tool(
                "money_query", {"aggregate": "list", "tags": [money.tag], "kind": "invoice"}
            )
        )
    counts = {row["sender"]: row["document_count"] for row in senders["rows"]}
    assert counts == {money.sender: 3, money.other_sender: 1, money.insurer: 1}
    assert senders["coverage"] == {"matched": 5, "included": 5, "excluded": {}, "needs_review": 1}
    assert [row["id"] for row in listed["rows"]] == [money.invoice]
    assert listed["rows"][0]["document_date"] == "2024-02-01"
    assert listed["rows"][0]["amount_total"] == "100.00"
    assert listed["coverage"]["matched"] == 1


async def test_money_query_filters_narrow_the_total(
    connect: Connector, token: str, api_database_url: str
) -> None:
    """Each filter reaches the query: six payments in powers of two, one per
    filter, so every subset has its own total and a dropped filter shows as
    the whole 63.00."""
    tag = f"mcp-money-filters-{secrets.token_hex(4)}"
    facet = f"{tag}-f"

    async def seed(marker: str, amount: str, day: int, **fields: Any) -> int:
        return await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag, *fields.pop("extra_tags", [])],
            amount_total=Decimal(amount),
            currency="EUR",
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2025, 2, day),
            **fields,
        )

    await seed("sender", "1.00", 1, sender_name=f"Example Sender {tag}")
    await seed("recipient", "2.00", 2, recipient_name=f"Example Person {tag}")
    await seed("project", "4.00", 3, project_slugs=[f"{tag}-p"])
    await seed("matter", "8.00", 4, matter_slugs=[f"{tag}-m"])
    labelled = await seed("facet", "16.00", 5, extra_tags=[f"{tag}-x"])
    await _label(api_database_url, labelled, facet, "v")
    await seed("plain", "32.00", 28)

    cases: list[tuple[dict[str, Any], str]] = [
        ({}, "63.00"),
        ({"sender": f"sender {tag}"}, "1.00"),
        ({"recipient": f"person {tag}"}, "2.00"),
        ({"projects": [f"{tag}-p"]}, "4.00"),
        ({"matters": [f"{tag}-m"]}, "8.00"),
        ({"facets": {facet: "v"}}, "16.00"),
        ({"tags": [tag, f"{tag}-x"]}, "16.00"),
        ({"date_from": "2025-02-28"}, "32.00"),
        ({"date_to": "2025-02-01"}, "1.00"),
    ]
    totals: list[str] = []
    async with connect(token) as session:
        for arguments, _ in cases:
            result = payload(await session.call_tool("money_query", {"tags": [tag], **arguments}))
            totals.extend(row["total"] for row in result["rows"])
    assert totals == [expected for _, expected in cases]


async def test_money_query_errors_the_model_can_fix(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Arguments that would silently give a wrong or empty total are refused
    with text that says what to change."""
    money = await _seed_money(api_database_url, auth_user)
    async with connect(token) as session:
        grouped_list = await session.call_tool(
            "money_query", {"aggregate": "list", "group_by": "sender", "tags": [money.tag]}
        )
        concept = await session.call_tool(
            "money_query", {"aggregate": "sum_amount", "kind": "energy", "tags": [money.tag]}
        )
        unknown_kind = await session.call_tool(
            "money_query", {"aggregate": "sum_amount", "kind": "nonsense", "tags": [money.tag]}
        )
        backwards = await session.call_tool(
            "money_query",
            {"tags": [money.tag], "date_from": "2024-02-29", "date_to": "2024-02-01"},
        )
        bad_aggregate = await session.call_tool("money_query", {"aggregate": "average"})
        bad_group = await session.call_tool(
            "money_query", {"aggregate": "sum_amount", "group_by": "month"}
        )

    assert error_text(grouped_list) == (
        "group_by applies only to aggregate='sum_amount'; omit it, or use sum_amount."
    )
    assert error_text(concept) == (
        "unknown kind 'energy'; did you mean kind='utility-bill'? list_kinds lists the kind slugs."
    )
    assert error_text(unknown_kind) == "unknown kind 'nonsense'. list_kinds lists the kind slugs."
    assert error_text(backwards) == "date_from 2024-02-29 is after date_to 2024-02-01."
    # Enum arguments are checked against the published schema before the tool
    # runs; the error names the valid values instead of being masked.
    for result, valid in ((bad_aggregate, "sum_amount"), (bad_group, "sender")):
        message = error_text(result)
        assert valid in message
        assert "Error calling tool" not in message
    for result in (grouped_list, concept, unknown_kind, backwards, bad_aggregate, bad_group):
        assert "/api/" not in error_text(result) and "REST" not in error_text(result)
