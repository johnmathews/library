"""procrastinate schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10

Applies the Procrastinate job-queue schema using the SQL published by the
installed ``procrastinate`` package (``SchemaManager.get_schema()``), so the
schema always matches the library version in the lockfile. The SQL contains
many statements (tables, types, functions, triggers); asyncpg's prepared
statements cannot run multi-statement strings, so online mode executes it on
the raw asyncpg connection (simple-query protocol).
"""

from collections.abc import Sequence

from alembic import context, op
from procrastinate.schema import SchemaManager
from sqlalchemy.util import await_only

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DROP_PROCRASTINATE_SQL: str = """
DROP TABLE IF EXISTS procrastinate_periodic_defers CASCADE;
DROP TABLE IF EXISTS procrastinate_events CASCADE;
DROP TABLE IF EXISTS procrastinate_jobs CASCADE;
DROP TABLE IF EXISTS procrastinate_workers CASCADE;

DO $drop_functions$
DECLARE
    fn record;
BEGIN
    FOR fn IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate\\_%'
    LOOP
        EXECUTE format('DROP FUNCTION %s CASCADE', fn.signature);
    END LOOP;
END
$drop_functions$;

DROP TYPE IF EXISTS procrastinate_job_to_defer_v1 CASCADE;
DROP TYPE IF EXISTS procrastinate_job_event_type CASCADE;
DROP TYPE IF EXISTS procrastinate_job_status CASCADE;
"""


def _execute_multistatement(sql: str) -> None:
    """Run a multi-statement SQL string.

    Offline mode: emit the SQL into the script verbatim. Online mode (asyncpg):
    bypass the prepared-statement path via the raw driver connection, which
    uses the simple-query protocol and accepts multiple statements.
    """
    if context.is_offline_mode():
        op.execute(sql)
        return
    driver_connection = op.get_bind().connection.driver_connection
    await_only(driver_connection.execute(sql))


def upgrade() -> None:
    _execute_multistatement(SchemaManager.get_schema())


def downgrade() -> None:
    _execute_multistatement(DROP_PROCRASTINATE_SQL)
