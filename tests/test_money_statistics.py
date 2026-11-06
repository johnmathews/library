"""``MONEY_TABLES`` must cover every table the money views read."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from library.money.statistics import MONEY_TABLES

pytestmark = pytest.mark.integration

# Base tables reachable from a view through pg_rewrite dependencies, however
# many views deep (spend_facts reads payments, which reads payment_edges).
_BASE_TABLES_OF_VIEW = """
WITH RECURSIVE deps(relid) AS (
  SELECT c.oid FROM pg_class c WHERE c.relname = :view AND c.relkind = 'v'
  UNION
  SELECT d.refobjid
  FROM deps
  JOIN pg_rewrite r ON r.ev_class = deps.relid
  JOIN pg_depend d ON d.objid = r.oid AND d.refobjid <> deps.relid
  WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
)
SELECT c.relname FROM deps JOIN pg_class c ON c.oid = deps.relid
WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace
"""


async def _base_tables(database_url: str, view: str) -> set[str]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text(_BASE_TABLES_OF_VIEW), {"view": view})
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("view", ["payments", "spend_facts"])
def test_money_tables_cover_every_base_table_of_the_money_views(
    migrated_database_url: str, view: str
) -> None:
    base = asyncio.run(_base_tables(migrated_database_url, view))
    assert {"documents", "payment_overrides"} <= base, "the query found no dependencies"
    assert base <= set(MONEY_TABLES), f"{view} reads {sorted(base - set(MONEY_TABLES))}"
