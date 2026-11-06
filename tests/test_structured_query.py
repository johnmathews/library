"""Tests for structured/analytical queries over extracted metadata."""

import hashlib
import re
from collections.abc import AsyncIterator, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.charts.query import chart_series
from library.charts.rule import Clause, Rule
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.models import (
    AmountKind,
    Document,
    DocumentSource,
    Facet,
    FacetValue,
    Grain,
    Kind,
    ReviewStatus,
    Sender,
)
from library.search import DocumentFilters
from library.spend_lines import LineInput, replace_lines
from library.structured_query import (
    Coverage,
    count_coverage,
    distinct_senders,
    list_documents,
    query_documents,
    sum_amount,
)
from tests.conftest import FIXTURE_VOCABULARY

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Document))
        await session.execute(delete(Sender))
        await session.commit()
        yield session


async def _sender(session: AsyncSession, name: str) -> Sender:
    existing = (
        await session.execute(select(Sender).where(Sender.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=name)
    session.add(sender)
    await session.commit()
    return sender


#: What ``seed`` gives an amount-bearing document when the caller says nothing.
#: ``sum_amount`` reads ``amount_kind``, so a document seeded without one is
#: *undecided* and never enters a total — which would silently empty the
#: fixtures of every pre-existing case here. Defaulting keeps those cases
#: meaning what they meant; the undecided state is asked for explicitly, by
#: passing ``amount_kind=None`` to a seed that also passes an ``amount``.
#:
#: A quote defaults to ``estimate`` because that is what a quote's amount *is*
#: (``docs/money-facts.md`` §2) and it is the kind ``sum_amount`` totals when the
#: caller asks about quotes specifically. Defaulting it to a spend kind instead
#: would make the quote cases pass while describing a document the archive
#: would never produce.
_DEFAULT_AMOUNT_KIND = AmountKind.PAYMENT_DUE
_DEFAULT_QUOTE_AMOUNT_KIND = AmountKind.ESTIMATE

_UNSET: Any = object()


async def seed(
    session: AsyncSession,
    marker: str,
    *,
    sender_name: str | None = None,
    kind_slug: str | None = None,
    document_date: date | None = None,
    amount: str | None = None,
    currency: str | None = None,
    amount_kind: AmountKind | None = _UNSET,
    reference: str | None = None,
    labels: dict[str, str] | None = None,
    lines: Sequence[tuple[str, dict[str, str] | None]] | None = None,
) -> int:
    sender = await _sender(session, sender_name) if sender_name else None
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    if amount_kind is _UNSET:
        if amount is None:
            amount_kind = None
        elif kind_slug == "quote":
            amount_kind = _DEFAULT_QUOTE_AMOUNT_KIND
        else:
            amount_kind = _DEFAULT_AMOUNT_KIND
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=document_date,
        amount_total=Decimal(amount) if amount is not None else None,
        amount_kind=amount_kind,
        reference=reference,
        currency=currency,
    )
    session.add(document)
    await session.commit()
    for facet_key, value_key in (labels or {}).items():
        await set_document_label(session, document.id, facet_key, value_key)
    if labels:
        await session.commit()
    if lines is not None:
        await replace_lines(
            session,
            document.id,
            [
                LineInput(amount=Decimal(line_amount), labels=line_labels or {})
                for line_amount, line_labels in lines
            ],
        )
        await session.commit()
    return document.id


@pytest.fixture
async def vocabulary(session: AsyncSession) -> dict[str, tuple[str, ...]]:
    """``FIXTURE_VOCABULARY``, created once for the facet-filter cases.

    The module's own ``session`` fixture truncates documents and senders but not
    the facet tables, so creation is get-or-create rather than unconditional.
    """
    for ordinal, (facet_key, value_keys) in enumerate(FIXTURE_VOCABULARY.items()):
        existing = await session.scalar(select(Facet.id).where(Facet.key == facet_key))
        if existing is None:
            await create_facet(session, facet_key, facet_key.replace("_", " ").title(), ordinal)
        for value_key in value_keys:
            already = await session.scalar(
                select(FacetValue.id)
                .join(Facet, Facet.id == FacetValue.facet_id)
                .where(Facet.key == facet_key, FacetValue.key == value_key)
            )
            if already is None:
                await create_value(session, facet_key, value_key, value_key.title())
    await session.commit()
    return FIXTURE_VOCABULARY


async def _fact_rows(session: AsyncSession, document_id: int) -> list[tuple[int | None, bool]]:
    """``(line_id, is_canonical)`` for a document's ``spend_facts`` rows.

    Read directly, so a test that means to exercise the view's *line* branch can
    say so rather than infer it from a total the document branch would also
    produce.
    """
    rows = await session.execute(
        text(
            "SELECT line_id, is_canonical FROM spend_facts "
            "WHERE document_id = :document_id ORDER BY line_id NULLS FIRST"
        ),
        {"document_id": document_id},
    )
    return [(row[0], row[1]) for row in rows.all()]


async def test_distinct_senders_ranked_by_document_count(session: AsyncSession) -> None:
    await seed(session, "v1", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "v2", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "e1", sender_name="Eneco", kind_slug="utility-bill")

    groups = (
        await distinct_senders(session, filters=DocumentFilters(kind_slug="utility-bill"))
    ).rows

    assert [(group.sender, group.document_count) for group in groups] == [
        ("Vattenfall", 2),
        ("Eneco", 1),
    ]
    assert all(group.document_ids for group in groups)


async def test_distinct_senders_honours_date_window(session: AsyncSession) -> None:
    """'Who was my energy provider last year?' — filter kind + date range."""
    await seed(
        session,
        "old",
        sender_name="OldEnergy",
        kind_slug="utility-bill",
        document_date=date(2024, 6, 1),
    )
    await seed(
        session,
        "new",
        sender_name="NewEnergy",
        kind_slug="utility-bill",
        document_date=date(2025, 6, 1),
    )

    groups = (
        await distinct_senders(
            session,
            filters=DocumentFilters(
                kind_slug="utility-bill", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
            ),
        )
    ).rows

    assert [group.sender for group in groups] == ["NewEnergy"]


async def test_sum_amount_groups_by_currency(session: AsyncSession) -> None:
    await seed(session, "a", kind_slug="invoice", amount="100.00", currency="EUR")
    await seed(session, "b", kind_slug="invoice", amount="50.50", currency="EUR")
    await seed(session, "c", kind_slug="invoice", amount="10.00", currency="USD")

    groups = (await sum_amount(session, filters=DocumentFilters(kind_slug="invoice"))).rows

    totals = {(group.currency, group.total) for group in groups}
    assert totals == {("EUR", "150.50"), ("USD", "10.00")}


async def test_sum_amount_excludes_quotes_from_spend(session: AsyncSession) -> None:
    """Quotes are not real expenditure: excluded from spend totals by default."""
    await seed(session, "real", kind_slug="invoice", amount="100.00", currency="EUR")
    await seed(session, "quote", kind_slug="quote", amount="999.00", currency="EUR")

    groups = (await sum_amount(session, filters=DocumentFilters())).rows

    # Only the invoice counts; the quote is ignored.
    assert {(g.currency, g.total) for g in groups} == {("EUR", "100.00")}


async def test_sum_amount_can_total_quotes_when_requested(session: AsyncSession) -> None:
    """Explicitly filtering kind='quote' totals the quotes themselves."""
    await seed(session, "real", kind_slug="invoice", amount="100.00", currency="EUR")
    await seed(session, "quote", kind_slug="quote", amount="999.00", currency="EUR")

    groups = (await sum_amount(session, filters=DocumentFilters(kind_slug="quote"))).rows

    assert {(g.currency, g.total) for g in groups} == {("EUR", "999.00")}


async def test_sum_amount_grouped_by_sender(session: AsyncSession) -> None:
    await seed(session, "s1", sender_name="Acme", amount="20.00", currency="EUR")
    await seed(session, "s2", sender_name="Acme", amount="30.00", currency="EUR")
    await seed(session, "s3", sender_name="Globex", amount="5.00", currency="EUR")

    groups = (await sum_amount(session, filters=DocumentFilters(), group_by="sender")).rows

    by_sender = {group.key: group.total for group in groups}
    assert by_sender == {"Acme": "50.00", "Globex": "5.00"}


async def test_list_documents_newest_first(session: AsyncSession) -> None:
    older = await seed(session, "older", document_date=date(2024, 1, 1))
    newer = await seed(session, "newer", document_date=date(2025, 1, 1))

    refs = (await list_documents(session, filters=DocumentFilters())).rows

    assert [ref.id for ref in refs[:2]] == [newer, older]


async def test_list_documents_narrows_by_project_slug(session: AsyncSession) -> None:
    from library.models import Project

    in_project = await seed(session, "in-project")
    await seed(session, "out-of-project")

    document = (
        await session.execute(select(Document).where(Document.id == in_project))
    ).scalar_one()
    document.projects = [Project(slug="renovation", name="Renovation")]
    await session.commit()

    refs = (
        await list_documents(session, filters=DocumentFilters(project_slugs=("renovation",)))
    ).rows

    assert [ref.id for ref in refs] == [in_project]


async def test_query_documents_dispatch_distinct_senders(session: AsyncSession) -> None:
    await seed(session, "q1", sender_name="Vattenfall", kind_slug="utility-bill")

    result = await query_documents(
        session, filters=DocumentFilters(kind_slug="utility-bill"), aggregate="distinct_senders"
    )

    assert result["result_type"] == "distinct_senders"
    assert result["rows"][0]["sender"] == "Vattenfall"


async def test_count_coverage_partitions_matched_into_included_and_excluded(
    session: AsyncSession,
) -> None:
    """matched = included + sum(excluded.values()), always."""
    await seed(session, "cov1", kind_slug="utility-bill", amount="10.00", currency="EUR")
    await seed(session, "cov2", kind_slug="utility-bill", amount="20.00", currency="EUR")
    await seed(session, "cov3", kind_slug="utility-bill")  # no amount

    coverage: Coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.matched == 3
    assert coverage.included == 2
    assert coverage.excluded == {"no_amount": 1}
    assert coverage.needs_review == 0
    assert coverage.included + sum(coverage.excluded.values()) == coverage.matched


async def test_count_coverage_omits_zero_reasons(session: AsyncSession) -> None:
    """A reason that excluded nothing is not reported — an empty dict means
    'the rows account for everything that matched'."""
    await seed(session, "cov4", kind_slug="invoice", amount="5.00", currency="EUR")

    coverage: Coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="invoice"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.excluded == {}


async def test_count_coverage_counts_needs_review_among_included(
    session: AsyncSession,
) -> None:
    """needs_review counts flagged documents that ARE in the rows — a flagged
    document the aggregate already dropped must not be double-reported."""
    included_id = await seed(
        session, "cov5", kind_slug="utility-bill", amount="30.00", currency="EUR"
    )
    excluded_id = await seed(session, "cov6", kind_slug="utility-bill")  # no amount
    for document_id in (included_id, excluded_id):
        document = await session.get(Document, document_id)
        assert document is not None
        document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    coverage: Coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.included == 1
    assert coverage.needs_review == 1


async def test_sum_amount_reports_documents_with_no_amount(session: AsyncSession) -> None:
    """The headline bug: a spend total that silently omits bills whose amount
    never extracted. The number is still returned — but so is the omission."""
    await seed(session, "s1", kind_slug="utility-bill", amount="100.00", currency="EUR")
    await seed(session, "s2", kind_slug="utility-bill", amount="50.00", currency="EUR")
    await seed(session, "s3", kind_slug="utility-bill")  # amount extraction failed

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert result.rows[0].total == "150.00"
    assert result.coverage.matched == 3
    assert result.coverage.included == 2
    assert result.coverage.excluded == {"no_amount": 1}


async def test_sum_amount_reports_the_quote_exclusion(session: AsyncSession) -> None:
    """Excluding quotes from spend is correct AND surprising, so it is disclosed
    rather than merely documented."""
    await seed(session, "s4", kind_slug="invoice", amount="200.00", currency="EUR")
    await seed(session, "s5", kind_slug="quote", amount="9999.00", currency="EUR")

    result = await sum_amount(session, filters=DocumentFilters())

    assert result.rows[0].total == "200.00"
    assert result.coverage.excluded == {"quote_not_spend": 1}


async def test_sum_amount_grouped_by_sender_reports_senderless_documents(
    session: AsyncSession,
) -> None:
    """group_by='sender' INNER JOINs Sender, so a document with no extracted
    sender drops out of a grouped total as well as an ungrouped one."""
    await seed(session, "s6", sender_name="Vattenfall", amount="80.00", currency="EUR")
    await seed(session, "s7", amount="20.00", currency="EUR")  # no sender

    result = await sum_amount(session, filters=DocumentFilters(), group_by="sender")

    assert [(row.key, row.total) for row in result.rows] == [("Vattenfall", "80.00")]
    assert result.coverage.excluded == {"no_sender": 1}


async def test_sum_amount_flags_untrusted_amounts(session: AsyncSession) -> None:
    """A summed amount the validator could not ground in the document text is
    counted, and reported as needing review."""
    document_id = await seed(
        session, "s8", kind_slug="utility-bill", amount="70.00", currency="EUR"
    )
    document = await session.get(Document, document_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert result.coverage.needs_review == 1


async def test_sum_amount_grouped_by_sender_partitions_a_senderless_quote_once(
    session: AsyncSession,
) -> None:
    """A document that is BOTH a quote and senderless must land in exactly one
    exclusion bucket, not two — the invariant is the regression test."""
    await seed(session, "s9", kind_slug="quote", amount="500.00", currency="EUR")  # no sender

    result = await sum_amount(session, filters=DocumentFilters(), group_by="sender")

    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_sum_amount_grouped_by_kind_reports_kindless_documents(
    session: AsyncSession,
) -> None:
    """group_by='kind' INNER JOINs Kind, so a document with no extracted kind
    drops out of a grouped total, reported as `no_kind`."""
    await seed(session, "k1", kind_slug="invoice", amount="40.00", currency="EUR")
    await seed(session, "k2", amount="10.00", currency="EUR")  # no kind

    result = await sum_amount(session, filters=DocumentFilters(), group_by="kind")

    assert result.coverage.excluded == {"no_kind": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_sum_amount_amountless_quote_counted_once_under_no_amount(
    session: AsyncSession,
) -> None:
    """An amountless quote must be reported under `no_amount` only — not also
    under `quote_not_spend` — so it is not double-counted."""
    await seed(session, "q1", kind_slug="quote")  # no amount at all

    result = await sum_amount(session, filters=DocumentFilters())

    assert result.coverage.excluded == {"no_amount": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_list_documents_reports_truncation(session: AsyncSession) -> None:
    """'List every invoice from 2024' must not return the newest N as though
    that were all of them."""
    for index in range(5):
        await seed(session, f"trunc{index}", kind_slug="invoice", sender_name="Acme")

    result = await list_documents(session, filters=DocumentFilters(kind_slug="invoice"), limit=2)

    assert len(result.rows) == 2
    assert result.coverage.matched == 5
    assert result.coverage.included == 2
    assert result.coverage.excluded == {"over_limit": 3}


async def test_list_documents_within_limit_reports_nothing_excluded(
    session: AsyncSession,
) -> None:
    await seed(session, "whole1", kind_slug="ticket")

    result = await list_documents(session, filters=DocumentFilters(kind_slug="ticket"), limit=50)

    assert result.coverage.excluded == {}
    assert result.coverage.included == result.coverage.matched == 1


async def test_list_documents_counts_needs_review_in_the_returned_page(
    session: AsyncSession,
) -> None:
    """needs_review describes the rows the model can see, not the whole match."""
    flagged = await seed(session, "flag1", kind_slug="warranty")
    document = await session.get(Document, flagged)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await list_documents(session, filters=DocumentFilters(kind_slug="warranty"))

    assert result.coverage.needs_review == 1


async def test_distinct_senders_reports_documents_with_no_sender(
    session: AsyncSession,
) -> None:
    """The sender join is an INNER JOIN, so a document whose sender never
    extracted is absent from 'who were my providers?' entirely."""
    await seed(session, "ds1", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "ds2", kind_slug="utility-bill")  # sender extraction failed

    result = await distinct_senders(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert [group.sender for group in result.rows] == ["Vattenfall"]
    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"no_sender": 1}


async def test_list_documents_rows_carry_review_status(session: AsyncSession) -> None:
    """A per-row trust flag, so the model can caveat one line of a list rather
    than the whole answer."""
    document_id = await seed(session, "rs1", kind_slug="receipt")
    document = await session.get(Document, document_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await list_documents(session, filters=DocumentFilters(kind_slug="receipt"))

    assert result.rows[0].review_status == "needs_review"


async def test_query_documents_result_carries_coverage(session: AsyncSession) -> None:
    """Coverage has to survive the dispatch layer or the model never sees it."""
    await seed(session, "qc1", kind_slug="utility-bill", amount="12.00", currency="EUR")
    await seed(session, "qc2", kind_slug="utility-bill")

    result = await query_documents(
        session, filters=DocumentFilters(kind_slug="utility-bill"), aggregate="sum_amount"
    )

    assert result["coverage"] == {
        "matched": 2,
        "included": 1,
        "excluded": {"no_amount": 1},
        "needs_review": 0,
    }


async def test_sum_amount_review_status_filter_does_not_shrink_matched(
    session: AsyncSession,
) -> None:
    """A review_status filter must not silently shrink the denominator: a
    caller asking for filters.review_status='verified' should see `matched`
    count every document that met the OTHER filters (verified AND
    needs_review alike), with the needs_review one reported as an explicit
    `filtered_review_status` exclusion rather than vanishing before `matched`
    is even computed."""
    verified_id = await seed(
        session, "rs-v1", kind_slug="utility-bill", amount="10.00", currency="EUR"
    )
    flagged_id = await seed(
        session, "rs-v2", kind_slug="utility-bill", amount="20.00", currency="EUR"
    )
    document = await session.get(Document, flagged_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()
    # verified_id defaults to ReviewStatus.UNREVIEWED or VERIFIED depending on
    # the model default; pin it explicitly so the filter has a real target.
    verified_document = await session.get(Document, verified_id)
    assert verified_document is not None
    verified_document.review_status = ReviewStatus.VERIFIED
    await session.commit()

    result = await sum_amount(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", review_status=ReviewStatus.VERIFIED),
    )

    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"filtered_review_status": 1}
    assert result.coverage.needs_review == 0
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert result.rows[0].total == "10.00"


async def test_distinct_senders_review_status_filter_does_not_shrink_matched(
    session: AsyncSession,
) -> None:
    """Same exposure as sum_amount: distinct_senders must not let a
    review_status filter shrink `matched` without disclosing the drop."""
    verified_id = await seed(session, "rs-d1", sender_name="Vattenfall", kind_slug="utility-bill")
    flagged_id = await seed(session, "rs-d2", sender_name="Eneco", kind_slug="utility-bill")
    document = await session.get(Document, flagged_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()
    verified_document = await session.get(Document, verified_id)
    assert verified_document is not None
    verified_document.review_status = ReviewStatus.VERIFIED
    await session.commit()

    result = await distinct_senders(
        session,
        filters=DocumentFilters(kind_slug="utility-bill", review_status=ReviewStatus.VERIFIED),
    )

    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"filtered_review_status": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert [group.sender for group in result.rows] == ["Vattenfall"]


async def test_list_documents_review_status_filter_does_not_shrink_matched(
    session: AsyncSession,
) -> None:
    """list_documents has the same exposure: `matched` must count both the
    verified and the flagged document, with the flagged one disclosed as
    `filtered_review_status` rather than silently absent."""
    verified_id = await seed(session, "rs-l1", kind_slug="receipt")
    flagged_id = await seed(session, "rs-l2", kind_slug="receipt")
    document = await session.get(Document, flagged_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()
    verified_document = await session.get(Document, verified_id)
    assert verified_document is not None
    verified_document.review_status = ReviewStatus.VERIFIED
    await session.commit()

    result = await list_documents(
        session,
        filters=DocumentFilters(kind_slug="receipt", review_status=ReviewStatus.VERIFIED),
    )

    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"filtered_review_status": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert [ref.id for ref in result.rows] == [verified_id]


async def test_sum_amount_quote_group_by_sender_partition_holds(
    session: AsyncSession,
) -> None:
    """kind_slug='quote' combined with group_by='sender' is the exact branch
    two reviewers had to verify by hand when the partition bug was fixed:
    quotes are the subject of the query here, so they must NOT be excluded as
    quote_not_spend, and the partition invariant must still hold."""
    await seed(
        session, "gq1", sender_name="Acme", kind_slug="quote", amount="100.00", currency="EUR"
    )
    await seed(
        session, "gq2", sender_name="Acme", kind_slug="quote", amount="50.00", currency="EUR"
    )
    await seed(session, "gq3", kind_slug="quote", amount="25.00", currency="EUR")  # no sender

    result = await sum_amount(
        session, filters=DocumentFilters(kind_slug="quote"), group_by="sender"
    )

    assert "quote_not_spend" not in result.coverage.excluded
    assert result.coverage.excluded == {"no_sender": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert {(row.key, row.total) for row in result.rows} == {("Acme", "150.00")}


# --- sum_amount over `spend_facts` (#136) ------------------------------------
#
# Every case below was observed FAILING against the pre-#136 aggregate, which
# summed `documents.amount_total` directly. That matters more here than usual:
# the three defects were documents wrongly *included*, and `coverage.excluded`
# has no bucket that reports an over-count — so a fixture that merely contains
# the awkward document, without asserting the total it must not reach, passes
# against both the broken and the fixed query. Each assertion below is written
# against the NUMBER, not against the document's presence.


async def test_sum_amount_refund_reduces_the_total(session: AsyncSession) -> None:
    """A refund is money returned: it must subtract, not add.

    The sign lives in `amount_kind`, never in `amount_total` (which is always a
    magnitude), so summing the column directly gets this exactly backwards —
    115.00 instead of 85.00, a number wrong by twice the refund.
    """
    await seed(
        session,
        "paid",
        kind_slug="receipt",
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    await seed(
        session,
        "refunded",
        kind_slug="receipt",
        amount="15.00",
        currency="EUR",
        amount_kind=AmountKind.REFUND,
    )

    result = await sum_amount(session, filters=DocumentFilters())

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "85.00")}
    # The refund is *included*, not excluded: it contributed, negatively.
    assert result.coverage.included == 2
    assert result.coverage.excluded == {}


async def test_sum_amount_counts_one_payment_once_across_an_invoice_and_a_receipt(
    session: AsyncSession,
) -> None:
    """One payment documented twice must be totalled once, and say so.

    The pair merges under rule R2 (same sender, same reference), so `spend_facts`
    marks exactly one of them canonical. Summing `documents.amount_total` reads
    neither, and returns 200.00 for a single 100.00 payment.
    """
    invoice = await seed(
        session,
        "inv",
        sender_name="Acme",
        kind_slug="invoice",
        amount="100.00",
        currency="EUR",
        reference="AC-4471",
        amount_kind=AmountKind.PAYMENT_DUE,
    )
    receipt = await seed(
        session,
        "rec",
        sender_name="Acme",
        kind_slug="receipt",
        amount="100.00",
        currency="EUR",
        reference="AC-4471",
        amount_kind=AmountKind.PAYMENT_MADE,
    )

    result = await sum_amount(session, filters=DocumentFilters())

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "100.00")}
    # The rows account for one document, and coverage names the other one's fate
    # rather than leaving the reader to notice 2 documents made a 1-document sum.
    assert [row.document_count for row in result.rows] == [1]
    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"duplicate_payment": 1}
    # Exactly one of the pair contributed; which one is the view's call.
    assert result.rows[0].document_ids in ([invoice], [receipt])


async def test_sum_amount_excludes_a_coverage_limit_and_a_balance(
    session: AsyncSession,
) -> None:
    """A policy's cover ceiling and an account balance are not expenditure.

    Both are legitimate `amount_total` values with no place in a spend total —
    the failure this makes visible is a total nearly two hundred times the money
    actually spent, presented with an empty `excluded` block.
    """
    await seed(
        session,
        "spend",
        kind_slug="receipt",
        amount="50.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    await seed(
        session,
        "ceiling",
        kind_slug="certificate",
        amount="9000.00",
        currency="EUR",
        amount_kind=AmountKind.COVERAGE_LIMIT,
    )
    await seed(
        session,
        "balance",
        kind_slug="other",
        amount="1200.00",
        currency="EUR",
        amount_kind=AmountKind.BALANCE,
    )

    result = await sum_amount(session, filters=DocumentFilters())

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "50.00")}
    assert result.coverage.excluded == {"not_summable_kind": 2}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_sum_amount_excludes_a_document_whose_amount_kind_is_undecided(
    session: AsyncSession,
) -> None:
    """An undecided `amount_kind` is not a licence to guess the number means spend.

    `amount_kind` is NULL until the classifier or a human decides it, and NULL
    must fall out under a named reason rather than being summed on the assumption
    that an unclassified amount is expenditure.
    """
    await seed(
        session,
        "decided",
        kind_slug="receipt",
        amount="40.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    await seed(
        session,
        "undecided",
        kind_slug="receipt",
        amount="999.00",
        currency="EUR",
        amount_kind=None,
    )

    result = await sum_amount(session, filters=DocumentFilters())

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "40.00")}
    assert result.coverage.excluded == {"not_summable_kind": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_sum_amount_totals_an_itemised_document_from_its_lines_exactly_once(
    session: AsyncSession,
) -> None:
    """The view's LINE branch, not its document branch, and one document either way.

    A split document is emitted as one `spend_facts` row *per line*, so a naive
    aggregate over the view double-counts the document in `document_count` and
    lists it twice in `document_ids` even though the money is right. Merging the
    itemised invoice with its receipt makes the case red against the old query
    too — the canonical slot goes to the line-bearing document, so the rows that
    contribute are line rows.
    """
    invoice = await seed(
        session,
        "itemised",
        sender_name="Acme",
        kind_slug="invoice",
        amount="100.00",
        currency="EUR",
        reference="AC-8820",
        amount_kind=AmountKind.PAYMENT_DUE,
        lines=[("60.00", None), ("40.00", None)],
    )
    await seed(
        session,
        "itemised-receipt",
        sender_name="Acme",
        kind_slug="receipt",
        amount="100.00",
        currency="EUR",
        reference="AC-8820",
        amount_kind=AmountKind.PAYMENT_MADE,
    )

    # Stated, not assumed: the contributing document really is represented by two
    # canonical LINE rows. Without this the test would still pass for a view that
    # emitted one synthetic document row, and the line branch would be untested.
    fact_rows = await _fact_rows(session, invoice)
    assert len(fact_rows) == 2
    assert all(line_id is not None for line_id, _ in fact_rows)
    assert all(is_canonical for _, is_canonical in fact_rows)

    result = await sum_amount(session, filters=DocumentFilters())

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "100.00")}
    assert [row.document_count for row in result.rows] == [1]
    assert result.rows[0].document_ids == [invoice]
    assert result.coverage.excluded == {"duplicate_payment": 1}


async def test_sum_amount_partitions_every_new_reason_in_order(
    session: AsyncSession,
) -> None:
    """All five drop reasons at once, each document landing in exactly one.

    The exclusions are successive refinements of one include chain, so a document
    that fails several gates must be reported under the FIRST one it fails. This
    is the case that would go red if a later gate were widened to stand on its own
    rather than on everything before it.
    """
    await seed(session, "kept", kind_slug="receipt", amount="10.00", currency="EUR")
    # No amount at all — and also a quote, and also senderless. `no_amount` wins.
    await seed(session, "amountless", kind_slug="quote", currency="EUR")
    # A quote with an amount and an unsummable kind: `quote_not_spend` wins.
    await seed(session, "quoted", kind_slug="quote", amount="500.00", currency="EUR")
    # Not a quote, has an amount, undecided kind: `not_summable_kind`.
    await seed(
        session, "undecided", kind_slug="receipt", amount="70.00", currency="EUR", amount_kind=None
    )
    # A merged twin: passes every earlier gate, fails only canonicality.
    await seed(
        session,
        "twin-a",
        sender_name="Globex",
        kind_slug="invoice",
        amount="33.00",
        currency="EUR",
        reference="GX-1",
        amount_kind=AmountKind.PAYMENT_DUE,
    )
    await seed(
        session,
        "twin-b",
        sender_name="Globex",
        kind_slug="receipt",
        amount="33.00",
        currency="EUR",
        reference="GX-1",
        amount_kind=AmountKind.PAYMENT_MADE,
    )

    result = await sum_amount(session, filters=DocumentFilters())

    assert result.coverage.matched == 6
    assert result.coverage.excluded == {
        "no_amount": 1,
        "quote_not_spend": 1,
        "not_summable_kind": 1,
        "duplicate_payment": 1,
    }
    assert result.coverage.included == 2
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "43.00")}


async def test_sum_amount_totals_quotes_from_their_estimate_kind(
    session: AsyncSession,
) -> None:
    """'How much have my quotes come to?' sums `estimate`, not the spend kinds.

    A quote's amount IS an `estimate` — the kind that exists precisely so it
    cannot contaminate a spend total. When the caller asks about quotes, that is
    the kind being asked for, so the summable set follows the question.
    """
    await seed(session, "spend", kind_slug="receipt", amount="10.00", currency="EUR")
    await seed(session, "q1", kind_slug="quote", amount="800.00", currency="EUR")
    await seed(session, "q2", kind_slug="quote", amount="150.00", currency="EUR")

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="quote"))

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "950.00")}
    assert result.coverage.excluded == {}


async def test_sum_amount_quote_total_excludes_a_quote_that_is_not_an_estimate(
    session: AsyncSession,
) -> None:
    """The quote branch is a kind gate too, not an unconditional pass.

    A document filed under kind `quote` but carrying a cover ceiling is not an
    estimate of anything the user asked to total, and must fall out under a named
    reason rather than inflate the quote total by two orders of magnitude.
    """
    await seed(session, "real-quote", kind_slug="quote", amount="150.00", currency="EUR")
    await seed(
        session,
        "misfiled",
        kind_slug="quote",
        amount="9000.00",
        currency="EUR",
        amount_kind=AmountKind.COVERAGE_LIMIT,
    )

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="quote"))

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "150.00")}
    assert result.coverage.excluded == {"not_summable_kind": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


async def test_sum_amount_narrows_by_facet(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """A facet filter reaches the money aggregate.

    `DocumentFilters` has carried `facets` since the vocabulary shipped; nothing
    in Ask could express one, so the curated `category` vocabulary was invisible
    to every money question.
    """
    await seed(
        session,
        "sw",
        kind_slug="receipt",
        amount="120.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    await seed(
        session,
        "sup",
        kind_slug="receipt",
        amount="60.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "supplies"},
    )

    result = await sum_amount(session, filters=DocumentFilters(facets={"category": "software"}))

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "120.00")}
    assert result.coverage.matched == 1


#: February, with its 29th: every line-facet case below is dated inside it, so
#: an off-by-one on the window's end would drop a document rather than pass.
_FEB_2024 = {"date_from": date(2024, 2, 1), "date_to": date(2024, 2, 29)}


async def _seed_line_facet_fixture(session: AsyncSession) -> dict[str, int]:
    """The itemised-payment fixture the line-facet cases share.

    * ``invoice`` — labelled ``category=services``, itemised 60.00 (inherits
      services) + 40.00 (overridden to supplies).
    * ``receipt`` — the same payment (same sender and reference, rule R2),
      labelled ``supplies`` on the whole 100.00. The invoice carries the
      payment wherever both are candidates, because a line-bearing document
      ranks first (``docs/money-facts.md`` §5.2) — so the receipt's 100.00
      must never be what a supplies total reads.
    * ``joinery`` — no document label at all, itemised 10.00 (services) +
      20.00 (unlabelled). Only a line carries the facet; the 20.00 must not
      ride along with it.
    * ``amountless`` — labelled services, no amount: the ``no_amount`` case.
    """
    invoice = await seed(
        session,
        "lf-invoice",
        sender_name="Example Builder",
        kind_slug="invoice",
        document_date=date(2024, 2, 8),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        reference="EB-201",
        labels={"category": "services"},
        lines=[("60.00", None), ("40.00", {"category": "supplies"})],
    )
    receipt = await seed(
        session,
        "lf-receipt",
        sender_name="Example Builder",
        kind_slug="receipt",
        document_date=date(2024, 2, 14),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        reference="EB-201",
        labels={"category": "supplies"},
    )
    joinery = await seed(
        session,
        "lf-joinery",
        sender_name="Example Joinery",
        kind_slug="invoice",
        document_date=date(2024, 2, 9),
        amount="30.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        reference="EJ-7",
        lines=[("10.00", {"category": "services"}), ("20.00", None)],
    )
    amountless = await seed(
        session,
        "lf-amountless",
        sender_name="Example Builder",
        kind_slug="invoice",
        document_date=date(2024, 2, 20),
        labels={"category": "services"},
    )
    return {"invoice": invoice, "receipt": receipt, "joinery": joinery, "amountless": amountless}


async def test_sum_amount_facet_counts_only_the_money_rows_carrying_it(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """A facet filter sums the rows (document or line) whose merged labels carry
    it, and the carrying twin is chosen among the rows that do.

    The expected numbers, from the ``spend_facts`` rules:

    * **services = 70.00**: the invoice's 60.00 line (it inherits services from
      the invoice) plus the joinery's 10.00 line. Not the invoice's 40.00
      supplies line, not the joinery's unlabelled 20.00, and not the receipt,
      whose only row says supplies. The old document-label filter gave 100.00:
      the whole invoice, and no joinery at all.
    * **supplies = 40.00**: the invoice's 40.00 line. Both twins carry
      supplies, so both are candidates, and the line-bearing invoice ranks
      first; the receipt is ``duplicate_payment``. Neither 100.00 (the
      receipt's total) nor 140.00 (both twins) is right: they are one
      payment, and its split says 40.00 of it was supplies. The old filter
      matched only the receipt by its document label and gave 100.00.

    Asked services → supplies → services, so a result that leaked state from
    the previous call (a cached CTE, a reused statement) cannot pass.
    """
    ids = await _seed_line_facet_fixture(session)

    async def total(value: str) -> tuple[set[tuple[str | None, str]], list[int], Coverage]:
        result = await sum_amount(
            session, filters=DocumentFilters(facets={"category": value}, **_FEB_2024)
        )
        cited = sorted(i for row in result.rows for i in row.document_ids)
        return {(row.currency, row.total) for row in result.rows}, cited, result.coverage

    for value in ("services", "supplies", "services"):
        rows, cited, coverage = await total(value)
        if value == "services":
            assert rows == {("EUR", "70.00")}
            assert cited == sorted([ids["invoice"], ids["joinery"]])
            # The amountless services document is matched and named, not lost.
            assert coverage == Coverage(
                matched=3, included=2, excluded={"no_amount": 1}, needs_review=0
            )
        else:
            assert rows == {("EUR", "40.00")}
            assert cited == [ids["invoice"]]
            assert coverage == Coverage(
                matched=2, included=1, excluded={"duplicate_payment": 1}, needs_review=0
            )


async def test_sum_amount_line_facet_grouped_by_sender_splits_the_matching_lines(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """The grouped path reads the same per-row facet: each sender's group holds
    only its own matching lines, so the groups still add up to the ungrouped
    70.00 (60.00 + 10.00) rather than to the documents' 130.00."""
    await _seed_line_facet_fixture(session)

    result = await sum_amount(
        session,
        filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024),
        group_by="sender",
    )

    assert {(row.key, row.total) for row in result.rows} == {
        ("Example Builder", "60.00"),
        ("Example Joinery", "10.00"),
    }
    assert result.coverage.excluded == {"no_amount": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )


@pytest.mark.parametrize("value", ["services", "supplies"])
async def test_sum_amount_line_facet_total_equals_the_chart_over_the_same_rows(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]], value: str
) -> None:
    """``sum_amount(facets={category: v})`` and a chart ruled ``category in [v]``
    read the same ``spend_facts`` rows, so over one fixture they give one total.

    Not a comparison of two copies of one rule: the chart filters on
    ``spend_facts.labels`` per row (``charts/rule.py``) and keeps the view's
    archive-wide twin, ``sum_amount`` chooses the twin within the filter. Here
    the two choices coincide (the itemised invoice wins both ways), which is
    what makes agreement the expected answer. The twin cases below, where the
    two choices could differ, hold because the view merges a payment's labels
    onto its canonical row (migration 0041).
    """
    await _seed_line_facet_fixture(session)

    money = await sum_amount(
        session, filters=DocumentFilters(facets={"category": value}, **_FEB_2024)
    )
    series = await chart_series(
        session,
        Rule(all=[Clause(facet="category", values=[value])]),
        grain=Grain.MONTH,
        split=None,
        currency="EUR",
        since=_FEB_2024["date_from"],
        until=_FEB_2024["date_to"],
    )

    assert [(row.currency, Decimal(row.total)) for row in money.rows] == [("EUR", series.total)]
    assert series.total != Decimal("0")


async def test_sum_amount_line_facet_chooses_the_twin_whose_money_carries_it(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """A twin's label reaches the canonical document, which then carries it.

    An itemised invoice with unlabelled lines and its receipt, labelled
    supplies, are one 100.00 payment. The invoice ranks first (it has lines),
    and since migration 0041 its lines inherit the receipt's supplies
    (``docs/money-facts.md`` §5.2: a payment's counted row carries its twins'
    labels). So both documents' money carries supplies, both are candidates,
    and the invoice carries the payment, as it does in every chart: 100.00,
    cited to the invoice, with the receipt reported as its
    ``duplicate_payment``.

    Before 0041 only the receipt's row carried supplies, so the receipt was
    the one candidate and was cited instead. Either way the total is 100.00;
    what this pins is that the cited document is the one charts count.
    """
    invoice = await seed(
        session,
        "unlabelled-lines",
        sender_name="Example Builder",
        kind_slug="invoice",
        document_date=date(2024, 2, 15),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        reference="EB-305",
        lines=[("70.00", None), ("30.00", None)],
    )
    await seed(
        session,
        "labelled-receipt",
        sender_name="Example Builder",
        kind_slug="receipt",
        document_date=date(2024, 2, 16),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        reference="EB-305",
        labels={"category": "supplies"},
    )

    result = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "supplies"}, **_FEB_2024)
    )

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "100.00")}
    assert result.rows[0].document_ids == [invoice]
    assert result.coverage == Coverage(
        matched=2, included=1, excluded={"duplicate_payment": 1}, needs_review=0
    )


async def _chart_total(
    session: AsyncSession, value: str, since: date | None, until: date | None
) -> Decimal:
    series = await chart_series(
        session,
        Rule(all=[Clause(facet="category", values=[value])]),
        grain=Grain.MONTH,
        split=None,
        currency="EUR",
        since=since,
        until=until,
    )
    return series.total


async def _bill_and_receipt(
    session: AsyncSession,
    reference: str,
    *,
    bill_date: date,
    receipt_date: date,
    bill_labels: dict[str, str] | None = None,
    receipt_labels: dict[str, str] | None = None,
) -> tuple[int, int]:
    """One 84.20 payment documented twice: a bill and its receipt (rule R2).

    The receipt is ``payment_made``, so it is the canonical document."""
    bill = await seed(
        session,
        f"{reference}-bill",
        sender_name="Example Water",
        kind_slug="utility-bill",
        document_date=bill_date,
        amount="84.20",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        reference=reference,
        labels=bill_labels,
    )
    receipt = await seed(
        session,
        f"{reference}-receipt",
        sender_name="Example Water",
        kind_slug="receipt",
        document_date=receipt_date,
        amount="84.20",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        reference=reference,
        labels=receipt_labels,
    )
    return bill, receipt


async def test_sum_amount_counts_a_payment_whose_label_only_its_bill_carries(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """F4, same month: the bill is labelled services, the canonical receipt is
    not. The total was right before 0041 too; what changes is that the receipt
    now carries services as well, so it is the counted document (as in the
    chart) and the bill is its ``duplicate_payment``."""
    _bill, receipt = await _bill_and_receipt(
        session,
        "EW-1",
        bill_date=date(2024, 2, 5),
        receipt_date=date(2024, 2, 12),
        bill_labels={"category": "services"},
    )

    result = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024)
    )

    assert [(row.currency, row.total) for row in result.rows] == [("EUR", "84.20")]
    assert result.rows[0].document_ids == [receipt]
    assert result.coverage == Coverage(
        matched=2, included=1, excluded={"duplicate_payment": 1}, needs_review=0
    )
    assert await _chart_total(
        session, "services", _FEB_2024["date_from"], _FEB_2024["date_to"]
    ) == Decimal("84.20")


async def test_sum_amount_lands_a_cross_month_payment_in_the_charts_month(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """F4 across a month boundary: a bill of 28 January labelled services, its
    unlabelled receipt of 3 February. The receipt is canonical, so the chart
    draws the payment in February; the services total must say February too,
    and January and February must add up to the whole."""
    await _bill_and_receipt(
        session,
        "EW-2",
        bill_date=date(2024, 1, 28),
        receipt_date=date(2024, 2, 3),
        bill_labels={"category": "services"},
    )
    january = {"date_from": date(2024, 1, 1), "date_to": date(2024, 1, 31)}
    services = {"category": "services"}

    jan = await sum_amount(session, filters=DocumentFilters(facets=services, **january))
    feb = await sum_amount(session, filters=DocumentFilters(facets=services, **_FEB_2024))
    whole = await sum_amount(session, filters=DocumentFilters(facets=services))

    assert jan.rows == []
    assert jan.coverage.excluded == {"payment_dated_elsewhere": 1}
    assert [(row.currency, row.total) for row in feb.rows] == [("EUR", "84.20")]
    assert [(row.currency, row.total) for row in whole.rows] == [("EUR", "84.20")]
    assert await _chart_total(
        session, "services", january["date_from"], january["date_to"]
    ) == Decimal("0")
    assert await _chart_total(
        session, "services", _FEB_2024["date_from"], _FEB_2024["date_to"]
    ) == Decimal("84.20")


async def test_sum_amount_does_not_count_an_itemised_payment_twice_across_facets(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """An unlabelled invoice itemised 60.00 + 40.00 (supplies), and its receipt
    labelled services. Before 0041 the services total was the receipt's whole
    100.00 and the supplies total the invoice's 40.00 line: 140.00 for one
    100.00 payment. Now the invoice's 60.00 line inherits services, the
    invoice carries the payment for both facets, and the two add up to 100.00."""
    invoice = await seed(
        session,
        "it-invoice",
        sender_name="Example Builder",
        kind_slug="invoice",
        document_date=date(2024, 2, 5),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        reference="EB-410",
        lines=[("60.00", None), ("40.00", {"category": "supplies"})],
    )
    await seed(
        session,
        "it-receipt",
        sender_name="Example Builder",
        kind_slug="receipt",
        document_date=date(2024, 2, 12),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        reference="EB-410",
        labels={"category": "services"},
    )

    totals = {}
    for value in ("services", "supplies"):
        result = await sum_amount(
            session, filters=DocumentFilters(facets={"category": value}, **_FEB_2024)
        )
        assert [row.document_ids for row in result.rows] == [[invoice]]
        totals[value] = Decimal(result.rows[0].total)
        assert totals[value] == await _chart_total(
            session, value, _FEB_2024["date_from"], _FEB_2024["date_to"]
        )

    assert totals == {"services": Decimal("60.00"), "supplies": Decimal("40.00")}


async def test_sum_amount_uses_the_canonical_amount_of_an_unequal_manual_merge(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """A hand merge (no rule requires equal amounts there): an invoice of
    100.00 labelled services and an unlabelled receipt of 90.00. The receipt
    is canonical and inherits services, so the services total is the
    receipt's 90.00, the figure every chart shows, not the invoice's 100.00."""
    invoice = await seed(
        session,
        "mm-invoice",
        sender_name="Example Garage",
        kind_slug="invoice",
        document_date=date(2024, 2, 5),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        labels={"category": "services"},
    )
    receipt = await seed(
        session,
        "mm-receipt",
        sender_name="Example Garage",
        kind_slug="receipt",
        document_date=date(2024, 2, 12),
        amount="90.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    await session.execute(
        text("INSERT INTO payment_overrides (kind, doc_a, doc_b) VALUES ('MERGE', :a, :b)"),
        {"a": min(invoice, receipt), "b": max(invoice, receipt)},
    )
    await session.commit()

    result = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024)
    )

    assert [(row.currency, row.total) for row in result.rows] == [("EUR", "90.00")]
    assert result.rows[0].document_ids == [receipt]
    assert await _chart_total(
        session, "services", _FEB_2024["date_from"], _FEB_2024["date_to"]
    ) == Decimal("90.00")


#: A read of the ``spend_facts`` view in emitted SQL (not a column reference
#: such as ``spend_facts.labels``).
_VIEW_READ = re.compile(r"\b(?:FROM|JOIN)\s+spend_facts\b")


async def test_sum_amount_facet_reads_the_view_no_more_often_than_unfaceted(
    engine: AsyncEngine, session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """Each evaluation of ``spend_facts`` re-runs its payment grouping, which
    is most of a money statement's cost. A facet filter must not add one:
    the coverage test shares the ranking's ``facet_facts`` CTE.

    Measured on 4,000 merged documents after ``ANALYZE``: a correlated
    ``EXISTS`` on the view in the coverage statement took a faceted total
    from 1.35 s to about 6.6 s, and an uncorrelated ``IN`` over a second read
    of the view to about 2.0 s. Both read the view once more per statement
    than this allows. Timings are not asserted; the statement shape is.
    """
    await _seed_line_facet_fixture(session)
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if "spend_facts" in statement:
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        await sum_amount(session, filters=DocumentFilters(**_FEB_2024))
        plain = [len(_VIEW_READ.findall(statement)) for statement in statements]
        statements.clear()
        await sum_amount(
            session, filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024)
        )
        faceted = [len(_VIEW_READ.findall(statement)) for statement in statements]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)

    assert len(plain) == 2
    assert faceted == plain


async def test_sum_amount_facet_matches_a_document_by_its_money_not_its_label(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """``matched`` under a facet: the documents with a money row carrying it,
    plus the amountless documents labelled with it (they have no money row to
    ask, and ``no_amount`` is the reason worth reporting).

    The adversarial one is ``relabelled``: labelled services, but its only line
    is overridden to supplies, so none of its money is services. Counting it
    as matched under services would leave it in no coverage bucket, since it
    is not a candidate for any payment, and the partition (``included +
    excluded == matched``) would break. Under supplies it is an ordinary
    included document.
    """
    relabelled = await seed(
        session,
        "relabelled",
        sender_name="Example Builder",
        kind_slug="invoice",
        document_date=date(2024, 2, 10),
        amount="50.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        labels={"category": "services"},
        lines=[("50.00", {"category": "supplies"})],
    )
    await seed(
        session,
        "amountless-services",
        kind_slug="invoice",
        document_date=date(2024, 2, 11),
        labels={"category": "services"},
    )
    await seed(
        session,
        "amountless-supplies",
        kind_slug="invoice",
        document_date=date(2024, 2, 12),
        labels={"category": "supplies"},
    )

    services = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024)
    )
    supplies = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "supplies"}, **_FEB_2024)
    )

    assert services.rows == []
    assert services.coverage == Coverage(
        matched=1, included=0, excluded={"no_amount": 1}, needs_review=0
    )
    assert {(row.currency, row.total) for row in supplies.rows} == {("EUR", "50.00")}
    assert supplies.rows[0].document_ids == [relabelled]
    assert supplies.coverage == Coverage(
        matched=2, included=1, excluded={"no_amount": 1}, needs_review=0
    )


async def test_sum_amount_facet_reports_an_amountless_labelled_document_as_no_amount(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """An amountless document has no ``spend_facts`` row to carry a facet, so
    its document label is what matches it: a services total still says one
    services document had no amount, rather than reading as complete."""
    await seed(
        session,
        "paid",
        kind_slug="receipt",
        document_date=date(2024, 2, 3),
        amount="25.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
        labels={"category": "services"},
    )
    await seed(
        session,
        "unread",
        kind_slug="invoice",
        document_date=date(2024, 2, 4),
        labels={"category": "services"},
    )
    await seed(
        session,
        "unread-elsewhere",
        kind_slug="invoice",
        document_date=date(2024, 2, 5),
        labels={"category": "software"},
    )

    result = await sum_amount(
        session, filters=DocumentFilters(facets={"category": "services"}, **_FEB_2024)
    )

    assert {(row.currency, row.total) for row in result.rows} == {("EUR", "25.00")}
    assert result.coverage == Coverage(
        matched=2, included=1, excluded={"no_amount": 1}, needs_review=0
    )


async def test_sum_amount_two_facets_must_hold_on_the_same_money_row(
    session: AsyncSession, vocabulary: dict[str, tuple[str, ...]]
) -> None:
    """Facets AND-compose per row, with each row's merged labels: a line's own
    label beside the facets it inherits from its document.

    The invoice is labelled ``scope=business``. Its 60.00 line is services (and
    inherits business); its 40.00 line is supplies and overrides scope to
    personal. ``{category: supplies, scope: business}`` therefore holds on no
    row, though the document has a supplies row and a business row; a check
    per facet rather than per row would answer 40.00.
    """
    await seed(
        session,
        "scoped",
        kind_slug="invoice",
        document_date=date(2024, 2, 6),
        amount="100.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_DUE,
        labels={"scope": "business"},
        lines=[
            ("60.00", {"category": "services"}),
            ("40.00", {"category": "supplies", "scope": "personal"}),
        ],
    )

    async def total(facets: dict[str, str]) -> set[tuple[str | None, str]]:
        result = await sum_amount(session, filters=DocumentFilters(facets=facets, **_FEB_2024))
        return {(row.currency, row.total) for row in result.rows}

    assert await total({"category": "services", "scope": "business"}) == {("EUR", "60.00")}
    assert await total({"category": "supplies", "scope": "business"}) == set()
    assert await total({"category": "supplies", "scope": "personal"}) == {("EUR", "40.00")}
    assert await total({"scope": "business"}) == {("EUR", "60.00")}


async def test_sum_amount_grouped_by_sender_still_partitions_with_the_new_reasons(
    session: AsyncSession,
) -> None:
    """`no_sender` remains the LAST gate, after the two new ones.

    A senderless document that also carries an unsummable kind must be reported
    as `not_summable_kind`, not as `no_sender`: the chain's order is the contract,
    and swapping two links leaves both totals right and both reasons wrong.
    """
    await seed(
        session,
        "has-sender",
        sender_name="Acme",
        kind_slug="receipt",
        amount="25.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )
    await seed(
        session,
        "senderless-and-unsummable",
        kind_slug="other",
        amount="4000.00",
        currency="EUR",
        amount_kind=AmountKind.BALANCE,
    )
    await seed(
        session,
        "senderless-but-summable",
        kind_slug="receipt",
        amount="5.00",
        currency="EUR",
        amount_kind=AmountKind.PAYMENT_MADE,
    )

    result = await sum_amount(session, filters=DocumentFilters(), group_by="sender")

    assert result.coverage.excluded == {"not_summable_kind": 1, "no_sender": 1}
    assert (
        result.coverage.included + sum(result.coverage.excluded.values()) == result.coverage.matched
    )
    assert {(row.key, row.total) for row in result.rows} == {("Acme", "25.00")}
