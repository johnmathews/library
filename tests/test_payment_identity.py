"""Payment identity: which documents describe one payment.

Every case here mirrors a real ambiguous shape in the archive, with invented
senders and amounts. The two that matter most are the pair four days apart that
must stay SEPARATE (two real purchases) and the pair months apart that must
MERGE (an invoice and the receipt that settled it).
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.models import AmountKind, Document, DocumentSource, DocumentStatus, Sender
from library.money.payments import add_override, collapse_counts, payment_group, payment_id_for

pytestmark = pytest.mark.integration


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _pair(
    database_url: str,
    rows: list[tuple[str | None, str, AmountKind | None, str | None]],
    currency: str | None = "EUR",
) -> list[int]:
    """Seed documents for ONE fresh sender. Rows are (date|None, amount, kind, ref)."""

    async def _work(session: AsyncSession) -> list[int]:
        sender = Sender(name=f"Vendor-{uuid.uuid4().hex[:8]}")
        session.add(sender)
        await session.flush()
        ids: list[int] = []
        for when, amount, kind, reference in rows:
            marker = f"pay:{uuid.uuid4()}"
            doc = Document(
                sha256=hashlib.sha256(marker.encode()).hexdigest(),
                mime_type="application/pdf",
                source=DocumentSource.UPLOAD,
                status=DocumentStatus.INDEXED,
                title=marker,
                sender_id=sender.id,
                document_date=date.fromisoformat(when) if when else None,
                amount_total=Decimal(amount),
                currency=currency,
                amount_kind=kind,
                reference=reference,
            )
            session.add(doc)
            await session.flush()
            ids.append(doc.id)
        return ids

    return asyncio.run(_run(database_url, _work))


def _group(database_url: str, document_id: int) -> list[int]:
    return asyncio.run(_run(database_url, lambda s: payment_group(s, document_id)))


def test_r1_same_day_same_amount_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
            ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r2_a_reference_match_merges_across_any_gap(api_database_url: str) -> None:
    """The case a date window cannot reach: a receipt issued months later."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-01-05", "900.00", AmountKind.PAYMENT_DUE, "K-100"),
            ("2026-03-20", "900.00", AmountKind.PAYMENT_MADE, "K-100"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_r3_complementary_kinds_within_sixty_days_merge(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-18", "13.25", AmountKind.PAYMENT_DUE, None),
            ("2026-08-24", "13.25", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_two_real_purchases_four_days_apart_stay_separate(api_database_url: str) -> None:
    """Both are payment_made, so R3 cannot fire. This is why complementarity,
    not a date window, is what makes date-tolerant merging safe."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert _group(api_database_url, a) == [a]
    assert _group(api_database_url, b) == [b]


def test_four_same_amount_invoices_merge_only_the_same_day_pair(
    api_database_url: str,
) -> None:
    a, b, c, d = _pair(
        api_database_url,
        [
            ("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2026-10-04", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2026-11-22", "689.40", AmountKind.PAYMENT_DUE, None),
            ("2027-01-05", "689.40", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, c) == [c]
    assert _group(api_database_url, d) == [d]


def test_differing_references_veto_a_same_day_merge(api_database_url: str) -> None:
    a, _b = _pair(
        api_database_url,
        [
            ("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-1"),
            ("2026-02-20", "300.00", AmountKind.PAYMENT_DUE, "R-2"),
        ],
    )
    assert _group(api_database_url, a) == [a]


def test_dateless_documents_still_pair_on_reference(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            (None, "75.00", AmountKind.PAYMENT_DUE, "Z-9"),
            (None, "75.00", AmountKind.PAYMENT_MADE, "Z-9"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_currency_less_documents_can_pair(api_database_url: str) -> None:
    """`currency = currency` is NULL for two NULL currencies; IS NOT DISTINCT FROM is not."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-05-01", "60.00", AmountKind.PAYMENT_DUE, None),
            ("2026-05-01", "60.00", AmountKind.PAYMENT_MADE, None),
        ],
        currency=None,
    )
    assert _group(api_database_url, a) == sorted([a, b])


def test_unbackfilled_amount_kinds_do_not_merge_on_r3(api_database_url: str) -> None:
    """NULL amount_kind must not satisfy complementarity, or an un-backfilled
    archive would silently collapse unrelated same-amount documents."""
    a, _b = _pair(
        api_database_url,
        [("2026-04-01", "99.00", None, None), ("2026-04-20", "99.00", None, None)],
    )
    assert _group(api_database_url, a) == [a]


def test_a_chain_of_three_collapses_to_one_payment(api_database_url: str) -> None:
    a, b, c = _pair(
        api_database_url,
        [
            ("2026-09-01", "30.00", AmountKind.PAYMENT_DUE, "T-1"),
            ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1"),
            ("2026-09-01", "30.00", AmountKind.PAYMENT_MADE, "T-1"),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b, c])


def test_a_split_override_unmerges_an_automatic_pair(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-08-04", "48.00", AmountKind.PAYMENT_DUE, None),
            ("2026-08-04", "48.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "SPLIT", a, b)))
    assert _group(api_database_url, a) == [a]


def test_a_merge_override_joins_a_pair_no_rule_merges(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_an_override_pair_is_ordered_regardless_of_argument_order(
    api_database_url: str,
) -> None:
    """doc_a < doc_b is a check constraint; add_override must order the pair."""
    a, b = _pair(
        api_database_url,
        [
            ("2026-07-03", "14.37", AmountKind.PAYMENT_MADE, None),
            ("2026-07-07", "14.37", AmountKind.PAYMENT_MADE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", b, a)))
    assert _group(api_database_url, a) == sorted([a, b])


def test_a_deleted_partner_leaves_the_survivor_alone(api_database_url: str) -> None:
    a, b = _pair(
        api_database_url,
        [
            ("2026-06-01", "55.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-01", "55.00", AmountKind.PAYMENT_MADE, None),
        ],
    )

    async def _delete(session: AsyncSession) -> None:
        from datetime import UTC, datetime

        document = await session.get(Document, b)
        assert document is not None
        document.deleted_at = datetime.now(UTC)

    asyncio.run(_run(api_database_url, _delete))
    assert _group(api_database_url, a) == [a]


def test_a_deleted_override_partner_does_not_corrupt_the_survivors_payment_id(
    api_database_url: str,
) -> None:
    """The override-specific version of the case above.

    Unlike a rule edge (R1/R2/R3), which is only ever derived by joining two
    LIVE documents, the ``payment_edges`` override union previously had no
    ``deleted_at`` filter. A trashed document stayed reachable as a `member`
    in the ``payments`` view's recursive closure and could still win
    ``min(member)`` — so the LIVE survivor's ``payment_id`` became an id that
    no longer exists anywhere else in the API (`payment_id_for` on that id
    itself would 404). These two documents are seeded so that NO automatic
    rule connects them (different amounts, same kind — R1/R2/R3 all miss);
    the only edge between them is the MERGE override, isolating exactly the
    behaviour the view fix targets.
    """
    a, b = _pair(
        api_database_url,
        [
            ("2026-06-01", "55.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-01", "91.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    asyncio.run(_run(api_database_url, lambda s: add_override(s, "MERGE", a, b)))
    assert _group(api_database_url, a) == sorted([a, b])

    victim, survivor = sorted([a, b])

    async def _delete(session: AsyncSession) -> None:
        from datetime import UTC, datetime

        document = await session.get(Document, victim)
        assert document is not None
        document.deleted_at = datetime.now(UTC)

    asyncio.run(_run(api_database_url, _delete))

    # The survivor must be alone in its OWN payment, not carrying the
    # deleted document's id — `payment_id_for(survivor)` must equal
    # `survivor`, and its group must contain nothing else.
    survivor_payment_id = asyncio.run(_run(api_database_url, lambda s: payment_id_for(s, survivor)))
    assert survivor_payment_id == survivor
    assert _group(api_database_url, survivor) == [survivor]


def test_collapse_counts_reports_payments_and_documents(api_database_url: str) -> None:
    """A merged pair plus a standalone document: 2 payments from 3 documents."""
    a, b, standalone = _pair(
        api_database_url,
        [
            ("2026-06-15", "22.00", AmountKind.PAYMENT_DUE, None),
            ("2026-06-15", "22.00", AmountKind.PAYMENT_MADE, None),
            ("2026-06-16", "77.00", AmountKind.PAYMENT_DUE, None),
        ],
    )
    assert _group(api_database_url, a) == sorted([a, b])
    assert _group(api_database_url, standalone) == [standalone]

    async def _work(session: AsyncSession) -> tuple[int, int]:
        return await collapse_counts(session, [a, b, standalone])

    payments, documents = asyncio.run(_run(api_database_url, _work))
    assert (payments, documents) == (2, 3)


def test_collapse_counts_of_no_documents_is_zero_without_a_query(
    api_database_url: str,
) -> None:
    async def _work(session: AsyncSession) -> tuple[int, int]:
        return await collapse_counts(session, [])

    assert asyncio.run(_run(api_database_url, _work)) == (0, 0)
