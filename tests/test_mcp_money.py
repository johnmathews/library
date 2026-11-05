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
from sqlalchemy import text

from library import structured_query
from library.ask.engine import _run_query_documents
from library.jobs import job_app, procrastinate_conninfo
from library.models import AmountKind, ReviewStatus
from library.money.payments import add_override
from library.spend_lines import LineInput, replace_lines
from tests.conftest import AuthUser, _insert_user
from tests.test_documents_api import _seed_document
from tests.test_mcp import MCP_URL, _asgi_client, _db, _insert_token, _label, error_text, payload

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


# --------------------------------------------- dedup within the filtered set
#
# W10 review, fix 1: the counted document of a payment is chosen among the
# documents the caller's filters let through, not archive-wide. Before, a
# filter that matched only a payment's non-canonical twin dropped the payment
# and reported it as `duplicate_payment`, "counted once", with a total of 0.


@dataclass(frozen=True)
class SplitPayments:
    tag: str
    water: str
    power: str
    builder: str
    bill: int
    receipt: int
    refund: int
    partner_bill: int
    invoice: int
    paid: int


async def _seed_split_payments(database_url: str, auth_user: AuthUser) -> SplitPayments:
    """Two merged payments whose archive-wide canonical twin is the one a
    narrow filter leaves out.

    P1, a water bill (utility-bill, payment_due) and its receipt (receipt,
    payment_made), one reference: the view counts the RECEIPT (payment_made
    wins). Only the bill carries the extra tag ``<tag>-bill``. A soft-deleted
    payment_made copy of the bill, same reference, sits in the trash: if it
    were ranked, it would take the bill's place and the bill would be dropped.

    P2, an itemised invoice (lines 50.00 + 25.50) and its receipt: the view
    counts the INVOICE (a line-bearing document wins). Only the receipt
    carries ``<tag>-paid``.

    Around them: a refund credit note from the water company (its own
    payment: the sign guard never merges it) on the leap day, and a power
    bill another household member uploaded, flagged needs_review. The P1
    receipt is verified, so a review_status filter can remove it.
    """
    tag = f"mcp-split-{secrets.token_hex(4)}"
    water = f"Example Water {tag}"
    power = f"Example Power {tag}"
    builder = f"Example Builder {tag}"
    other_user_id = await _insert_user(database_url, f"{tag}-partner", "pw-partner-1234")

    async def seed(marker: str, *, extra_tags: tuple[str, ...] = (), **fields: Any) -> int:
        fields.setdefault("currency", "EUR")
        fields.setdefault("uploader_id", auth_user.id)
        return await _seed_document(
            database_url, f"{tag}-{marker}", tag_slugs=[tag, *extra_tags], **fields
        )

    bill = await seed(
        "bill",
        extra_tags=(f"{tag}-bill",),
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("84.20"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=f"W-{tag}",
        document_date=date(2024, 2, 5),
    )
    receipt = await seed(
        "receipt",
        kind_slug="receipt",
        sender_name=water,
        amount_total=Decimal("84.20"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"W-{tag}",
        document_date=date(2024, 2, 12),
        review_status=ReviewStatus.VERIFIED,
    )
    await seed(
        "bill-deleted",
        extra_tags=(f"{tag}-bill",),
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("84.20"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"W-{tag}",
        document_date=date(2024, 2, 5),
        deleted_at=datetime.now(UTC),
    )
    refund = await seed(
        "credit",
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("12.40"),
        amount_kind=AmountKind.REFUND,
        reference=f"W-{tag}-CR",
        document_date=date(2024, 2, 29),
    )
    partner_bill = await seed(
        "partner",
        kind_slug="utility-bill",
        sender_name=power,
        amount_total=Decimal("31.15"),
        amount_kind=AmountKind.PAYMENT_DUE,
        document_date=date(2024, 2, 20),
        uploader_id=other_user_id,
        review_status=ReviewStatus.NEEDS_REVIEW,
    )
    invoice = await seed(
        "invoice",
        kind_slug="invoice",
        sender_name=builder,
        amount_total=Decimal("75.50"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=f"B-{tag}",
        document_date=date(2024, 2, 8),
    )
    paid = await seed(
        "paid",
        extra_tags=(f"{tag}-paid",),
        kind_slug="receipt",
        sender_name=builder,
        amount_total=Decimal("75.50"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"B-{tag}",
        document_date=date(2024, 2, 9),
    )
    async with _db(database_url) as session:
        await replace_lines(
            session,
            invoice,
            [LineInput(amount=Decimal("50.00")), LineInput(amount=Decimal("25.50"))],
        )
        await session.commit()
    # Stated, not assumed: archive-wide, each payment's counted document is
    # the one the narrow filters below leave out.
    assert await _view_canonical(database_url, [bill, receipt, invoice, paid]) == {
        receipt,
        invoice,
    }
    return SplitPayments(
        tag=tag,
        water=water,
        power=power,
        builder=builder,
        bill=bill,
        receipt=receipt,
        refund=refund,
        partner_bill=partner_bill,
        invoice=invoice,
        paid=paid,
    )


async def _view_canonical(database_url: str, document_ids: list[int]) -> set[int]:
    """Which of ``document_ids`` the ``spend_facts`` view marks canonical,
    archive-wide: the choice the within-filter ranking must reproduce when
    every twin passes."""
    async with _db(database_url) as session:
        rows = await session.execute(
            text(
                "SELECT DISTINCT document_id FROM spend_facts "
                "WHERE is_canonical AND document_id = ANY(:ids)"
            ),
            {"ids": document_ids},
        )
        return set(rows.scalars())


def _partitions(coverage: dict[str, Any]) -> bool:
    """``included + sum(excluded) == matched``: the reasons partition the match."""
    return bool(coverage["included"] + sum(coverage["excluded"].values()) == coverage["matched"])


async def test_money_query_counts_a_payment_whose_canonical_twin_is_filtered_out(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """The reviewer's reproduction: kind='utility-bill' matches the bill of a
    bill+receipt payment whose receipt the view counts. The bill must carry
    the payment, and nothing may be reported as a duplicate."""
    money = await _seed_split_payments(api_database_url, auth_user)
    arguments: dict[str, Any] = {"kind": "utility-bill", "tags": [money.tag], **FEBRUARY}
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", arguments))
        grouped = payload(
            await session.call_tool("money_query", {**arguments, "group_by": "sender"})
        )

    # Hand-computed, EUR, utility bills in February 2024:
    #   water bill (its receipt is not a utility bill)  +84.20
    #   water credit note                                -12.40
    #   the partner's power bill                         +31.15
    #   the deleted copy of the bill                     not matched
    #   net                                              = 102.95
    # Before the fix: 18.75 (31.15 - 12.40), the bill "a duplicate".
    assert [(row["total"], row["currency"]) for row in result["rows"]] == [("102.95", "EUR")]
    assert sorted(result["rows"][0]["document_ids"]) == sorted(
        [money.bill, money.refund, money.partner_bill]
    )
    assert result["coverage"] == {
        "matched": 3,
        "included": 3,
        "excluded": {},
        "needs_review": 1,
    }
    # Per sender: water 84.20 - 12.40 = 71.80, power 31.15.
    assert {row["key"]: row["total"] for row in grouped["rows"]} == {
        money.water: "71.80",
        money.power: "31.15",
    }
    assert grouped["coverage"]["excluded"] == {}


async def test_money_query_counts_a_payment_by_a_tag_only_one_twin_carries(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """A tag on one twin only, both ways round: the water bill (its receipt
    is canonical archive-wide) and the builder's receipt (its invoice is,
    because the invoice has lines)."""
    money = await _seed_split_payments(api_database_url, auth_user)
    async with connect(token) as session:
        bill_only = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag, f"{money.tag}-bill"], **FEBRUARY}
            )
        )
        paid_only = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag, f"{money.tag}-paid"], **FEBRUARY}
            )
        )
    # The bill alone: 84.20 (the deleted copy also carries the tag: not
    # matched). Before the fix: no rows, the bill a "duplicate".
    assert [row["total"] for row in bill_only["rows"]] == ["84.20"]
    assert bill_only["rows"][0]["document_ids"] == [money.bill]
    assert bill_only["coverage"] == {"matched": 1, "included": 1, "excluded": {}, "needs_review": 0}
    # The receipt alone: 75.50 from its own row, not the invoice's lines.
    assert [row["total"] for row in paid_only["rows"]] == ["75.50"]
    assert paid_only["rows"][0]["document_ids"] == [money.paid]
    assert paid_only["coverage"]["excluded"] == {}


async def test_money_query_counts_an_itemised_payment_from_either_side(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """kind='receipt' keeps both receipts; the builder's is not canonical
    archive-wide (its invoice has lines). kind='invoice' sums the lines."""
    money = await _seed_split_payments(api_database_url, auth_user)
    async with connect(token) as session:
        receipts = payload(
            await session.call_tool(
                "money_query", {"kind": "receipt", "tags": [money.tag], **FEBRUARY}
            )
        )
        invoices = payload(
            await session.call_tool(
                "money_query", {"kind": "invoice", "tags": [money.tag], **FEBRUARY}
            )
        )
    # Receipts: water 84.20 + builder 75.50 = 159.70. Before the fix: 84.20.
    assert [row["total"] for row in receipts["rows"]] == ["159.70"]
    assert sorted(receipts["rows"][0]["document_ids"]) == sorted([money.receipt, money.paid])
    assert receipts["coverage"] == {"matched": 2, "included": 2, "excluded": {}, "needs_review": 0}
    # Invoices: the itemised invoice from its two lines, 50.00 + 25.50, one
    # document (two view rows, cited once).
    assert [(row["total"], row["document_count"]) for row in invoices["rows"]] == [("75.50", 1)]
    assert invoices["rows"][0]["document_ids"] == [money.invoice]


async def test_money_query_counts_twins_both_in_the_filter_once(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Both twins of each payment pass: counted once, via the document the
    view picks, and the other twin is the duplicate."""
    money = await _seed_split_payments(api_database_url, auth_user)
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", {"tags": [money.tag], **FEBRUARY}))
        grouped = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag], "group_by": "kind", **FEBRUARY}
            )
        )

    # Hand-computed, EUR, February 2024:
    #   water payment, counted once (receipt)    +84.20
    #   water credit note                         -12.40
    #   partner's power bill                      +31.15
    #   builder payment, counted once (invoice)   +75.50
    #   net                                       = 178.45
    # Counting each twin: 338.15.
    assert [row["total"] for row in result["rows"]] == ["178.45"]
    ids = set(result["rows"][0]["document_ids"])
    assert ids == {money.receipt, money.refund, money.partner_bill, money.invoice}
    assert result["coverage"] == {
        "matched": 6,
        "included": 4,
        "excluded": {"duplicate_payment": 2},
        "needs_review": 1,
    }
    # By kind, the counted document's kind holds the money: utility-bill
    # 31.15 - 12.40 = 18.75, receipt 84.20, invoice 75.50.
    assert {row["key"]: row["total"] for row in grouped["rows"]} == {
        "utility-bill": "18.75",
        "receipt": "84.20",
        "invoice": "75.50",
    }
    assert grouped["coverage"]["excluded"] == {"duplicate_payment": 2}


async def test_money_query_breaks_a_tie_between_twins_as_the_view_does(
    connect: Connector, token: str, api_database_url: str
) -> None:
    """Two receipts of one payment (same sender, amount and day), neither
    itemised: the ordering's last key, the lower id, decides, as in the view."""
    tag = f"mcp-split-tie-{secrets.token_hex(4)}"
    ids = [
        await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            kind_slug="receipt",
            sender_name=f"Example Cafe {tag}",
            amount_total=Decimal("4.80"),
            currency="EUR",
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2024, 2, 3),
        )
        for marker in ("first", "second")
    ]
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", {"tags": [tag], **FEBRUARY}))
    assert await _view_canonical(api_database_url, ids) == {min(ids)}
    # One payment of 4.80, cited by the view's choice.
    assert [(row["total"], row["document_ids"]) for row in result["rows"]] == [("4.80", [min(ids)])]
    assert result["coverage"]["excluded"] == {"duplicate_payment": 1}


async def test_money_query_counts_a_payment_whose_view_choice_is_undecided(
    connect: Connector, token: str, api_database_url: str
) -> None:
    """Over the whole archive too: an undecided document with the lower id is
    the view's choice for its payment (NULL is not payment_made, so the id
    decides), and it is never summed. Its payment_due twin now counts."""
    tag = f"mcp-split-undecided-{secrets.token_hex(4)}"

    async def seed(marker: str, amount_kind: AmountKind | None) -> int:
        return await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            kind_slug="invoice",
            sender_name=f"Example Garage {tag}",
            amount_total=Decimal("230.00"),
            currency="EUR",
            amount_kind=amount_kind,
            reference=f"G-{tag}",
            document_date=date(2024, 2, 16),
        )

    undecided = await seed("undecided", None)
    due = await seed("due", AmountKind.PAYMENT_DUE)
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", {"tags": [tag], **FEBRUARY}))
    assert await _view_canonical(api_database_url, [undecided, due]) == {undecided}
    # The garage invoice, 230.00, once. Before the fix: no rows, the due
    # invoice a "duplicate" of a document no total ever sums.
    assert [(row["total"], row["document_ids"]) for row in result["rows"]] == [("230.00", [due])]
    assert result["coverage"]["excluded"] == {"not_summable_kind": 1}


async def test_money_query_review_status_filter_moves_the_count_to_the_other_twin(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """review_status is an earlier gate too: filtering out the verified
    receipt leaves the bill to carry the water payment."""
    money = await _seed_split_payments(api_database_url, auth_user)
    async with connect(token) as session:
        result = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag], "review_status": "unreviewed", **FEBRUARY}
            )
        )
    # Unreviewed only: the verified receipt and the flagged power bill go.
    #   water bill (now carries the payment)   +84.20
    #   water credit note                       -12.40
    #   builder invoice (its receipt the dup)   +75.50
    #   net                                     = 147.30
    # Before the fix: 63.10, the bill a "duplicate" of a filtered-out receipt.
    assert [row["total"] for row in result["rows"]] == ["147.30"]
    assert result["coverage"] == {
        "matched": 6,
        "included": 3,
        "excluded": {"filtered_review_status": 2, "duplicate_payment": 1},
        "needs_review": 0,
    }
    assert _partitions(result["coverage"])


async def test_money_query_group_by_counts_a_payment_via_the_twin_with_the_key(
    connect: Connector, token: str, api_database_url: str
) -> None:
    """With group_by, a document lacking the key is a duplicate when its
    payment is counted through a twin that has the key, and no_sender only
    when its payment is not in the rows at all."""
    tag = f"mcp-split-key-{secrets.token_hex(4)}"
    shop = f"Example Shop {tag}"

    async def seed(marker: str, **fields: Any) -> int:
        return await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            currency="EUR",
            document_date=date(2024, 2, 14),
            **fields,
        )

    # A person merged a sender-less receipt with the shop's invoice. The
    # receipt is payment_made, so the view counts it, and it has no sender.
    invoice = await seed(
        "invoice",
        kind_slug="invoice",
        sender_name=shop,
        amount_total=Decimal("19.99"),
        amount_kind=AmountKind.PAYMENT_DUE,
    )
    receipt = await seed(
        "receipt",
        kind_slug="receipt",
        amount_total=Decimal("19.99"),
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    # A sender-less payment of its own: genuinely missing from the rows.
    await seed(
        "loose", kind_slug="receipt", amount_total=Decimal("2.50"), amount_kind=AmountKind.REFUND
    )
    async with _db(api_database_url) as session:
        await add_override(session, "MERGE", invoice, receipt)
        await session.commit()

    async with connect(token) as session:
        result = payload(
            await session.call_tool(
                "money_query", {"tags": [tag], "group_by": "sender", **FEBRUARY}
            )
        )
    # The shop's payment, through its invoice: 19.99. The loose refund has no
    # sender and no twin: no_sender. Before the fix: no rows at all, the
    # invoice a "duplicate" and the receipt no_sender.
    assert [(row["key"], row["total"]) for row in result["rows"]] == [(shop, "19.99")]
    assert result["rows"][0]["document_ids"] == [invoice]
    assert result["coverage"] == {
        "matched": 3,
        "included": 1,
        "excluded": {"duplicate_payment": 1, "no_sender": 1},
        "needs_review": 0,
    }


async def _seed_many_senders(database_url: str, auth_user: AuthUser) -> tuple[str, list[str]]:
    """Five sender-and-currency groups for the row-cap tests, plus a deleted
    500.00 that would be the largest group if it were counted.

    Returns the tag and the sender names, alphabetical: Alpha (30.00 + 10.00
    EUR and 12.00 USD), Charlie (a 90.00 refund), Delta (5.00, uploaded by
    another member), Echo (1.00, flagged needs_review).
    """
    tag = f"mcp-cap-{secrets.token_hex(4)}"
    names = [f"Example {name} {tag}" for name in ("Alpha", "Charlie", "Delta", "Echo")]
    alpha, charlie, delta, echo = names
    other_user_id = await _insert_user(database_url, f"{tag}-partner", "pw-partner-1234")

    async def seed(marker: str, sender: str, amount: str, **fields: Any) -> None:
        fields.setdefault("currency", "EUR")
        fields.setdefault("amount_kind", AmountKind.PAYMENT_MADE)
        fields.setdefault("uploader_id", auth_user.id)
        await _seed_document(
            database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            kind_slug="receipt",
            sender_name=sender,
            amount_total=Decimal(amount),
            document_date=date(2024, 2, 2 + len(marker)),
            **fields,
        )

    await seed("a1", alpha, "30.00")
    await seed("a22", alpha, "10.00")
    await seed("a333", alpha, "12.00", currency="USD")
    await seed("c", charlie, "90.00", amount_kind=AmountKind.REFUND)
    await seed("d", delta, "5.00", uploader_id=other_user_id)
    await seed("e", echo, "1.00", review_status=ReviewStatus.NEEDS_REVIEW)
    await seed("gone", f"Example Foxtrot {tag}", "500.00", deleted_at=datetime.now(UTC))
    return tag, names


async def test_money_query_caps_group_rows_keeping_the_largest(
    connect: Connector,
    token: str,
    api_database_url: str,
    auth_user: AuthUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grouped sums and sender lists return at most MAX_GROUP_ROWS rows, the
    largest kept, and what the cut rows held is reported as over_limit."""
    tag, (alpha, charlie, delta, _echo) = await _seed_many_senders(api_database_url, auth_user)
    cap = structured_query.MAX_GROUP_ROWS
    monkeypatch.setattr(structured_query, "MAX_GROUP_ROWS", 3)
    arguments: dict[str, Any] = {"tags": [tag], **FEBRUARY}
    async with connect(token) as session:
        by_sender = payload(
            await session.call_tool("money_query", {**arguments, "group_by": "sender"})
        )
        senders = payload(
            await session.call_tool("money_query", {**arguments, "aggregate": "distinct_senders"})
        )
        monkeypatch.setattr(structured_query, "MAX_GROUP_ROWS", 1)
        by_currency = payload(await session.call_tool("money_query", arguments))
        description = {tool.name: tool.description for tool in (await session.list_tools()).tools}

    # Groups by |total|: Charlie EUR 90.00 (a refund, -90.00), Alpha EUR
    # 40.00, Alpha USD 12.00 | Delta EUR 5.00, Echo EUR 1.00 cut. Rows stay
    # ordered by total. The deleted 500.00 is not matched, so not kept.
    assert [(row["key"], row["currency"], row["total"]) for row in by_sender["rows"]] == [
        (alpha, "EUR", "40.00"),
        (alpha, "USD", "12.00"),
        (charlie, "EUR", "-90.00"),
    ]
    # Six matched; Delta's and Echo's documents are in no row. Echo's flag
    # is not reported: it is not among the included.
    assert by_sender["coverage"] == {
        "matched": 6,
        "included": 4,
        "excluded": {"over_limit": 2},
        "needs_review": 0,
    }
    # Senders by document count, then name: Alpha 3, Charlie 1, Delta 1 | Echo 1.
    assert [(row["sender"], row["document_count"]) for row in senders["rows"]] == [
        (alpha, 3),
        (charlie, 1),
        (delta, 1),
    ]
    assert senders["coverage"] == {
        "matched": 6,
        "included": 5,
        "excluded": {"over_limit": 1},
        "needs_review": 0,
    }
    # Ungrouped, one row: EUR 40.00 - 90.00 + 5.00 + 1.00 = -44.00 outweighs
    # USD 12.00, whose one document is cut.
    assert [(row["currency"], row["total"]) for row in by_currency["rows"]] == [("EUR", "-44.00")]
    assert by_currency["coverage"] == {
        "matched": 6,
        "included": 5,
        "excluded": {"over_limit": 1},
        "needs_review": 1,
    }
    # The real cap, and that it exists, is in what the model reads.
    assert cap == 50
    assert f"at most {cap} rows" in (description["money_query"] or "")


async def test_money_query_row_cap_keeps_a_group_with_no_currency(
    connect: Connector, token: str, api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document with no currency is a group of its own, and when that group
    is the one kept, its document is still counted as included."""
    tag = f"mcp-cap-null-{secrets.token_hex(4)}"
    for marker, amount, currency in (("eur", "3.00", "EUR"), ("none", "50.00", None)):
        await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            kind_slug="receipt",
            amount_total=Decimal(amount),
            currency=currency,
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2024, 2, 29),
        )
    monkeypatch.setattr(structured_query, "MAX_GROUP_ROWS", 1)
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", {"tags": [tag], **FEBRUARY}))
    # 50.00 with no currency outweighs EUR 3.00, which is cut.
    assert [(row["currency"], row["total"]) for row in result["rows"]] == [(None, "50.00")]
    assert result["coverage"] == {
        "matched": 2,
        "included": 1,
        "excluded": {"over_limit": 1},
        "needs_review": 0,
    }


async def test_ask_query_documents_caps_group_rows_too(
    api_database_url: str, auth_user: AuthUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask's query_documents feeds the same rows to its model: same cap."""
    tag, (alpha, charlie, delta, _echo) = await _seed_many_senders(api_database_url, auth_user)
    monkeypatch.setattr(structured_query, "MAX_GROUP_ROWS", 2)
    async with _db(api_database_url) as session:
        result = await _run_query_documents(
            session, {"aggregate": "distinct_senders", "tags": [tag], **FEBRUARY}, set()
        )
    # Alpha 3, Charlie 1 | Delta 1, Echo 1 cut.
    assert [row["sender"] for row in result["rows"]] == [alpha, charlie]
    assert result["coverage"]["excluded"] == {"over_limit": 2}
    assert delta not in {row["sender"] for row in result["rows"]}


async def test_ask_query_documents_counts_a_payment_via_its_twin_in_the_filter(
    api_database_url: str, auth_user: AuthUser
) -> None:
    """Ask's query_documents tool runs the same service, and gets the same
    filtered total as the money_query reproduction."""
    money = await _seed_split_payments(api_database_url, auth_user)
    cited: set[int] = set()
    async with _db(api_database_url) as session:
        result = await _run_query_documents(
            session,
            {"aggregate": "sum_amount", "kind": "utility-bill", "tags": [money.tag], **FEBRUARY},
            cited,
        )
    # 84.20 - 12.40 + 31.15, as in the money_query reproduction above.
    assert [row["total"] for row in result["rows"]] == ["102.95"]
    assert result["coverage"]["excluded"] == {}
    assert money.bill in cited


# ------------------------------------------- a date window does not choose
#
# Second review of fix 1: ranking only the documents inside the date window
# let a window pick the twin, so a payment whose bill is dated 28 September
# and whose receipt is dated 3 October counted in both months. The date
# window now applies to the chosen document instead of to the ranking.

SEPTEMBER: dict[str, str] = {"date_from": "2024-09-01", "date_to": "2024-09-30"}
OCTOBER: dict[str, str] = {"date_from": "2024-10-01", "date_to": "2024-10-31"}
AUTUMN: dict[str, str] = {"date_from": "2024-09-01", "date_to": "2024-10-31"}


@dataclass(frozen=True)
class MonthPayments:
    tag: str
    water: str
    bill: int
    receipt: int
    prepaid: int
    invoiced_after: int
    refund: int


async def _seed_month_payments(database_url: str, auth_user: AuthUser) -> MonthPayments:
    """Payments whose twins fall in different months.

    P1 (water, 100.00): a utility bill dated 28 Sep and its receipt dated
    3 Oct. The receipt is payment_made, so it carries the payment: October.
    A soft-deleted payment_made copy of the bill, created BEFORE the receipt
    (lower id) and dated 28 Sep, would carry it in September if it were
    ranked. P2 (shop, 40.00, uploaded by another household member): paid on
    30 Sep, invoiced on 2 Oct; the receipt carries it: September. P3 (garage,
    20.00): an undated receipt and its invoice dated 20 Oct; the undated
    receipt carries it, so no date window holds it. A water credit note of
    15.00 on 15 Oct, and a café receipt of 7.00 on 29 Feb 2024.
    """
    tag = f"mcp-month-{secrets.token_hex(4)}"
    water = f"Example Water {tag}"
    shop = f"Example Shop {tag}"
    garage = f"Example Garage {tag}"
    other_user_id = await _insert_user(database_url, f"{tag}-partner", "pw-partner-1234")

    async def seed(marker: str, **fields: Any) -> int:
        fields.setdefault("currency", "EUR")
        fields.setdefault("uploader_id", auth_user.id)
        return await _seed_document(database_url, f"{tag}-{marker}", tag_slugs=[tag], **fields)

    bill = await seed(
        "bill",
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=f"W-{tag}",
        document_date=date(2024, 9, 28),
    )
    await seed(
        "bill-deleted",
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"W-{tag}",
        document_date=date(2024, 9, 28),
        deleted_at=datetime.now(UTC),
    )
    receipt = await seed(
        "receipt",
        kind_slug="receipt",
        sender_name=water,
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"W-{tag}",
        document_date=date(2024, 10, 3),
    )
    prepaid = await seed(
        "prepaid",
        kind_slug="receipt",
        sender_name=shop,
        amount_total=Decimal("40.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"S-{tag}",
        document_date=date(2024, 9, 30),
        uploader_id=other_user_id,
    )
    invoiced_after = await seed(
        "invoiced-after",
        kind_slug="invoice",
        sender_name=shop,
        amount_total=Decimal("40.00"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=f"S-{tag}",
        document_date=date(2024, 10, 2),
        uploader_id=other_user_id,
    )
    await seed(
        "garage-receipt",
        kind_slug="receipt",
        sender_name=garage,
        amount_total=Decimal("20.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=f"G-{tag}",
    )
    await seed(
        "garage-invoice",
        kind_slug="invoice",
        sender_name=garage,
        amount_total=Decimal("20.00"),
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=f"G-{tag}",
        document_date=date(2024, 10, 20),
    )
    refund = await seed(
        "credit",
        kind_slug="utility-bill",
        sender_name=water,
        amount_total=Decimal("15.00"),
        amount_kind=AmountKind.REFUND,
        reference=f"W-{tag}-CR",
        document_date=date(2024, 10, 15),
    )
    await seed(
        "cafe",
        kind_slug="receipt",
        sender_name=f"Example Cafe {tag}",
        amount_total=Decimal("7.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2024, 2, 29),
    )
    assert await _view_canonical(database_url, [bill, receipt, prepaid, invoiced_after]) == {
        receipt,
        prepaid,
    }
    return MonthPayments(
        tag=tag,
        water=water,
        bill=bill,
        receipt=receipt,
        prepaid=prepaid,
        invoiced_after=invoiced_after,
        refund=refund,
    )


async def test_money_query_monthly_totals_add_up_to_the_period(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """September + October == September-October: each payment counts in
    the window holding the document that carries it, whichever month the
    window's other twin is in."""
    money = await _seed_month_payments(api_database_url, auth_user)
    totals: dict[str, dict[str, Any]] = {}
    async with connect(token) as session:
        for name, window in (
            ("sep", SEPTEMBER),
            ("oct", OCTOBER),
            ("autumn", AUTUMN),
            ("year", {"date_from": "2024-01-01", "date_to": "2024-12-31"}),
            ("all", {}),
        ):
            totals[name] = payload(
                await session.call_tool("money_query", {"tags": [money.tag], **window})
            )

    def total(name: str) -> str:
        return str([row["total"] for row in totals[name]["rows"]])

    # September: the shop's prepayment (carried by its 30 Sep receipt) 40.00.
    # The water bill of 28 Sep is carried by its October receipt.
    # Before the fix: 140.00, the bill counting the water payment here too.
    assert total("sep") == "['40.00']"
    assert totals["sep"]["rows"][0]["document_ids"] == [money.prepaid]
    assert totals["sep"]["coverage"] == {
        "matched": 2,
        "included": 1,
        "excluded": {"payment_dated_elsewhere": 1},
        "needs_review": 0,
    }
    # October: water receipt 100.00 - credit 15.00 = 85.00. The shop's invoice
    # (its payment is September's) and the garage invoice (its payment is
    # carried by an undated receipt) are dated elsewhere. Before: 125.00.
    assert total("oct") == "['85.00']"
    assert sorted(totals["oct"]["rows"][0]["document_ids"]) == sorted([money.receipt, money.refund])
    assert totals["oct"]["coverage"] == {
        "matched": 4,
        "included": 2,
        "excluded": {"payment_dated_elsewhere": 2},
        "needs_review": 0,
    }
    # Both months: 100.00 + 40.00 - 15.00 = 125.00 = 40.00 + 85.00.
    assert total("autumn") == "['125.00']"
    assert totals["autumn"]["coverage"] == {
        "matched": 6,
        "included": 3,
        "excluded": {"duplicate_payment": 2, "payment_dated_elsewhere": 1},
        "needs_review": 0,
    }
    # The year adds February's 7.00: 132.00. Only a total with no date window
    # holds the garage payment (its receipt is undated): 152.00.
    assert total("year") == "['132.00']"
    assert total("all") == "['152.00']"
    assert totals["all"]["coverage"]["excluded"] == {"duplicate_payment": 3}
    for result in totals.values():
        assert _partitions(result["coverage"])


async def test_money_query_kind_filter_still_chooses_within_the_kind(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Non-date filters still select the candidates: with kind='utility-bill'
    the bill is the only candidate of the water payment, so it carries it, in
    September, its own date."""
    money = await _seed_month_payments(api_database_url, auth_user)
    totals: dict[str, list[str]] = {}
    async with connect(token) as session:
        for name, window in (("sep", SEPTEMBER), ("oct", OCTOBER), ("autumn", AUTUMN)):
            result = payload(
                await session.call_tool(
                    "money_query", {"kind": "utility-bill", "tags": [money.tag], **window}
                )
            )
            assert result["coverage"]["excluded"] == {}, name
            totals[name] = [row["total"] for row in result["rows"]]
    # Utility bills: the water bill 100.00 in September, the credit note
    # -15.00 in October; together 85.00.
    assert totals == {"sep": ["100.00"], "oct": ["-15.00"], "autumn": ["85.00"]}


async def test_money_query_groups_by_month_window_without_double_counting(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """The same rule holds per sender, with the other member's shop payment."""
    money = await _seed_month_payments(api_database_url, auth_user)
    async with connect(token) as session:
        sep = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag], "group_by": "sender", **SEPTEMBER}
            )
        )
        oct_ = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag], "group_by": "sender", **OCTOBER}
            )
        )
    # September: shop 40.00 only. October: water 100.00 - 15.00 = 85.00.
    assert {row["key"]: row["total"] for row in sep["rows"]} == {
        f"Example Shop {money.tag}": "40.00"
    }
    assert {row["key"]: row["total"] for row in oct_["rows"]} == {money.water: "85.00"}
    assert sep["coverage"]["excluded"] == {"payment_dated_elsewhere": 1}
    assert oct_["coverage"]["excluded"] == {"payment_dated_elsewhere": 2}


async def test_money_query_twin_of_a_cut_group_is_over_limit(
    connect: Connector, token: str, api_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A twin whose payment's row was cut by the row cap is over_limit, not
    a duplicate: its payment is in no row."""
    tag = f"mcp-cap-twin-{secrets.token_hex(4)}"

    async def seed(marker: str, amount: str, currency: str, amount_kind: AmountKind) -> int:
        return await _seed_document(
            api_database_url,
            f"{tag}-{marker}",
            tag_slugs=[tag],
            kind_slug="receipt",
            sender_name=f"Example Hotel {tag}" if currency == "USD" else f"Example Bakery {tag}",
            amount_total=Decimal(amount),
            currency=currency,
            amount_kind=amount_kind,
            reference=f"H-{tag}" if currency == "USD" else None,
            document_date=date(2024, 2, 29),
        )

    await seed("bakery", "50.00", "EUR", AmountKind.PAYMENT_MADE)
    await seed("hotel-invoice", "30.00", "USD", AmountKind.PAYMENT_DUE)
    await seed("hotel-receipt", "30.00", "USD", AmountKind.PAYMENT_MADE)
    monkeypatch.setattr(structured_query, "MAX_GROUP_ROWS", 1)
    async with connect(token) as session:
        result = payload(await session.call_tool("money_query", {"tags": [tag], **FEBRUARY}))
    # EUR 50.00 outweighs USD 30.00 (one payment, two documents): both hotel
    # documents are over_limit. Before: one over_limit, one "duplicate".
    assert [(row["currency"], row["total"]) for row in result["rows"]] == [("EUR", "50.00")]
    assert result["coverage"] == {
        "matched": 3,
        "included": 1,
        "excluded": {"over_limit": 2},
        "needs_review": 0,
    }


# ------------------------------------------------- blank and unknown filters
#
# W10 review, fix 2: a filter value that can match nothing made a silent,
# plausible total of 0 (`matched: 0`, nothing excluded). The filter types the
# three filtered tools share refuse blank values; money_query also refuses
# slugs and facet values the archive does not have.


async def test_filtered_tools_refuse_blank_filter_values(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """Blank or whitespace-only values are refused, by all three tools that
    share the filter types; surrounding whitespace is stripped."""
    money = await _seed_money(api_database_url, auth_user)
    blank_arguments: list[dict[str, Any]] = [
        {"tags": [money.tag, ""]},
        {"tags": [money.tag, "   "]},
        {"facets": {"category": ""}},
        {"facets": {" ": "energy"}},
        {"sender": ""},
        {"sender": "  "},
        {"recipient": ""},
        {"kind": " "},
        {"projects": [""]},
        {"matters": ["\t"]},
    ]
    async with connect(token) as session:
        refused = {
            tool: [
                await session.call_tool(tool, {**extra, **arguments})
                for arguments in blank_arguments
            ]
            for tool, extra in (
                ("money_query", {}),
                ("search_documents", {}),
                ("semantic_search", {"query": "anything"}),
            )
        }
        padded = payload(
            await session.call_tool(
                "money_query", {"tags": [f"  {money.tag} "], "sender": f" {money.sender} "}
            )
        )
    for tool, results in refused.items():
        for arguments, result in zip(blank_arguments, results, strict=True):
            message = error_text(result)
            assert "at least 1 character" in message, (tool, arguments, message)
            assert "Error calling tool" not in message
    # Stripped, the values match: the energy sender's one payment, 100.00 -
    # 15.25 (the March 1000.00 is another sender's).
    assert [row["total"] for row in padded["rows"]] == ["84.75"]


async def test_money_query_refuses_unknown_slugs_and_facet_values(
    connect: Connector, token: str, api_database_url: str, auth_user: AuthUser
) -> None:
    """An unknown tag, project, matter, facet or facet value would make a
    total of 0; money_query names them all and where the real ones are."""
    money = await _seed_money(api_database_url, auth_user)
    facet = f"{money.tag}-f"
    await _label(api_database_url, money.invoice, facet, "known")
    async with connect(token) as session:
        unknown_tag = await session.call_tool(
            "money_query", {"tags": [money.tag, f"{money.tag}-nope"]}
        )
        unknown_value = await session.call_tool(
            "money_query", {"tags": [money.tag], "facets": {facet: "unknown"}}
        )
        unknown_facet = await session.call_tool(
            "money_query", {"tags": [money.tag], "facets": {f"{facet}-x": "known"}}
        )
        unknown_slugs = await session.call_tool(
            "money_query",
            {
                "tags": [money.tag],
                "projects": [f"{money.tag}-p"],
                "matters": [f"{money.tag}-m"],
            },
        )
        known = payload(
            await session.call_tool(
                "money_query", {"tags": [money.tag], "facets": {facet: "known"}}
            )
        )
        # The search tools keep answering with an empty result: a zero there
        # is not a total, and their counts already say nothing matched.
        searched = payload(
            await session.call_tool("search_documents", {"tags": [f"{money.tag}-nope"]})
        )

    assert error_text(unknown_tag) == (
        f"unknown tag '{money.tag}-nope'. get_library_context lists the tags, projects, "
        "matters and facet values the filters take; list_tags, list_projects and "
        "list_matters list them with document counts."
    )
    assert error_text(unknown_value).startswith(f"unknown facet value {facet}='unknown'.")
    assert error_text(unknown_facet).startswith(f"unknown facet '{facet}-x'.")
    assert error_text(unknown_slugs).startswith(
        f"unknown project '{money.tag}-p'; unknown matter '{money.tag}-m'."
    )
    # The labelled invoice's payment: 100.00, one payment.
    assert [row["total"] for row in known["rows"]] == ["100.00"]
    assert searched["total"] == 0
