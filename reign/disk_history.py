"""Single-instance encrypted transcript continuity on Core's existing private disk.

This is NOT a durable LangGraph checkpoint saver. Never resume an interrupted
browser action from an archived transcript. The Core-only admin boundary owns
all calls; no model/tool/API may supply a different identity.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from pathlib import Path

from reign.history_crypto import HistoryCipher, SealedRecord

MOUNT = "/var/data/reign-vault"
ARCHIVE_NAME = "reign-history.sqlite3"
OWNER = "pilot-admin"  # one AX authenticated administrator; multi-user unsupported
MAX_TURNS = 12


class HistoryArchive:
    def __init__(self, path: str, cipher: HistoryCipher):
        self.path, self.cipher = path, cipher
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                thread TEXT NOT NULL,
                turn INTEGER NOT NULL CHECK (turn >= 0 AND turn < 12),
                role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                message_id TEXT NOT NULL UNIQUE,
                ciphertext BLOB NOT NULL,
                nonce BLOB NOT NULL,
                key_version INTEGER NOT NULL,
                PRIMARY KEY (thread, turn, role))""")

    @classmethod
    def from_env(cls) -> "HistoryArchive":
        if os.getenv("REIGN_HISTORY_MODE") != "disk":
            raise RuntimeError("Durable transcript history not enabled")
        root = os.environ.get("REIGN_VAULT_DIR")
        if root != MOUNT or os.path.realpath(MOUNT) != MOUNT or not os.path.ismount(MOUNT):
            raise RuntimeError("Verified Core persistent mount required")
        folder = Path(root)
        mode = folder.stat().st_mode
        if not stat.S_ISDIR(mode) or (mode & 0o077):
            raise RuntimeError("Private persistent mount required")
        if not os.access(root, os.W_OK):
            raise RuntimeError("Persistent mount is not writable")
        cipher = HistoryCipher.from_env()  # never reuse vault encryption key
        return cls(str(folder / ARCHIVE_NAME), cipher)

    def _connect(self):
        parent = os.path.dirname(self.path)
        if not os.path.isdir(parent):
            raise RuntimeError("History directory unavailable")
        if os.path.lexists(self.path):
            info = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_mode & 0o077):
                raise RuntimeError("Unsafe history database file")
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            except FileExistsError:
                raise RuntimeError("Concurrent history database initialization") from None
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _thread(thread_id: str) -> str:
        return str(uuid.UUID(thread_id))

    def save_turn(self, thread_id: str, turn: int, user_text: str, assistant_text: str):
        tid = self._thread(thread_id)
        if type(turn) is not int or not 0 <= turn < MAX_TURNS:
            raise ValueError("Invalid turn ordinal")
        if type(user_text) is not str or type(assistant_text) is not str:
            raise ValueError("Invalid transcript content")
        values = []
        for role, content in (("user", user_text), ("assistant", assistant_text)):
            mid = str(uuid.uuid4())
            sealed = self.cipher.seal(content.encode("utf-8"), owner_id=OWNER,
                thread_id=tid, record_id=mid, purpose="message")
            values.append((tid, turn, role, mid, sealed.ciphertext,
                           sealed.nonce, sealed.key_version))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in values:
                db.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?)", row)

    def load_turns(self, thread_id: str) -> list[tuple[str, str]]:
        tid = self._thread(thread_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT turn,role,message_id,ciphertext,nonce,key_version FROM turns "
                "WHERE thread=? ORDER BY turn,CASE role WHEN 'user' THEN 0 ELSE 1 END",
                (tid,)).fetchall()
        turns: list[tuple[str, str]] = []
        for i in range(0, len(rows), 2):
            pair = rows[i:i+2]
            if len(pair) != 2 or pair[0][0] != pair[1][0] or (
                    pair[0][1], pair[1][1]) != ("user", "assistant"):
                raise RuntimeError("Incomplete stored history turn")
            value = []
            for _turn, _role, mid, encrypted, nonce, version in pair:
                plain = self.cipher.open(
                    SealedRecord(bytes(encrypted), bytes(nonce), version),
                    owner_id=OWNER, thread_id=tid, record_id=mid, purpose="message")
                value.append(plain.decode("utf-8"))
            turns.append((value[0], value[1]))
        return turns

    def delete_thread(self, thread_id: str) -> None:
        tid = self._thread(thread_id)
        with self._connect() as db:
            db.execute("DELETE FROM turns WHERE thread=?", (tid,))
