import os
import unittest
from unittest.mock import AsyncMock, patch

from reign.database import database_dsn, probe_reign_database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    def test_database_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                database_dsn()

    def test_rejects_non_postgres_urls(self):
        with patch.dict(os.environ, {"REIGN_DATABASE_URL": "https://example.com/db"}):
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                database_dsn()

    def test_accepts_postgres_uri_without_exposing_secret(self):
        value = "postgresql://reign:secret@db.internal:5432/reign"
        with patch.dict(os.environ, {"REIGN_DATABASE_URL": value}):
            self.assertEqual(database_dsn(), value)

    async def test_probe_checks_only_reign_schema_permission(self):
        from contextlib import asynccontextmanager
        from reign import database
        connection = AsyncMock()
        connection.fetchrow.return_value = {"allowed": True}

        @asynccontextmanager
        async def fake_connect():
            from reign.data_tools import SessionDataGateway
            yield SessionDataGateway(connection)

        with patch.object(database, "connect_reign_database", fake_connect):
            self.assertTrue(await probe_reign_database())
        sql = connection.fetchrow.await_args.args[0]
        self.assertIn("has_schema_privilege", sql)
        self.assertIn("'reign'", sql)
        self.assertNotIn("CREATE", sql.upper())


if __name__ == "__main__":
    unittest.main()
