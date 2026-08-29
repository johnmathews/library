"""Shared test fixtures for the Library backend."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from procrastinate.testing import InMemoryConnector
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from library.app import create_app
from library.auth.deps import CSRF_COOKIE, CSRF_HEADER
from library.auth.passwords import hash_password
from library.config import get_settings
from library.db import get_session
from library.extraction import apply as extraction_apply_module
from library.facets.vocabulary import create_facet, create_value, set_document_label
from library.jobs import job_app, procrastinate_conninfo
from library.models import (
    AmountKind,
    Base,
    Document,
    DocumentSource,
    DocumentStatus,
    FxRate,
    Sender,
    User,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _llm_backend_is_the_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the suite on the metered-API backend, whatever the default is.

    ``ask_llm_backend`` ships defaulting to "subscription" so the deployed app
    uses the Claude subscription. Tests must not: that path shells out to the
    bundled Claude CLI and would make real network calls against real
    credentials. Pin it here rather than in each test, so a new test that
    exercises ask cannot silently start billing someone's subscription.

    Tests covering the subscription path set the backend explicitly and stub the
    SDK (see tests/test_ask_backend.py).
    """
    monkeypatch.setenv("LIBRARY_ASK_LLM_BACKEND", "api")
    monkeypatch.setenv("LIBRARY_SERIES_INSIGHT_LLM_BACKEND", "api")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _embedding_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the suite hermetic: embeddings off unless a test opts in.

    The embedding stage reaches for a network sidecar; defaulting it off means
    pipeline tests never make real HTTP calls. Tests exercising embedding set
    ``LIBRARY_EMBEDDING_ENABLED=true`` and monkeypatch the embed call.
    """
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _facet_labelling_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the suite hermetic: the ingest facet-labelling hook is a no-op by default.

    ``apply_extraction``'s success path calls ``label_and_apply``, which reaches
    the Anthropic API. Several extraction tests build a ``Settings`` with a
    *fake* API key (``anthropic_api_key="test-key"``, see
    ``test_extraction_apply.py::settings``) so ``extract`` itself can be
    monkeypatched — but that same fake key is enough for ``label_and_apply`` to
    open a real connection to api.anthropic.com and get a 401 back. The broad
    ``except Exception`` in the hook swallows that, so tests still pass, but the
    suite is no longer hermetic and now depends on network access. Default the
    hook to a no-op here, the same way embeddings are defaulted off above; a
    test exercising the hook itself monkeypatches
    ``library.extraction.apply.label_and_apply`` back to a stub (see
    ``test_extraction_apply.py::test_apply_extraction_calls_the_facet_labeller``).
    """

    async def _noop_label_and_apply(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(extraction_apply_module, "label_and_apply", _noop_label_and_apply)
    yield


@dataclass(frozen=True)
class AuthUser:
    """Credentials of a user created directly in the test database."""

    id: int
    username: str
    password: str


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """A fresh application instance (job queue swapped for an in-memory one)."""
    with job_app.replace_connector(InMemoryConnector()):
        yield create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """An HTTP test client bound to the app fixture."""
    with TestClient(app) as test_client:
        yield test_client


def alembic_config(database_url: str) -> Config:
    """An Alembic Config pointed at this repo's migrations and the given database."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def _create_database(admin_url: str, name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def create_database(admin_url: str, name: str) -> str:
    """Create (or recreate) a database in the test Postgres; return its asyncpg URL."""
    asyncio.run(_create_database(admin_url, name))
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """A real ephemeral Postgres 17 (with pgvector) for integration tests.

    Pinned to ``C.UTF-8`` so text ordering is byte-wise — matching both the
    existing production cluster (C collation) and Python's ``sorted``. The
    Debian-based pgvector image would otherwise default to a glibc linguistic
    collation and silently reorder taxonomy/sender listings. See docs/deployment.md.
    """
    container = PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg").with_env(
        "POSTGRES_INITDB_ARGS", "--locale=C.UTF-8"
    )
    with container:
        yield container


@pytest.fixture(scope="session")
def admin_database_url(postgres_container: PostgresContainer) -> str:
    """asyncpg URL for the container's default database (used to create others)."""
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database_url(admin_database_url: str) -> str:
    """A dedicated database migrated to head, shared by model-level tests."""
    url = create_database(admin_database_url, "library_models")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def api_database_url(admin_database_url: str) -> str:
    """A dedicated database migrated to head for API-level integration tests."""
    url = create_database(admin_database_url, "library_api")
    command.upgrade(alembic_config(url), "head")
    return url


#: Tables the migrations seed as reference data. Truncating these would break
#: every test that resolves a kind slug or a recipient, and they are not test
#: state — they are part of a migrated schema.
_SEEDED_TABLES: frozenset[str] = frozenset({"kinds", "recipients", "fx_rates", "alembic_version"})

#: Procrastinate's own tables. Not in Base.metadata (they are created by
#: Procrastinate's migrations, not ours), so they must be named explicitly or a
#: test's deferred jobs leak into the next test's job assertions.
_PROCRASTINATE_TABLES: tuple[str, ...] = (
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
)


@pytest.fixture(scope="session")
def _api_truncate_connection(api_database_url: str) -> Iterator[Any]:
    """A sync psycopg connection used only to truncate between tests.

    Sync and separate on purpose. The app's sessions are created inside
    TestClient's own event loop (see ``api_app``), and asyncpg connections are
    loop-bound, so a truncation running on the test's loop cannot safely share
    the app's connection. A plain psycopg connection sidesteps the question
    entirely and costs one connection for the whole session.
    """
    import psycopg

    conninfo = procrastinate_conninfo(api_database_url)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        yield connection


@pytest.fixture(autouse=True)
def _truncate_api_database(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Leave the shared API database empty after every test that touched it.

    ``api_database_url`` is **session-scoped** — one migrated database shared by
    the whole API suite — so without this every test inherits the rows of every
    test that ran before it. That produced real, recurring bugs rather than
    theoretical ones: list endpoints default to ``limit=25``, so assertions on
    totals or on ``.first()`` silently depended on execution order, and two
    files had already grown their own ``delete(Document)`` workarounds.

    **In teardown, not setup.** A setup-time truncate would delete the
    ``auth_user`` row that ``api_client`` created for the test that is about to
    run. Cleaning up after means each test starts from whatever the previous
    test's teardown left, which is nothing.

    **No ``RESTART IDENTITY``.** Sequences stay monotonic, so a stale id held by
    a test can never coincidentally match a live row from a later one — it fails
    loudly with a 404 instead of quietly addressing someone else's data.

    Only runs for tests that actually requested the database: the fixture is
    autouse, so it must be cheap and silent for the ~900 tests that do not.
    """
    yield
    if "api_database_url" not in request.fixturenames:
        return
    connection = request.getfixturevalue("_api_truncate_connection")
    targets = [
        table.name for table in Base.metadata.sorted_tables if table.name not in _SEEDED_TABLES
    ]
    targets.extend(_PROCRASTINATE_TABLES)
    # One statement: TRUNCATE takes an ACCESS EXCLUSIVE lock per table, and
    # naming them together avoids both deadlock risk and per-table round trips.
    # CASCADE covers FK edges to anything not listed (procrastinate's, mainly).
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {', '.join(targets)} CASCADE")


@pytest.fixture
def api_app(
    api_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    """An app wired to the test database, with data_dir pointed at tmp_path.

    The session dependency is overridden with a NullPool engine created
    lazily inside the app's event loop (TestClient runs the app in its own
    loop, and asyncpg connections are loop-bound).
    """
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIBRARY_DATABASE_URL", api_database_url)
    # TestClient speaks plain http://testserver; Secure cookies would never
    # be sent back, so tests run with the dev override.
    monkeypatch.setenv("LIBRARY_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    application = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session
        finally:
            await engine.dispose()

    application.dependency_overrides[get_session] = override_session
    yield application
    get_settings.cache_clear()


@pytest.fixture
async def job_connector() -> AsyncIterator[InMemoryConnector]:
    """An open in-memory Procrastinate connector.

    Needed by tests that drive pipeline code directly (``advance_pipeline``
    defers the thumbnail job after OCR); deferred jobs can be inspected via
    ``connector.jobs``.
    """
    connector = InMemoryConnector()
    with job_app.replace_connector(connector):
        async with job_app.open_async():
            yield connector


async def _insert_user(
    database_url: str,
    username: str,
    password: str,
    *,
    is_active: bool = True,
    is_admin: bool = False,
) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(
                username=username,
                password_hash=hash_password(password),
                is_active=is_active,
                is_admin=is_admin,
            )
            session.add(user)
            await session.commit()
            return user.id
    finally:
        await engine.dispose()


def create_user(
    database_url: str,
    username: str | None = None,
    password: str = "correct horse battery staple",
    *,
    is_active: bool = True,
    is_admin: bool = False,
) -> AuthUser:
    """Insert a user (random unique username by default) from sync test code."""
    name = username or f"user-{uuid.uuid4().hex[:12]}"
    user_id = asyncio.run(
        _insert_user(database_url, name, password, is_active=is_active, is_admin=is_admin)
    )
    return AuthUser(id=user_id, username=name, password=password)


@pytest.fixture
def auth_user(api_database_url: str) -> AuthUser:
    """A fresh active user in the API test database."""
    return create_user(api_database_url)


@pytest.fixture
def admin_user(api_database_url: str) -> AuthUser:
    """A fresh active admin user in the API test database."""
    return create_user(api_database_url, is_admin=True)


def login(client: TestClient, user: AuthUser) -> None:
    """Log the client in as the given user and arm it for CSRF checks.

    Sets the ``X-CSRF-Token`` default header from the ``library_csrftoken``
    cookie so state-changing requests pass the double-submit check without
    per-test ceremony.
    """
    response = client.post(
        "/api/auth/login", json={"username": user.username, "password": user.password}
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]


@pytest.fixture
def api_client(
    api_app: FastAPI, api_database_url: str, auth_user: AuthUser
) -> Iterator[TestClient]:
    """Authenticated HTTP client against api_app with a real Procrastinate connector.

    Logged in as ``auth_user`` via the session cookie, with the CSRF header
    pre-set. Deferred jobs land in the test database's procrastinate_jobs
    table, so tests can assert on real queue rows.
    """
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as test_client:
        login(test_client, auth_user)
        yield test_client


@pytest.fixture
def admin_client(
    api_app: FastAPI, api_database_url: str, admin_user: AuthUser
) -> Iterator[TestClient]:
    """Authenticated HTTP client logged in as an admin user."""
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as test_client:
        login(test_client, admin_user)
        yield test_client


@pytest.fixture
def anon_client(api_app: FastAPI, api_database_url: str) -> Iterator[TestClient]:
    """Unauthenticated HTTP client against api_app (same wiring as api_client)."""
    connector = PsycopgConnector(conninfo=procrastinate_conninfo(api_database_url))
    with job_app.replace_connector(connector), TestClient(api_app) as test_client:
        yield test_client


async def _fetch_all(database_url: str, query: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query), params)
            return [tuple(row) for row in result.all()]
    finally:
        await engine.dispose()


def fetch_all(database_url: str, query: str, **params: object) -> list[tuple[object, ...]]:
    """Run a query against the given database from sync test code."""
    return asyncio.run(_fetch_all(database_url, query, **params))


@pytest.fixture
def seeded_document_id(api_database_url: str) -> int:
    """One indexed document, for tests that only need a valid documents.id."""
    import asyncio
    import hashlib
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from library.models import Document, DocumentSource, DocumentStatus

    async def _seed() -> int:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                marker = f"facet-fixture:{_uuid.uuid4()}"
                doc = Document(
                    sha256=hashlib.sha256(marker.encode()).hexdigest(),
                    mime_type="application/pdf",
                    source=DocumentSource.UPLOAD,
                    status=DocumentStatus.INDEXED,
                    title=marker,
                )
                session.add(doc)
                await session.flush()
                await session.commit()
                return doc.id
        finally:
            await engine.dispose()

    return asyncio.run(_seed())


@pytest.fixture
def payment_pair(api_database_url: str) -> tuple[int, int]:
    """Two documents the R1 rule merges into one payment: same sender, date,
    amount and currency, with complementary amount kinds."""
    import asyncio
    import hashlib
    import uuid as _uuid
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from library.models import (
        AmountKind,
        Document,
        DocumentSource,
        DocumentStatus,
        Sender,
    )

    async def _seed() -> tuple[int, int]:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                sender = Sender(name=f"PayVendor-{_uuid.uuid4().hex[:8]}")
                session.add(sender)
                await session.flush()
                ids: list[int] = []
                for kind in (AmountKind.PAYMENT_DUE, AmountKind.PAYMENT_MADE):
                    marker = f"paypair:{_uuid.uuid4()}"
                    doc = Document(
                        sha256=hashlib.sha256(marker.encode()).hexdigest(),
                        mime_type="application/pdf",
                        source=DocumentSource.UPLOAD,
                        status=DocumentStatus.INDEXED,
                        title=marker,
                        sender_id=sender.id,
                        document_date=date(2026, 8, 4),
                        amount_total=Decimal("48.00"),
                        currency="EUR",
                        amount_kind=kind,
                    )
                    session.add(doc)
                    await session.flush()
                    ids.append(doc.id)
                await session.commit()
                return ids[0], ids[1]
        finally:
            await engine.dispose()

    return asyncio.run(_seed())


# --- The charts-engine fixture vocabulary -----------------------------------
#
# `session`, `facets` and `document` are the shared building blocks for the
# spend-lines / spend-facts / chart-query work. They live here rather than in
# one test file because seven later tasks build on exactly this shape.

#: The date every fixture document carries unless the caller names another.
#: A real date rather than NULL on purpose: the R1 payment rule (same sender,
#: same currency, same amount, *same day*) can only ever merge two documents
#: that share a date, so a NULL default would make merge behaviour untestable
#: from these fixtures.
FIXTURE_DOCUMENT_DATE: date = date(2026, 3, 15)

#: The vocabulary the `facets` fixture creates. Deliberately contains two
#: facets carrying values that could be confused for each other's — `scope`
#: has no `services`, `category` does — so a test can prove a line label
#: cannot claim one facet while pointing at another facet's value.
FIXTURE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "category": ("software", "services", "supplies", "accountancy"),
    "scope": ("business", "personal"),
    "cost_type": ("subscription", "usage"),
}


@pytest.fixture
async def session(api_database_url: str) -> AsyncIterator[AsyncSession]:
    """An ``AsyncSession`` on the shared API database.

    Requesting ``api_database_url`` is what arms the autouse truncation, so a
    test using this fixture leaves nothing behind. ``expire_on_commit=False``
    matches the app's own session factory, so an ORM object read after a commit
    does not go looking for a fresh connection.
    """
    engine = create_async_engine(api_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest.fixture
async def facets(session: AsyncSession) -> dict[str, tuple[str, ...]]:
    """Create ``FIXTURE_VOCABULARY`` and return it.

    Committed rather than flushed: other sessions (an API client, a second
    engine) must be able to see the vocabulary a test's documents are labelled
    against.
    """
    for ordinal, (facet_key, value_keys) in enumerate(FIXTURE_VOCABULARY.items()):
        await create_facet(session, facet_key, facet_key.replace("_", " ").title(), ordinal)
        for value_key in value_keys:
            await create_value(session, facet_key, value_key, value_key.title())
    await session.commit()
    return FIXTURE_VOCABULARY


class DocumentFactory(Protocol):
    """Signature of the `document` fixture. See its docstring for the defaults."""

    async def __call__(
        self,
        *,
        amount_total: Decimal | str | None = None,
        amount_kind: AmountKind | None = None,
        document_date: date | None = FIXTURE_DOCUMENT_DATE,
        currency: str | None = "EUR",
        labels: Mapping[str, str] | None = None,
        deleted: bool = False,
        title: str | None = None,
        sender: str | None = None,
        reference: str | None = None,
    ) -> Document: ...


@pytest.fixture
async def document(session: AsyncSession, facets: dict[str, tuple[str, ...]]) -> DocumentFactory:
    """Make a document, committed, with everything the chart work needs on it.

    Depends on `facets` so `labels=` always resolves; the vocabulary is cheap
    and truncated with everything else, and making the dependency implicit
    removes an ordering trap from every later task.

    `sender` defaults to **None**, so two fixture documents never merge into one
    payment by accident — the payment rules all require a non-NULL, matching
    `sender_id`. A test that wants a merge names the sender on both documents;
    senders are created on demand and shared by name within a test.
    """

    async def _sender_id(name: str) -> int:
        existing = await session.scalar(select(Sender.id).where(Sender.name == name))
        if existing is not None:
            return int(existing)
        row = Sender(name=name)
        session.add(row)
        await session.flush()
        return row.id

    async def _make(
        *,
        amount_total: Decimal | str | None = None,
        amount_kind: AmountKind | None = None,
        document_date: date | None = FIXTURE_DOCUMENT_DATE,
        currency: str | None = "EUR",
        labels: Mapping[str, str] | None = None,
        deleted: bool = False,
        title: str | None = None,
        sender: str | None = None,
        reference: str | None = None,
    ) -> Document:
        marker = uuid.uuid4().hex
        row = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            status=DocumentStatus.INDEXED,
            title=title if title is not None else f"fixture-{marker[:8]}",
            document_date=document_date,
            amount_total=Decimal(amount_total) if amount_total is not None else None,
            amount_kind=amount_kind,
            # R2 (same sender + same reference, any date gap) is the strongest
            # merge rule and the one 0034's sign guard exists for, so a
            # canonicality test needs to be able to set this.
            reference=reference,
            currency=currency,
            sender_id=await _sender_id(sender) if sender is not None else None,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        session.add(row)
        await session.flush()
        for facet_key, value_key in (labels or {}).items():
            await set_document_label(session, row.id, facet_key, value_key)
        await session.commit()
        return row

    return _make


class FxRateFactory(Protocol):
    """Signature of the `fx_rates` fixture. See its docstring for the direction."""

    async def __call__(self, rows: Sequence[tuple[str | date, str, str | Decimal]]) -> None: ...


@pytest.fixture
async def fx_rates(session: AsyncSession) -> AsyncIterator[FxRateFactory]:
    """Write reference FX rows for a test, and take exactly those out again.

    ``fx_rates`` is in ``_SEEDED_TABLES``: migration 0015 fills it with a yearly
    snapshot and the autouse truncation deliberately leaves the table alone. So
    a test adding rows must remove its own, or it silently changes every later
    test's conversions. Only the ids inserted here are deleted; the seeded
    snapshot is never touched, and a collision with a seeded ``(currency,
    as_of)`` raises on insert rather than overwriting it.

    **Direction.** ``rate_to_base`` is the value of ONE UNIT of ``currency`` in
    USD (``library.fx``: base = USD, USD itself is 1.0 in code and not stored).
    So ``("2026-04-01", "GBP", "1.20")`` means GBP 1 buys USD 1.20, and GBP 100
    converts to USD 120. ``test_the_fixture_writes_usd_per_unit_not_the_inverse``
    pins that so a flipped fixture cannot quietly make a conversion test pass.
    """
    inserted: list[int] = []

    async def _add(rows: Sequence[tuple[str | date, str, str | Decimal]]) -> None:
        for as_of, currency, rate in rows:
            row = FxRate(
                currency=currency,
                as_of=date.fromisoformat(as_of) if isinstance(as_of, str) else as_of,
                rate_to_base=Decimal(rate),
            )
            session.add(row)
            await session.flush()
            inserted.append(row.id)
        await session.commit()

    yield _add
    if inserted:
        # Unconditional: a test body that left the session in a failed
        # transaction would make the DELETE raise `PendingRollbackError`, and
        # because `fx_rates` is never truncated the inserted rates would then
        # survive into every later test in the run.
        await session.rollback()
        await session.execute(delete(FxRate).where(FxRate.id.in_(inserted)))
        await session.commit()


#: Invented vendors for the `seeded` corpus. Distinct senders are what make
#: `split="sender"` a non-trivial axis; none of these names is real.
SEEDED_SENDERS: tuple[str, str, str] = (
    "Cygnus Test Software",
    "Draco Test Services",
    "Eridanus Test Supplies",
)


@pytest.fixture
async def seeded(document: DocumentFactory) -> Sequence[Document]:
    """A small corpus that varies across `category`, `scope` AND `sender`.

    Shaped for the split-invariance property (spec §9.2), which is asserted by
    comparing series to each other rather than against a literal — so the
    fixture has to be able to *break* that comparison if the split were applied
    as a filter. It therefore contains, deliberately:

    * a document unlabelled for every facet and with no sender, which a
      filtering split would drop from all three axes;
    * a document labelled for `category` but not `scope`, which a filtering
      split would drop from one axis only — so the three axes disagree rather
      than all being wrong together;
    * a refund, which must lower a bucket rather than vanish;
    * a non-contributing kind (a coverage ceiling), large enough that its
      accidental inclusion is unmissable;
    * two months, so the time axis has more than one bucket.

    Everything is EUR, so a chart drawn in EUR converts one-to-one and the
    invariance under test is not entangled with FX.
    """
    software, services, supplies = SEEDED_SENDERS
    return [
        await document(
            amount_total=Decimal("120.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 3, 4),
            labels={"category": "software", "scope": "business"},
            sender=software,
        ),
        await document(
            amount_total=Decimal("60.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 3, 20),
            labels={"category": "supplies", "scope": "personal"},
            sender=supplies,
        ),
        await document(
            amount_total=Decimal("80.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 4, 2),
            labels={"category": "services", "scope": "personal"},
            sender=services,
        ),
        await document(
            amount_total=Decimal("45.50"),
            amount_kind=AmountKind.PAYMENT_DUE,
            document_date=date(2026, 4, 10),
            # Labelled for `category` only: unlabelled for `scope`.
            labels={"category": "software"},
            sender=software,
        ),
        await document(
            amount_total=Decimal("15.00"),
            amount_kind=AmountKind.REFUND,
            document_date=date(2026, 4, 20),
            labels={"category": "services", "scope": "personal"},
            sender=services,
        ),
        # Unlabelled entirely, and no sender: NULL on all three axes.
        await document(
            amount_total=Decimal("30.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 4, 18),
        ),
        # Never summed, whatever the split.
        await document(
            amount_total=Decimal("9000.00"),
            amount_kind=AmountKind.COVERAGE_LIMIT,
            document_date=date(2026, 4, 22),
            labels={"category": "accountancy", "scope": "business"},
            sender=services,
        ),
    ]
