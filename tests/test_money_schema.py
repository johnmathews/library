"""The money-facts columns and the overrides table."""

import asyncio
import hashlib
import importlib.util
import pathlib
import re
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.extraction.schema import AMOUNT_KINDS
from library.models import (
    AmountKind,
    Document,
    DocumentSource,
    DocumentStatus,
    PaymentOverride,
)

pytestmark = pytest.mark.integration


def _migration_amount_kinds() -> tuple[str, ...]:
    """``_AMOUNT_KINDS`` as migration 0034 actually declares it.

    0034 is the migration that owns the vocabulary's database-side
    enforcement — it adds the ``ck_documents_amount_kind`` CHECK that 0033
    never had — so it is the current third copy this test compares against.
    Loaded from the file rather than copied here: a copy would be a fourth
    place to drift. The module name starts with a digit, so it cannot be
    imported by name.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations/versions/0034_refund_amount_kind.py"
    )
    spec = importlib.util.spec_from_file_location("refund_amount_kind_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    kinds: tuple[str, ...] = module._AMOUNT_KINDS
    return kinds


def test_the_three_copies_of_the_amount_kinds_agree() -> None:
    """The eight kinds exist in three places and nothing else compares them.

    Drift is dangerous in both directions. A kind the migration's enum accepts
    but ``AMOUNT_KINDS`` omits is silently normalised to NULL and disappears;
    a kind ``AMOUNT_KINDS`` accepts but the enum rejects makes
    ``AmountKind(metadata.amount_kind)`` in ``extraction/apply.py`` raise
    *outside* the facet savepoint, destroying the whole extraction rather than
    one field.
    """
    from_enum = tuple(kind.value for kind in AmountKind)
    assert from_enum == AMOUNT_KINDS
    assert from_enum == _migration_amount_kinds()


def _migration_negative_kinds() -> set[str]:
    """The kind literal(s) named in 0034's ``_SIGN_GUARD`` SQL.

    ``_SIGN_GUARD`` and ``AMOUNT_SIGN`` are two hand-maintained lists of the
    same fact — which kinds are negative — so nothing stops them from
    disagreeing. This parses the guard's SQL text for the ``'kind'`` literals
    it names, the same style as ``_migration_amount_kinds`` above.
    """
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations/versions/0034_refund_amount_kind.py"
    )
    spec = importlib.util.spec_from_file_location("refund_amount_kind_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    guard: str = module._SIGN_GUARD
    return set(re.findall(r"'([a-z_]+)'", guard))


def test_the_sign_guard_and_the_sign_map_agree_on_which_kinds_are_negative() -> None:
    """The migration hardcodes the guard's kind(s); ``AMOUNT_SIGN`` is the
    Python source of truth. One list drifting from the other would silently
    let a negative kind merge with a positive one, or vice versa."""
    from library.models import AMOUNT_SIGN

    negative_in_python = {kind.value for kind, sign in AMOUNT_SIGN.items() if sign < 0}
    assert _migration_negative_kinds() == negative_in_python


def test_refund_is_a_known_kind_and_contributes_negatively() -> None:
    from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS, AmountKind

    assert AmountKind.REFUND in SUMMABLE_AMOUNT_KINDS
    assert AMOUNT_SIGN[AmountKind.REFUND] == -1
    assert AMOUNT_SIGN[AmountKind.PAYMENT_DUE] == 1
    assert AMOUNT_SIGN[AmountKind.PAYMENT_MADE] == 1
    assert AMOUNT_SIGN[AmountKind.ASSESSMENT] == 1


def test_summable_is_derived_from_the_sign_map_not_declared_twice() -> None:
    """Two hand-maintained lists are two lists that can disagree."""
    from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS

    assert frozenset(AMOUNT_SIGN) == SUMMABLE_AMOUNT_KINDS


def test_a_non_contributing_kind_has_no_sign() -> None:
    from library.models import AMOUNT_SIGN, AmountKind

    for kind in (
        AmountKind.COVERAGE_LIMIT,
        AmountKind.BALANCE,
        AmountKind.ESTIMATE,
        AmountKind.NONE,
    ):
        assert kind not in AMOUNT_SIGN


def test_the_database_now_rejects_a_kind_outside_the_vocabulary(
    api_database_url: str,
) -> None:
    """`0033` shipped `amount_kind` as an unconstrained varchar(16).

    Verified against Postgres: `'not_a_real_kind'` inserted successfully
    before `0034`. The vocabulary is load-bearing twice over — it decides
    what enters a total and what may merge — so it belongs in the database.
    """
    import sqlalchemy
    from sqlalchemy import create_engine, text

    engine = create_engine(
        api_database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    )
    with engine.begin() as connection, pytest.raises(sqlalchemy.exc.IntegrityError):
        connection.execute(
            text(
                "INSERT INTO documents (sha256, mime_type, status, source, "
                "language, amount_kind, created_at, updated_at) VALUES "
                "(:s, 'application/pdf', 'ready', 'upload', 'en', "
                "'not_a_real_kind', now(), now())"
            ),
            {"s": "c" * 64},
        )


async def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await work(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _new_document(**kwargs: object) -> Document:
    marker = f"money:{uuid.uuid4()}"
    return Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        status=DocumentStatus.INDEXED,
        title=marker,
        **kwargs,
    )


def test_amount_kind_and_reference_round_trip(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> int:
        doc = _new_document(amount_kind=AmountKind.PAYMENT_MADE, reference="ABC-123")
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document).where(Document.id == document_id)),
        )
    ).scalar_one()
    assert stored.amount_kind is AmountKind.PAYMENT_MADE
    assert stored.reference == "ABC-123"


def test_amount_kind_defaults_to_null_not_to_a_payment(api_database_url: str) -> None:
    """NULL means 'not yet decided'. Task 4 treats it as not summable, so an
    un-backfilled archive under-reports rather than over-reports."""

    async def _work(session: AsyncSession) -> int:
        doc = _new_document()
        session.add(doc)
        await session.flush()
        return doc.id

    document_id = asyncio.run(_run(api_database_url, _work))
    stored = asyncio.run(
        _run(
            api_database_url,
            lambda s: s.execute(select(Document).where(Document.id == document_id)),
        )
    ).scalar_one()
    assert stored.amount_kind is None


def test_an_override_kind_outside_merge_or_split_is_rejected(api_database_url: str) -> None:
    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="NONSENSE", doc_a=1, doc_b=2))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))


def _two_document_ids(api_database_url: str) -> tuple[int, int]:
    """Two real, distinct documents.id values, ordered (lo, hi)."""

    async def _work(session: AsyncSession) -> tuple[int, int]:
        doc_1 = _new_document()
        doc_2 = _new_document()
        session.add_all([doc_1, doc_2])
        await session.flush()
        return (doc_1.id, doc_2.id)

    first, second = asyncio.run(_run(api_database_url, _work))
    return (first, second) if first < second else (second, first)


def test_an_override_with_doc_a_after_doc_b_is_rejected(api_database_url: str) -> None:
    """doc_a < doc_b is what makes an override pair canonical; a caller that
    hands the pair in the wrong order must be rejected, not silently accepted
    as a second, un-deduplicated row."""
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=hi, doc_b=lo))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))


def test_an_override_pair_cannot_be_recorded_twice_for_the_same_kind(api_database_url: str) -> None:
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        await session.flush()

    with pytest.raises(IntegrityError):
        asyncio.run(_run(api_database_url, _work))


def test_the_same_pair_is_allowed_under_a_different_kind(api_database_url: str) -> None:
    """The uniqueness is scoped to the (kind, doc_a, doc_b) triple, not just
    the pair: a MERGE and a SPLIT can both exist for the same two documents
    (e.g. a MERGE recorded in error, then a SPLIT undoing it) without one
    blocking the other."""
    lo, hi = _two_document_ids(api_database_url)

    async def _work(session: AsyncSession) -> None:
        session.add(PaymentOverride(kind="MERGE", doc_a=lo, doc_b=hi))
        session.add(PaymentOverride(kind="SPLIT", doc_a=lo, doc_b=hi))
        await session.flush()

    asyncio.run(_run(api_database_url, _work))
