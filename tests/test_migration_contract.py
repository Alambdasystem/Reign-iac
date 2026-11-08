"""Migration contract tests; live PostgreSQL integration requires a disposable DB."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class MigrationContractTests(unittest.TestCase):
    def test_migrations_are_isolated_and_additive(self):
        first = (ROOT / "migrations/001_reign_isolated_schema.sql").read_text()
        second = (ROOT / "migrations/002_reign_computer_profiles.sql").read_text()
        for sql in (first, second):
            self.assertIn("CREATE SCHEMA IF NOT EXISTS reign", sql)
            self.assertNotIn("DROP TABLE", sql.upper())
            self.assertNotIn("ALTER ROLE", sql.upper())
            self.assertNotIn("GRANT ALL", sql.upper())
        for name in ("computer_profiles", "control_leases"):
            self.assertIn("CREATE TABLE IF NOT EXISTS reign." + name, second)
        self.assertIn("REFERENCES reign.sessions(session_id)", second)
        self.assertIn("CHECK (epoch >= 0)", second)
        self.assertIn("owner_id text NOT NULL", second)
