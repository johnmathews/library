"""Migration round-trip tests against a real ephemeral Postgres 17."""

import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from library.extraction.schema import KIND_SLUGS
from library.models import FTS_EXPRESSION
from tests.conftest import alembic_config, create_database, fetch_all

pytestmark = pytest.mark.integration

EXPECTED_TABLES: set[str] = {
    "users",
    "sessions",
    "api_tokens",
    "kinds",
    "senders",
    "tags",
    "document_tags",
    "documents",
    "document_chunks",
    "document_pages",
    "ingestion_events",
    "note_versions",
    "ask_threads",
    "ask_turns",
    "projects",
    "document_projects",
}

PROCRASTINATE_TABLES: set[str] = {
    "procrastinate_jobs",
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
}

EXPECTED_KIND_SLUGS: set[str] = {
    "invoice",
    "receipt",
    "certificate",
    "utility-bill",
    "parking-ticket",
    "warranty",
    "manual",
    "reference",
    "research",
    "note",
    "letter",
    "contract",
    "ticket",
    "other",
    "quote",
}


async def _fetch_scalars(database_url: str, query: str) -> list[object]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query))
            return list(result.scalars())
    finally:
        await engine.dispose()


async def _execute(database_url: str, statements: list[str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


def _table_names(database_url: str) -> set[str]:
    rows = asyncio.run(
        _fetch_scalars(
            database_url,
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()",
        )
    )
    return {str(row) for row in rows}


def test_upgrade_downgrade_upgrade_cycle(admin_database_url: str) -> None:
    """upgrade head -> downgrade base -> upgrade head runs clean on an empty database."""
    url = create_database(admin_database_url, "library_migrations")
    config = alembic_config(url)

    command.upgrade(config, "head")
    tables = _table_names(url)
    assert tables >= EXPECTED_TABLES
    assert tables >= PROCRASTINATE_TABLES

    command.downgrade(config, "base")
    leftover = _table_names(url) - {"alembic_version"}
    assert leftover == set(), f"downgrade base left tables behind: {leftover}"

    command.upgrade(config, "head")
    assert _table_names(url) >= EXPECTED_TABLES | PROCRASTINATE_TABLES


def test_kinds_seeded(migrated_database_url: str) -> None:
    slugs = asyncio.run(_fetch_scalars(migrated_database_url, "SELECT slug FROM kinds"))
    assert len(slugs) == 15
    assert {str(slug) for slug in slugs} == EXPECTED_KIND_SLUGS


def test_extraction_enum_covers_seeded_kinds(migrated_database_url: str) -> None:
    """The extractor's vocabulary must equal what the migrations actually seed.

    A kind seeded by a migration but missing from ``KIND_SLUGS`` is unreachable:
    the classification prompt never mentions it and the structured-output
    Literal would reject it (this is how ``quote`` went unclassifiable for a
    month). ``POST /api/kinds`` lets users add arbitrary kinds at runtime, so
    the assertion is against a freshly migrated database — the seeded set.
    """
    slugs = asyncio.run(_fetch_scalars(migrated_database_url, "SELECT slug FROM kinds"))
    assert {str(slug) for slug in slugs} == EXPECTED_KIND_SLUGS
    assert set(KIND_SLUGS) == EXPECTED_KIND_SLUGS


def test_new_kinds_seeded(migrated_database_url: str) -> None:
    slugs = asyncio.run(_fetch_scalars(migrated_database_url, "SELECT slug FROM kinds"))
    assert {"reference", "research", "note"} <= {str(slug) for slug in slugs}


def test_fts_indexes_exist(migrated_database_url: str) -> None:
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'documents'",
        )
    )
    names = {str(name) for name in indexes}
    assert "ix_documents_search_vector_nl" in names
    assert "ix_documents_search_vector_en" in names


def test_vector_extension_enabled(migrated_database_url: str) -> None:
    extensions = asyncio.run(
        _fetch_scalars(migrated_database_url, "SELECT extname FROM pg_extension")
    )
    assert "vector" in {str(name) for name in extensions}


# --- Accent folding (#138 / migration 0039) ----------------------------------


def test_unaccent_extension_and_immutable_wrapper_exist(migrated_database_url: str) -> None:
    """0039 installs the extension itself rather than leaving it a manual host
    step: `unaccent` is TRUSTED from PG13 on and the application role owns the
    database, so no superuser is involved. If that ever stops being true this
    fails at migration time rather than as a mysterious search bug."""
    extensions = asyncio.run(
        _fetch_scalars(migrated_database_url, "SELECT extname FROM pg_extension")
    )
    assert "unaccent" in {str(name) for name in extensions}

    functions = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = 'immutable_unaccent'",
        )
    )
    assert [str(name) for name in functions] == ["immutable_unaccent"]


def test_immutable_unaccent_is_actually_immutable(migrated_database_url: str) -> None:
    """The whole reason the wrapper exists. `unaccent()`'s one-argument form is
    STABLE (it looks the dictionary up at runtime), and Postgres refuses a
    STABLE function in a generated column or an index. Declaring the wrapper
    IMMUTABLE is what makes 0039's generated columns legal — so assert the
    declared volatility rather than trusting that the migration said so."""
    volatility = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT provolatile::text FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = 'immutable_unaccent'",
        )
    )
    assert [str(v) for v in volatility] == ["i"]  # i = immutable


def test_fts_expression_matches_the_generated_columns(migrated_database_url: str) -> None:
    """`library.models.FTS_EXPRESSION` and the migrations are two copies of one
    expression, by necessity — a migration must be frozen at the schema it
    shipped, so it cannot import the constant.

    This closes that gap without a code-to-code comparison that could fail
    open: it reads the definition back out of the LIVE database and compares it
    to the constant the ORM believes in. If a future migration changes one and
    not the other, this goes red.
    """
    rows = fetch_all(
        migrated_database_url,
        "SELECT a.attname, pg_get_expr(d.adbin, d.adrelid) "
        "FROM pg_attrdef d "
        "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
        "WHERE d.adrelid = 'documents'::regclass "
        "AND a.attname IN ('search_vector_nl', 'search_vector_en')",
    )
    found = {str(name): str(expr) for name, expr in rows}
    assert set(found) == {"search_vector_nl", "search_vector_en"}

    for column, config in (("search_vector_nl", "dutch"), ("search_vector_en", "english")):
        rendered = FTS_EXPRESSION.format(config=config)
        # Postgres normalises whitespace and adds its own casts/parens when it
        # stores an expression, so compare on the load-bearing tokens rather
        # than the exact string: the fold must be present, applied to the
        # concatenated source, under the right config.
        expr = found[column]
        assert "immutable_unaccent" in expr, f"{column} is not accent-folded: {expr}"
        assert config in expr
        for source_column in ("title", "summary", "pages_markdown", "ocr_text", "topics"):
            assert source_column in expr, f"{column} lost {source_column}: {expr}"
            assert source_column in rendered


def test_document_chunks_indexes_exist(migrated_database_url: str) -> None:
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'document_chunks'",
        )
    )
    names = {str(name) for name in indexes}
    assert "ix_document_chunks_embedding" in names
    assert "ix_document_chunks_document_id" in names


def test_users_have_preferences_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'preferences'
        """,
    )
    assert rows == [("preferences", "jsonb", "NO")]


def test_document_chunks_have_page_number_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'page_number'
        """,
    )
    assert rows == [("page_number", "integer", "YES")]


def test_documents_have_pages_markdown_column(migrated_database_url: str) -> None:
    """0025 adds a nullable documents.pages_markdown text column."""
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'pages_markdown'
        """,
    )
    assert rows == [("pages_markdown", "text", "YES")]


def test_0025_backfills_pages_markdown_and_fts_from_existing_pages(
    admin_database_url: str,
) -> None:
    """The 0025 backfill fills pages_markdown (and regenerates the FTS vector)
    for rows that already had document_pages BEFORE the migration ran.

    Fresh-DB schema tests migrate an empty database, so the ``string_agg``
    backfill touches zero rows — they cover the destination, not the journey.
    This migrates to 0024, inserts pre-0025-shaped data (pages + thin OCR, no
    mirror column), upgrades to head, and asserts the backfill populated the
    mirror and the search vector.
    """
    url = create_database(admin_database_url, "library_backfill")
    config = alembic_config(url)

    command.upgrade(config, "0024")
    asyncio.run(
        _execute(
            url,
            [
                # Doc 901: thin OCR letterhead + two out-of-order markdown pages.
                "INSERT INTO documents (id, sha256, mime_type, status, source, language, ocr_text) "
                "VALUES (901, 'sha-with-pages', 'application/pdf', 'indexed', 'upload', 'eng', "
                "'ACME Corporation letterhead')",
                "INSERT INTO document_pages (document_id, page_number, markdown, char_count) "
                "VALUES (901, 2, 'Line item widget zorptastic-5501', 99)",
                "INSERT INTO document_pages (document_id, page_number, markdown, char_count) "
                "VALUES (901, 1, 'Invoice header', 99)",
                # Doc 902: OCR body, no pages — the fallback path.
                "INSERT INTO documents (id, sha256, mime_type, status, source, language, ocr_text) "
                "VALUES (902, 'sha-no-pages', 'application/pdf', 'indexed', 'upload', 'eng', "
                "'plain ocr body quixolate-4420')",
            ],
        )
    )

    command.upgrade(config, "head")  # applies 0025 + backfill

    # Doc 901: pages concatenated in page order into the mirror column.
    assert fetch_all(url, "SELECT pages_markdown FROM documents WHERE id = 901") == [
        ("Invoice header\n\nLine item widget zorptastic-5501",)
    ]
    # Doc 902: no pages -> mirror stays NULL (FTS falls back to ocr_text).
    assert fetch_all(url, "SELECT pages_markdown FROM documents WHERE id = 902") == [(None,)]

    # The regenerated FTS vector indexes the backfilled page body (901)...
    from_pages = asyncio.run(
        _fetch_scalars(
            url,
            "SELECT id FROM documents "
            "WHERE search_vector_en @@ websearch_to_tsquery('english', 'zorptastic-5501')",
        )
    )
    assert [int(row) for row in from_pages] == [901]
    # ...and still matches the OCR fallback body (902).
    from_ocr = asyncio.run(
        _fetch_scalars(
            url,
            "SELECT id FROM documents "
            "WHERE search_vector_en @@ websearch_to_tsquery('english', 'quixolate-4420')",
        )
    )
    assert [int(row) for row in from_ocr] == [902]


def test_documents_have_topics_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'topics'
        """,
    )
    assert rows == [("topics", "jsonb", "YES")]


def test_ask_turns_has_messages_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'ask_turns' AND column_name = 'messages'
        """,
    )
    assert rows == [("messages", "jsonb", "NO")]


def test_projects_slug_unique_and_archived_at_nullable(migrated_database_url: str) -> None:
    archived = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'projects' AND column_name = 'archived_at'
        """,
    )
    assert archived == [("archived_at", "timestamp with time zone", "YES")]

    unique = fetch_all(
        migrated_database_url,
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'projects' AND con.contype = 'u'
        """,
    )
    assert ("uq_projects_slug",) in unique


def test_document_projects_project_id_index_exists(migrated_database_url: str) -> None:
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'document_projects'",
        )
    )
    assert "ix_document_projects_project_id" in {str(name) for name in indexes}


def test_documents_source_check_allows_note(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'documents' AND con.conname = 'ck_documents_document_source'
        """,
    )
    assert rows, "documents source CHECK constraint is missing"
    assert "'note'" in rows[0][0]


def test_ask_logs_table_is_gone(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        "SELECT tablename FROM pg_tables WHERE tablename = 'ask_logs'",
    )
    assert rows == []


#: The legacy series stack's tables, dropped by 0038. Children first — the order
#: `0038._DROP_ORDER` uses, and the order its `downgrade` reverses.
SERIES_STACK_TABLES: tuple[str, ...] = (
    "authored_series_members",
    "authored_series_suggestions",
    "authored_series_exclusions",
    "authored_series",
    "series_membership_overrides",
    "series_meta_overrides",
    "series_insights",
)


def test_series_stack_tables_are_dropped(migrated_database_url: str) -> None:
    """0038 drops all seven legacy series tables.

    This replaced a guard that asserted `series_membership_overrides` *existed*
    (0015's NULLS-NOT-DISTINCT unique). That guard was kept deliberately through
    the code-deletion PR so the drop could not leak forward early; it inverts
    here, at the migration that actually drops them.
    """
    tables = _table_names(migrated_database_url)
    survivors = sorted(set(SERIES_STACK_TABLES) & tables)
    assert survivors == [], f"series tables survived 0038: {survivors}"
    # Not a vacuous pass: a migrated database is still a populated one.
    assert {"documents", "charts", "spend_lines"} <= tables


#: Everything about the seven tables that the 0037 schema fixes and a rewritten
#: `create_table` can silently get wrong: column types and nullability, server
#: defaults, every constraint's rendered definition (so `NULLS NOT DISTINCT`,
#: `ON DELETE` actions and CHECK bodies are all in scope), and every index.
_SERIES_STACK_SCHEMA_QUERIES: dict[str, str] = {
    # `ordinal_position` is **selected**, not just ordered by: without it in the
    # row, two tables holding the same columns in a different order compare
    # equal, and a reordered `create_table` slips through. `udt_name` and
    # `collation_name` cost nothing and close the same class of gap (a `char(3)`
    # rebuilt as `varchar(3)` shares a `data_type`; a column rebuilt under a
    # different collation reorders text silently).
    "columns": """
        SELECT table_name, ordinal_position, column_name, data_type, udt_name,
               is_nullable, column_default, character_maximum_length,
               numeric_precision, numeric_scale, collation_name
        FROM information_schema.columns
        WHERE table_name = ANY(:tables)
        ORDER BY table_name, ordinal_position
    """,
    "constraints": """
        SELECT rel.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = ANY(:tables)
        ORDER BY 1, 2
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE tablename = ANY(:tables)
        ORDER BY 1, 2
    """,
}


def _series_stack_schema(database_url: str) -> dict[str, list[tuple[object, ...]]]:
    return {
        key: fetch_all(database_url, query, tables=list(SERIES_STACK_TABLES))
        for key, query in _SERIES_STACK_SCHEMA_QUERIES.items()
    }


def test_0038_downgrade_restores_the_seven_tables_empty(admin_database_url: str) -> None:
    """`downgrade` to 0037 restores schema, never rows — and `upgrade` re-drops.

    The point of the reverse is that an older image can boot. What it must not
    do is bring the data back: those rows are gone for good, and only a database
    backup returns them. Schema *fidelity* is
    `test_0038_downgrade_restores_the_exact_0037_schema` below; this is the
    round-trip and the emptiness.
    """
    url = create_database(admin_database_url, "library_drop_series")
    config = alembic_config(url)

    command.upgrade(config, "head")
    assert set(SERIES_STACK_TABLES) & _table_names(url) == set()

    command.downgrade(config, "0037")
    missing = sorted(set(SERIES_STACK_TABLES) - _table_names(url))
    assert missing == [], f"downgrade did not restore: {missing}"

    for table in SERIES_STACK_TABLES:
        assert fetch_all(url, f"SELECT count(*) FROM {table}")[0][0] == 0, (
            f"{table} came back non-empty — downgrade restores schema, not rows"
        )

    command.upgrade(config, "head")
    assert set(SERIES_STACK_TABLES) & _table_names(url) == set()


def test_0038_downgrade_restores_the_exact_0037_schema(admin_database_url: str) -> None:
    """The recreated tables must equal the ones 0038 dropped, not resemble them.

    0038's `downgrade` is a hand-written mirror of six migrations' `create_table`
    calls (0009, 0015, 0018, 0019, 0021, 0029) — the kind of transcription where
    a lost `postgresql_nulls_not_distinct`, a forgotten `ON DELETE CASCADE`, one
    of the three columns 0029 added later, or 0021's state CHECK all vanish
    silently and only surface when an older image runs against the result. So
    rather than assert the handful of properties one remembers to name, this
    migrates a second database to 0037 directly and diffs the two schemas.

    Both sides come from the migrations themselves, so neither can drift into
    agreement: the left is the schema 0038 actually removed.
    """
    original = create_database(admin_database_url, "library_series_at_0037")
    command.upgrade(alembic_config(original), "0037")

    roundtrip = create_database(admin_database_url, "library_series_roundtrip")
    config = alembic_config(roundtrip)
    command.upgrade(config, "0038")
    command.downgrade(config, "0037")

    before, after = _series_stack_schema(original), _series_stack_schema(roundtrip)
    for key in _SERIES_STACK_SCHEMA_QUERIES:
        assert before[key], f"no {key} found at 0037 — the diff would pass vacuously"
        assert after[key] == before[key], (
            f"{key} differ after the downgrade\n"
            f"lost: {[row for row in before[key] if row not in after[key]]}\n"
            f"gained: {[row for row in after[key] if row not in before[key]]}"
        )


def test_recipients_table_and_john_seeded(migrated_database_url: str) -> None:
    """0016 adds the recipients lookup table and seeds a "John" row."""
    cols = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'recipients'
        ORDER BY column_name
        """,
    )
    names = {name for name, _ in cols}
    assert {"id", "name", "created_at"} <= names
    unique = fetch_all(
        migrated_database_url,
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'recipients' AND con.contype = 'u'
        """,
    )
    assert ("uq_recipients_name",) in unique
    recipient_names = {
        str(name)
        for name in asyncio.run(
            _fetch_scalars(migrated_database_url, "SELECT name FROM recipients")
        )
    }
    assert "John" in recipient_names


def test_documents_have_recipient_id_column_and_index(migrated_database_url: str) -> None:
    """0016 adds a nullable documents.recipient_id FK plus its index."""
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'recipient_id'
        """,
    )
    assert rows == [("recipient_id", "integer", "YES")]
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'documents'",
        )
    )
    assert "ix_documents_recipient_id" in {str(name) for name in indexes}


def test_email_selection_traces_table_exists(migrated_database_url: str) -> None:
    """0027 adds the durable per-email skip-audit table plus its created_at index."""
    cols = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'email_selection_traces'
        ORDER BY column_name
        """,
    )
    by_name = {name: (data_type, nullable) for name, data_type, nullable in cols}
    assert by_name == {
        "id": ("bigint", "NO"),
        "message_id": ("text", "YES"),
        "subject": ("text", "YES"),
        "from_address": ("text", "YES"),
        "decisions": ("jsonb", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    indexes = asyncio.run(
        _fetch_scalars(
            migrated_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'email_selection_traces'",
        )
    )
    assert "ix_email_selection_traces_created_at" in {str(name) for name in indexes}


def test_fx_rates_table_seeded(migrated_database_url: str) -> None:
    """0015 adds fx_rates and seeds a researched historical snapshot."""
    cols = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'fx_rates'
        ORDER BY column_name
        """,
    )
    names = {name for name, _ in cols}
    assert {"id", "currency", "as_of", "rate_to_base"} <= names
    count = fetch_all(migrated_database_url, "SELECT count(*) FROM fx_rates")[0][0]
    assert count > 0
    currencies = {
        row[0] for row in fetch_all(migrated_database_url, "SELECT DISTINCT currency FROM fx_rates")
    }
    assert {"EUR", "GBP"} <= currencies


def test_check_constraint_names_match_the_naming_convention(
    migrated_database_url: str,
) -> None:
    """`sa.CheckConstraint(name=...)` is substituted *into* the "ck" naming
    convention's `%(constraint_name)s` token, not used verbatim: passing an
    already-prefixed name (`ck_charts_default_grain`) doubles the prefix in
    the live database (`ck_charts_ck_charts_default_grain`). 0035 and 0036
    pass the convention-relative suffix instead — this pins the actual
    database-side names so a future CHECK doesn't repeat the doubling."""
    checks = fetch_all(
        migrated_database_url,
        """
        SELECT rel.relname, con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE con.contype = 'c'
          AND rel.relname IN ('charts', 'spend_lines')
        """,
    )
    by_table = dict(checks)
    assert by_table["charts"] == "ck_charts_default_grain"
    assert by_table["spend_lines"] == "ck_spend_lines_origin"
