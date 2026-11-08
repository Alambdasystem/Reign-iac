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
    """Verify role permissions without creating or changing any tables."""
    async with connect_reign_database() as gateway:
        row = await gateway.connection.fetchrow(
            "SELECT has_schema_privilege(current_user, 'reign', 'USAGE') AS allowed"
        )
        return bool(row and row["allowed"])
