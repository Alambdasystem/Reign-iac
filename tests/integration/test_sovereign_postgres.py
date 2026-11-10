"""Disposable PostgreSQL end-to-end tests for the Sovereign schema.

Requires REIGN_TEST_DATABASE_URL pointing to localhost/reign_ci. Never uses
production or shared databases.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit
import unittest

import asyncpg

ROOT = Path(__file__).resolve().parents[2]

class SovereignPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("REIGN_TEST_DATABASE_URL", "")
        url = urlsplit(dsn)
        if url.hostname not in {"127.0.0.1", "localhost"} or url.path != "/reign_ci":
            raise RuntimeError("Sovereign integration tests require disposable local reign_ci")
        self.db = await asyncpg.connect(dsn)
        await self.db.execute("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sovereign_ci_app') THEN
                CREATE ROLE sovereign_ci_app NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
              END IF;
            END $$;
            DROP SCHEMA IF EXISTS reign CASCADE;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        """)
        for _ in range(2):
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                await self.db.execute(path.read_text())
        await self.db.execute("""
            GRANT USAGE ON SCHEMA reign TO sovereign_ci_app;
            GRANT SELECT,INSERT,UPDATE ON reign.sovereign_agents TO sovereign_ci_app;
        """)

    async def asyncTearDown(self):
        if hasattr(self, "db"):
            await self.db.close()

    async def test_persistent_registration_and_rls_isolation(self):
        async with self.db.transaction():
            await self.db.execute("SET LOCAL ROLE sovereign_ci_app")
            await self.db.execute("SELECT set_config('sovereign.tenant_id','tenant-a',true)")
            await self.db.execute("SELECT set_config('sovereign.owner_id','owner-a',true)")
            await self.db.execute("""
                INSERT INTO reign.sovereign_agents
                (tenant_id,agent_id,owner_id,display_name,harness,provider,constitution_version,capabilities)
                VALUES ('tenant-a','reign','owner-a','REIGN','langgraph','render','v1',ARRAY['web.read'])
            """)
            self.assertEqual(await self.db.fetchval(
                "SELECT count(*) FROM reign.sovereign_agents"),1)
            await self.db.execute("SELECT set_config('sovereign.owner_id','owner-b',true)")
            self.assertEqual(await self.db.fetchval(
                "SELECT count(*) FROM reign.sovereign_agents"),0)
        async with self.db.transaction():
            await self.db.execute("SET LOCAL ROLE sovereign_ci_app")
            with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                await self.db.execute("CREATE TABLE reign.escape_hatch(x int)")
        async with self.db.transaction():
            await self.db.execute("SET LOCAL ROLE sovereign_ci_app")
            await self.db.execute("SELECT set_config('sovereign.tenant_id','tenant-b',true)")
            await self.db.execute("SELECT set_config('sovereign.owner_id','owner-a',true)")
            self.assertEqual(await self.db.fetchval(
                "SELECT count(*) FROM reign.sovereign_agents"),0)
        async with self.db.transaction():
            await self.db.execute("SET LOCAL ROLE sovereign_ci_app")
            await self.db.execute("SELECT set_config('sovereign.tenant_id','tenant-a',true)")
            await self.db.execute("SELECT set_config('sovereign.owner_id','owner-a',true)")
            self.assertEqual(await self.db.fetchval(
                "SELECT count(*) FROM reign.sovereign_agents"),1)
            self.assertEqual(await self.db.execute("""
                UPDATE reign.sovereign_agents
                SET lifecycle='active',revision=revision+1
                WHERE tenant_id='tenant-a' AND agent_id='reign'
                AND owner_id='owner-a' AND revision=1
            """),"UPDATE 1")
            self.assertEqual(await self.db.execute("""
                UPDATE reign.sovereign_agents
                SET lifecycle='suspended',revision=revision+1
                WHERE tenant_id='tenant-a' AND agent_id='reign'
                AND owner_id='owner-a' AND revision=1
            """),"UPDATE 0")

    async def test_parent_must_stay_in_same_tenant_and_identifiers_valid(self):
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.db.execute("""
                INSERT INTO reign.sovereign_agents
                (tenant_id,agent_id,owner_id,display_name,harness,provider,constitution_version)
                VALUES ('tenant-a','INVALID ID','owner-a','Bad','langgraph','render','v1')
            """)
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self.db.execute("""
                INSERT INTO reign.sovereign_agents
                (tenant_id,agent_id,owner_id,display_name,harness,provider,constitution_version,parent_id)
                VALUES ('tenant-a','child','owner-a','Child','langgraph','render','v1','other-tenant-root')
            """)

if __name__ == "__main__":
    unittest.main()
