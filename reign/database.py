"""REIGN-only PostgreSQL connection lifecycle.

The application uses a dedicated least-privilege REIGN database role. It never
uses Moby Alpha's application credentials or modifies its schema.
"""
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from reign.data_tools import SessionDataGateway


def database_dsn() -> str:
    value = os.environ.get("REIGN_DATABASE_URL", "")
    if not value:
        raise RuntimeError("REIGN_DATABASE_URL is not configured")
    parts = urlsplit(value)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise RuntimeError("REIGN_DATABASE_URL must be a PostgreSQL connection URI")
    if parts.fragment:
        raise RuntimeError("Database URL fragments are not supported")
    return value


@asynccontextmanager
async def connect_reign_database():
    """Open one connection; callers must close it even when operations fail."""
    import asyncpg

    conn = await asyncpg.connect(database_dsn(), timeout=10, command_timeout=15)
    try:
        yield SessionDataGateway(conn)
    finally:
        await conn.close()


async def probe_reign_database() -> bool:
    """Read-only release gate for tables and effective runtime permissions.

    Schema USAGE alone cannot prove migrations ran or that the login is isolated.
    Reject elevated roles, DDL privileges, missing table privileges and access to
    non-REIGN user tables (including privileges inherited through PUBLIC).
    """
    async with connect_reign_database() as gateway:
        row = await gateway.connection.fetchrow(
            """
            WITH required(name) AS (
              VALUES ('sessions'), ('tasks'), ('computer_profiles'), ('control_leases')
            ), privileges(privilege) AS (
              VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')
            )
            SELECT (
              has_schema_privilege(current_user, 'reign', 'USAGE')
              AND NOT has_schema_privilege(current_user, 'reign', 'CREATE')
              AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
              AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user
                AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
              AND NOT EXISTS (SELECT 1 FROM pg_auth_members m
                JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = current_user)
              AND (SELECT bool_and(COALESCE(
                has_table_privilege(current_user, to_regclass('reign.' || name), privilege), false))
                FROM required CROSS JOIN privileges)
              AND NOT EXISTS (
                SELECT 1 FROM pg_namespace n
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
                  AND has_schema_privilege(current_user, n.oid, 'CREATE'))
              AND NOT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN ('reign', 'pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND has_table_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE,DELETE'))
            ) AS allowed
            """
        )
        return bool(row and row["allowed"])
