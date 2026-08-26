"""Tests for structured/analytical queries over extracted metadata."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.models import Document, DocumentSource, Kind, ReviewStatus, Sender
from library.search import DocumentFilters
from library.structured_query import (
    Coverage,
    count_coverage,
    distinct_senders,
    list_documents,
    query_documents,
    sum_amount,
)

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


async def seed(
    session: AsyncSession,
    marker: str,
    *,
    sender_name: str | None = None,
    kind_slug: str | None = None,
    document_date: date | None = None,
    amount: str | None = None,
    currency: str | None = None,
) -> int:
    sender = await _sender(session, sender_name) if sender_name else None
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        sender=sender,
        kind=kind,
        document_date=document_date,
        amount_total=Decimal(amount) if amount is not None else None,
        currency=currency,
    )
    session.add(document)
    await session.commit()
    return document.id


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
