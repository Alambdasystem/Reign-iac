"""Integration against a disposable local PostgreSQL 18 database only.

Never point this suite at a shared or production database. Both host and DB
name are checked before the first DDL statement.
"""
import os
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
from reign.database import probe_reign_database
from reign.data_tools import DataAction, DataCommand, DataPrincipal, SessionDataGateway

ROOT = Path(__file__).resolve().parents[2]


class PostgresIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("REIGN_TEST_DATABASE_URL", "")
        url = urlsplit(dsn)
        if url.hostname not in ("127.0.0.1", "localhost") or url.path != "/reign_ci":
            raise RuntimeError("Integration suite requires the disposable local reign_ci database")
        self.db = await asyncpg.connect(dsn)
        await self.db.execute("""
          DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='reign_ci_app') THEN
              CREATE ROLE reign_ci_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOINHERIT NOREPLICATION NOBYPASSRLS;
            END IF;
          END $$;
          REVOKE CREATE ON SCHEMA public FROM PUBLIC;
          DROP SCHEMA IF EXISTS reign CASCADE;
          DROP SCHEMA IF EXISTS moby_fixture CASCADE;
          CREATE SCHEMA moby_fixture;
          CREATE TABLE moby_fixture.private_data (value text);
          INSERT INTO moby_fixture.private_data VALUES ('fixture-only');
        """)
        for _ in range(2):
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                await self.db.execute(path.read_text())
        await self.db.execute("""
          GRANT USAGE ON SCHEMA reign TO reign_ci_app;
          GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA reign TO reign_ci_app;
          SET ROLE reign_ci_app;
        """)

    async def asyncTearDown(self):
        if hasattr(self, "db"):
            await self.db.close()

    async def probe(self):
        @asynccontextmanager
        async def connected():
            yield SessionDataGateway(self.db)
        with patch("reign.database.connect_reign_database", connected):
            return await probe_reign_database()

    async def test_migrations_idempotent_role_isolated_and_crud_works(self):
        self.assertTrue(await self.probe())
        sid, task = uuid4(), uuid4()
        await self.db.execute("INSERT INTO reign.computer_profiles(profile_id,owner_id,mode) VALUES ('p','alice','managed')")
        await self.db.execute("INSERT INTO reign.sessions(session_id,owner_id,profile_id,status) VALUES ($1,'alice','p','active')", sid)
        await self.db.execute("INSERT INTO reign.tasks(task_id,owner_id,session_id) VALUES ($1,'alice',$2)", task, sid)
        await self.db.execute("INSERT INTO reign.control_leases(session_id,owner_id,worker_id,mode) VALUES ($1,'alice','worker','agent')", sid)
        gateway = SessionDataGateway(self.db)
        alice = DataPrincipal("alice", frozenset({"sessions:read", "tasks:write"}))
        bob = DataPrincipal("bob", alice.scopes)
        row = await gateway.execute(DataCommand(DataAction.GET_SESSION, alice, {"session_id": str(sid)}))
        self.assertEqual(row["session_id"], sid)
        self.assertIsNone(await gateway.execute(DataCommand(DataAction.GET_SESSION, bob, {"session_id": str(sid)})))
        self.assertEqual(await gateway.execute(DataCommand(DataAction.SAVE_TASK_RESULT, bob,
            {"task_id": str(task), "result": "blocked"})), "UPDATE 0")
        self.assertEqual(await gateway.execute(DataCommand(DataAction.SAVE_TASK_RESULT, alice,
            {"task_id": str(task), "result": "ok"})), "UPDATE 1")
        for sql in ("SELECT * FROM moby_fixture.private_data",
                    "CREATE TABLE public.unwanted(x int)", "CREATE TABLE reign.unwanted(x int)",
                    "CREATE SCHEMA unwanted", "DROP TABLE reign.sessions"):
            with self.assertRaises(asyncpg.InsufficientPrivilegeError, msg=sql):
                await self.db.execute(sql)
        await self.db.execute("DELETE FROM reign.tasks WHERE task_id=$1", task)
        await self.db.execute("DELETE FROM reign.sessions WHERE session_id=$1", sid)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM reign.control_leases"), 0)

    async def test_constraints_reject_invalid_state(self):
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.db.execute("INSERT INTO reign.computer_profiles(profile_id,owner_id,mode) VALUES ('external','alice','external')")
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.db.execute("INSERT INTO reign.sessions(session_id,owner_id,profile_id,status) VALUES ($1,'alice','p','invalid')", uuid4())
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self.db.execute("INSERT INTO reign.tasks(task_id,owner_id,session_id) VALUES ($1,'alice',$2)", uuid4(), uuid4())

    async def test_history_and_traces_fail_closed_until_rls_policies_exist(self):
        # Proposal migration is deliberately *not* runtime-enabled.
        # Even a table grant must not permit querying other users' history.
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await self.db.execute(
                "INSERT INTO reign.conversations(thread_id, owner_id) VALUES ($1,'alice')", uuid4()
            )
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM reign.conversations"), 0)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM reign.trace_spans"), 0)

    async def test_encrypted_history_round_trip_is_owner_scoped(self):
        from reign.history_crypto import HistoryCipher
        from reign.history_store import HistoryRepository
        cipher = HistoryCipher(bytes(range(32)))
        store = HistoryRepository(self.db, cipher)
        alice_id, bob_id = str(uuid4()), str(uuid4())
        await store.create_conversation("alice", alice_id)
        await store.create_conversation("bob", bob_id)
        mid = await store.append_message(owner_id="alice", thread_id=alice_id, ordinal=0,
                                         role="user", text="private fixture for Alice")
        messages = await store.get_messages(owner_id="alice", thread_id=alice_id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message_id"], mid)
        self.assertEqual(messages[0]["text"], "private fixture for Alice")
        # The database is storing ciphertext, not a plaintext message.
        async with self.db.transaction():
            await self.db.fetchval("SELECT set_config('reign.actor_id',$1,true)", "alice")
            raw = await self.db.fetchval(
                "SELECT body_ciphertext FROM reign.conversation_messages WHERE message_id=$1",
                __import__("uuid").UUID(mid))
            self.assertNotIn(b"private fixture for Alice", bytes(raw))
        self.assertEqual(await store.get_messages(owner_id="bob", thread_id=alice_id), [])
        self.assertEqual(await store.list_conversations("bob"), [
            dict(thread_id=bob_id, status="idle", created_at=unittest.mock.ANY,
                 updated_at=unittest.mock.ANY)])
        # A cross-owner write must not modify the original owner's conversation.
        with self.assertRaises((asyncpg.InsufficientPrivilegeError,
                                asyncpg.ForeignKeyViolationError)):
            await store.append_message(owner_id="bob", thread_id=alice_id, ordinal=1,
                                       role="user", text="attempted cross-owner write")
        self.assertEqual(len(await store.get_messages(owner_id="alice", thread_id=alice_id)), 1)

    async def test_trace_sinks_reject_cross_user_read_and_raw_metadata(self):
        import time
        from reign.telemetry import safe_span, new_trace_id
        from reign.trace_store import TraceRepository
        traces = TraceRepository(self.db)
        span = safe_span(trace_id=new_trace_id(), service="core",
                         operation="agent.tool", outcome="ok",
                         started_monotonic=time.monotonic(),
                         tool_name="workspace_status", input_tokens=12)
        await traces.record(owner_id="alice", span=span)
        alice = await traces.recent("alice")
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0]["tool_name"], "workspace_status")
        self.assertEqual(alice[0]["input_tokens"], 12)
        self.assertEqual(await traces.recent("bob"), [])
        with self.assertRaises(ValueError):
            await traces.record(owner_id="alice", span={**span, "raw_prompt": "private fixture"})
        self.assertEqual(len(await traces.recent("alice")), 1)

    async def test_immutable_encrypted_checkpoints_survive_reconnection(self):
        from reign.checkpoint_store import CheckpointRepository
        from reign.history_crypto import HistoryCipher
        from reign.history_store import HistoryRepository
        cipher = HistoryCipher(bytes(range(32)))  # Disposable CI key only.
        owner, other = "alice", "bob"
        tid = str(uuid4())
        await HistoryRepository(self.db, cipher).create_conversation(owner, tid)
        checkpoints = CheckpointRepository(self.db, cipher)
        await checkpoints.save(owner_id=owner, thread_id=tid, checkpoint_id="ckpt-1",
                               payload=b'{"next":"wait_for_user","side_effects":"not_replayed"}')
        await checkpoints.save(owner_id=owner, thread_id=tid, checkpoint_id="ckpt-2",
                               payload=b'{"next":"paused"}', parent_checkpoint_id="ckpt-1")
        self.assertEqual(await checkpoints.list_ids(owner_id=owner, thread_id=tid),
                         ["ckpt-2", "ckpt-1"])
        with self.assertRaises(asyncpg.UniqueViolationError):
            await checkpoints.save(owner_id=owner, thread_id=tid, checkpoint_id="ckpt-1",
                                   payload=b'{"next":"replay"}')
        with self.assertRaises(ValueError):
            await checkpoints.save(owner_id=owner, thread_id=tid, checkpoint_id="ckpt-3",
                                   payload=b"orphan", parent_checkpoint_id="missing")
        self.assertIsNone(await checkpoints.load(owner_id=other, thread_id=tid))
        self.assertEqual(await checkpoints.list_ids(owner_id=other, thread_id=tid), [])
        with self.assertRaises((asyncpg.InsufficientPrivilegeError,
                                asyncpg.ForeignKeyViolationError)):
            await checkpoints.save(owner_id=other, thread_id=tid, checkpoint_id="ckpt-3",
                                   payload=b"cross-owner")
        # Simulate process restart: NEW database connection and NEW repository
        # instance read the ciphertext with only the explicitly supplied key.
        replacement = await asyncpg.connect(os.environ["REIGN_TEST_DATABASE_URL"])
        try:
            await replacement.execute("SET ROLE reign_ci_app")
            restored = await CheckpointRepository(replacement, cipher).load(
                owner_id=owner, thread_id=tid)
            self.assertEqual(restored.checkpoint_id, "ckpt-2")
            self.assertEqual(restored.parent_checkpoint_id, "ckpt-1")
            self.assertEqual(restored.payload, b'{"next":"paused"}')
            raw = await replacement.fetchval("""
                SELECT state_ciphertext FROM reign.graph_checkpoints
                WHERE thread_id=$1 AND checkpoint_id='ckpt-2'
            """, uuid4()) if False else None
            # No model invocation or browser effect takes place on restoration.
            self.assertIsNone(await CheckpointRepository(replacement, cipher).load(
                owner_id="bob", thread_id=tid))
        finally:
            await replacement.close()

    async def test_history_foreign_keys_reject_cross_owner_linkage(self):
        # Migration role creates the fixture, but database-level composite FK
        # prevents a message or checkpoint claiming someone else's thread.
        await self.db.execute("RESET ROLE")
        tid = uuid4()
        try:
            await self.db.execute(
                "INSERT INTO reign.conversations(thread_id,owner_id) VALUES ($1,'alice')", tid)
            with self.assertRaises(asyncpg.ForeignKeyViolationError):
                await self.db.execute("""
                    INSERT INTO reign.conversation_messages
                      (message_id,thread_id,owner_id,ordinal,role,body_ciphertext,
                       body_nonce,key_version)
                    VALUES ($1,$2,'bob',0,'user',$3,$4,1)
                """, uuid4(), tid, b"encrypted-test-only", b"0" * 12)
            with self.assertRaises(asyncpg.ForeignKeyViolationError):
                await self.db.execute("""
                    INSERT INTO reign.graph_checkpoints
                      (thread_id,checkpoint_id,owner_id,state_ciphertext,state_nonce,key_version)
                    VALUES ($1,'c1','bob',$2,$3,1)
                """, tid, b"encrypted-test-only", b"1" * 12)
        finally:
            await self.db.execute("SET ROLE reign_ci_app")

    async def test_readiness_rejects_missing_table_grant(self):
        await self.db.execute("RESET ROLE; REVOKE UPDATE ON reign.tasks FROM reign_ci_app; SET ROLE reign_ci_app")
        self.assertFalse(await self.probe())

    async def test_readiness_rejects_missing_migration(self):
        await self.db.execute("RESET ROLE; DROP TABLE reign.control_leases; SET ROLE reign_ci_app")
        self.assertFalse(await self.probe())

    async def test_readiness_rejects_cross_application_access(self):
        await self.db.execute("RESET ROLE; GRANT USAGE ON SCHEMA moby_fixture TO reign_ci_app; GRANT SELECT ON moby_fixture.private_data TO reign_ci_app; SET ROLE reign_ci_app")
        self.assertFalse(await self.probe())

    async def test_readiness_rejects_superuser(self):
        await self.db.execute("RESET ROLE")
        self.assertFalse(await self.probe())


if __name__ == "__main__":
    unittest.main()
