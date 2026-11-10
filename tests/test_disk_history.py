"""Encrypted disk transcript and actual graph-context reconstruction tests.

No external provider, production mount or authenticated AX UI is used.
"""
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from reign.agent import AgentRuntime
from reign.disk_history import HistoryArchive
from reign.history_crypto import HistoryCipher


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.answers.pop(0)


class Adapter:
    def __init__(self):
        self.calls = 0

    async def status(self):
        self.calls += 1
        return {"status": "ready", "components": {"core": "ready"}}


def tool_status():
    return AIMessage(content="", tool_calls=[{
        "name": "workspace_status", "args": {},
        "id": str(uuid.uuid4()), "type": "tool_call",
    }])


class DiskHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbfile = str(Path(self.temp.name) / "history.sqlite")
        self.cipher = HistoryCipher(bytes(range(32)))
        self.archive = HistoryArchive(self.dbfile, self.cipher)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-only"})
        self.env.start()
        self.runtime = None

    async def asyncTearDown(self):
        if self.runtime:
            await self.runtime.shutdown()
        self.env.stop()
        self.temp.cleanup()

    async def test_encrypted_roundtrip_survives_reopening_and_is_not_plaintext(self):
        tid = str(uuid.uuid4())
        self.archive.save_turn(tid, 0, "sensitive sample prompt", "sample response")
        self.assertEqual(HistoryArchive(self.dbfile, self.cipher).load_turns(tid),
                         [("sensitive sample prompt", "sample response")])
        with open(self.dbfile, "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"sensitive sample prompt", raw)
        self.assertNotIn(b"sample response", raw)
        self.assertEqual(self.archive.list_threads(), [{"thread_id": tid, "turns": 1}])
        self.assertEqual(os.stat(self.dbfile).st_mode & 0o077, 0)

    async def test_no_completed_tool_replay_after_new_runtime_reconstructs_context(self):
        adapter = Adapter()
        first = Model(tool_status(), AIMessage(content="The workspace is ready."))
        save_errors = []
        original_save = self.archive.save_turn
        def instrumented_save(*args, **kwargs):
            try:
                return original_save(*args, **kwargs)
            except Exception as exc:
                save_errors.append((type(exc).__name__, str(exc)))
                raise
        self.archive.save_turn = instrumented_save
        self.runtime = AgentRuntime(lambda: first, adapter, archive=self.archive)
        receipt = self.runtime.submit("Check the workspace")
        thread = self.runtime.get(receipt["thread_id"])
        await thread.task
        self.assertEqual(thread.status, "completed", repr((thread.error, thread.events, self.archive.list_threads(), save_errors, thread.turns)))
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.archive.load_turns(thread.id),
                         [("Check the workspace", "The workspace is ready.")])
        await self.runtime.shutdown()

        # Different runtime and model, same encrypted DB file.
        second = Model(AIMessage(content="We checked workspace status."))
        self.runtime = AgentRuntime(lambda: second, adapter,
                                    archive=HistoryArchive(self.dbfile, self.cipher))
        restored = self.runtime.get(thread.id)
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.snapshot()["memory"], "durable_transcript")
        self.assertEqual(restored.answer, "The workspace is ready.")
        next_turn = self.runtime.submit("What did we do?", thread.id)
        await self.runtime.get(next_turn["thread_id"]).task
        self.assertEqual(adapter.calls, 1)  # Previous tool was not invoked again.
        self.assertTrue(any(isinstance(m, HumanMessage) and
                            m.content == "Check the workspace" for m in second.seen[0]))
        self.assertEqual(len(self.archive.load_turns(thread.id)), 2)

    async def test_duplicate_turn_rejected_and_wrong_key_cannot_read(self):
        tid = str(uuid.uuid4())
        self.archive.save_turn(tid, 0, "first", "done")
        with self.assertRaises(sqlite3.IntegrityError):
            self.archive.save_turn(tid, 0, "duplicate", "bad")
        self.assertEqual(self.archive.load_turns(tid), [("first", "done")])
        from cryptography.exceptions import InvalidTag
        wrong = HistoryArchive(self.dbfile, HistoryCipher(bytes(reversed(range(32)))))
        with self.assertRaises(InvalidTag):
            wrong.load_turns(tid)

    async def test_delete_thread_removes_history(self):
        tid = str(uuid.uuid4())
        self.archive.save_turn(tid, 0, "one", "two")
        self.archive.delete_thread(tid)
        self.assertEqual(self.archive.load_turns(tid), [])
        self.assertEqual(self.archive.list_threads(), [])

    async def test_symlink_history_database_is_rejected(self):
        actual = str(Path(self.temp.name) / "real.sqlite")
        Path(actual).write_bytes(b"fixture")
        link = str(Path(self.temp.name) / "symlink.sqlite")
        os.symlink(actual, link)
        with self.assertRaises(RuntimeError):
            HistoryArchive(link, self.cipher)

    async def test_activation_refuses_missing_verified_disk_or_key(self):
        with patch.dict(os.environ, {"REIGN_HISTORY_MODE": "disk",
                                     "REIGN_VAULT_DIR": self.temp.name,
                                     "REIGN_HISTORY_KEY": ""}):
            with self.assertRaises(RuntimeError):
                HistoryArchive.from_env()
