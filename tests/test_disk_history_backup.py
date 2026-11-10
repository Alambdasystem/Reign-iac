"""Offline, synthetic-key encrypted transcript recovery gate.

This proves SQLite backup/restore mechanics only. No Render disk, production key,
backups, operational escrow or authenticated AX restart are accessed here.
"""
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag

from reign.disk_history import HistoryArchive
from reign.history_crypto import HistoryCipher


class OfflineRecoveryTests(unittest.TestCase):
    def test_consistent_encrypted_backup_and_restore_rejects_wrong_key(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp, "original.sqlite3")
            backup = Path(temp, "backup.sqlite3")
            restored = Path(temp, "restored.sqlite3")
            cipher = HistoryCipher(bytes(range(32)))  # synthetic fixture, never production
            thread = str(uuid.uuid4())
            store = HistoryArchive(str(original), cipher)
            store.save_turn(thread, 0, "private sample prompt", "private sample answer")
            # Create the destination at private permissions before SQLite opens it.
            backup_fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(backup_fd)
            with sqlite3.connect(original) as source, sqlite3.connect(backup) as target:
                source.backup(target)  # transactionally consistent snapshot
            self.assertEqual(backup.stat().st_mode & 0o077, 0)
            self.assertNotIn(b"private sample prompt", backup.read_bytes())
            self.assertEqual(
                HistoryArchive(str(backup), cipher).load_turns(thread),
                [("private sample prompt", "private sample answer")])
            # Exercise recovery in a *different* archive path after source loss.
            os.unlink(original)
            recover_fd = os.open(restored, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(recover_fd, "wb") as out:
                out.write(backup.read_bytes())
            self.assertEqual(
                HistoryArchive(str(restored), cipher).load_turns(thread),
                [("private sample prompt", "private sample answer")])
            self.assertEqual(restored.stat().st_mode & 0o077, 0)
            with self.assertRaises(InvalidTag):
                HistoryArchive(
                    str(restored), HistoryCipher(bytes(reversed(range(32))))).load_turns(thread)

    def test_invalid_or_publicly_readable_restore_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            unsafe = Path(temp, "archive.sqlite3")
            unsafe.write_text("not an archive")
            os.chmod(unsafe, 0o644)
            with self.assertRaises(RuntimeError):
                HistoryArchive(str(unsafe), HistoryCipher(bytes(range(32))))
