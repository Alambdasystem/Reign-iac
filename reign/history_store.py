"""Owner-scoped encrypted history repository, not wired into production.

The caller must derive owner_id from the authenticated server-side principal.
Connection must use a dedicated least-privilege REIGN role with the reviewed
RLS policies in place. Never expose this class to an arbitrary model tool.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from reign.history_crypto import HistoryCipher, SealedRecord

MAX_HISTORY_PAGE = 50
ROLES = frozenset({"user", "assistant", "tool"})


def _owner(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= 128) or any(c in value for c in "\x00\r\n"):
        raise ValueError("Invalid authenticated history owner")
    return value


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


class HistoryRepository:
    """Parameterized SQL only; owner context is transaction-local."""

    def __init__(self, conn: Any, cipher: HistoryCipher):
        self.conn, self.cipher = conn, cipher

    async def _bind_owner(self, owner_id: str):
        # Only called *inside* a database transaction.
        await self.conn.fetchval("SELECT set_config('reign.actor_id', $1, true)", _owner(owner_id))

    async def create_conversation(self, owner_id: str, thread_id: str) -> None:
        tid = _uuid(thread_id)
        async with self.conn.transaction():
            await self._bind_owner(owner_id)
            result = await self.conn.execute(
                "INSERT INTO reign.conversations(thread_id,owner_id) VALUES ($1,$2)",
                tid, owner_id)
            if result != "INSERT 0 1":
                raise RuntimeError("Conversation insert did not complete")

    async def append_message(self, *, owner_id: str, thread_id: str,
                             ordinal: int, role: str, text: str) -> str:
        if role not in ROLES or type(ordinal) is not int or ordinal < 0:
            raise ValueError("Invalid message metadata")
        if type(text) is not str:
            raise ValueError("Message must be text")
        tid = _uuid(thread_id)
        mid = uuid.uuid4()
        sealed = self.cipher.seal(text.encode("utf-8"), owner_id=_owner(owner_id),
                                  thread_id=str(tid), record_id=str(mid), purpose="message")
        async with self.conn.transaction():
            await self._bind_owner(owner_id)
            await self.conn.execute("""
                INSERT INTO reign.conversation_messages
                  (message_id,thread_id,owner_id,ordinal,role,
                   body_ciphertext,body_nonce,key_version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """, mid, tid, owner_id, ordinal, role,
                sealed.ciphertext, sealed.nonce, sealed.key_version)
            await self.conn.execute(
                "UPDATE reign.conversations SET updated_at=now() WHERE thread_id=$1 AND owner_id=$2",
                tid, owner_id)
        return str(mid)

    async def get_messages(self, *, owner_id: str, thread_id: str,
                           limit: int = MAX_HISTORY_PAGE, after: int = -1) -> list[dict]:
        if type(limit) is not int or not (1 <= limit <= MAX_HISTORY_PAGE):
            raise ValueError("Invalid page limit")
        if type(after) is not int or after < -1:
            raise ValueError("Invalid cursor")
        tid = _uuid(thread_id)
        async with self.conn.transaction():
            await self._bind_owner(owner_id)
            rows = await self.conn.fetch("""
                SELECT message_id, ordinal, role, body_ciphertext, body_nonce, key_version
                FROM reign.conversation_messages
                WHERE thread_id=$1 AND owner_id=$2 AND ordinal>$3
                ORDER BY ordinal LIMIT $4
            """, tid, owner_id, after, limit)
        messages = []
        for row in rows:
            sealed = SealedRecord(bytes(row["body_ciphertext"]), bytes(row["body_nonce"]),
                                  row["key_version"])
            plaintext = self.cipher.open(
                sealed, owner_id=owner_id, thread_id=str(tid),
                record_id=str(row["message_id"]), purpose="message")
            messages.append({"message_id": str(row["message_id"]), "ordinal": row["ordinal"],
                             "role": row["role"], "text": plaintext.decode("utf-8")})
        return messages

    async def list_conversations(self, owner_id: str, *, limit: int = MAX_HISTORY_PAGE) -> list[dict]:
        if type(limit) is not int or not (1 <= limit <= MAX_HISTORY_PAGE):
            raise ValueError("Invalid page limit")
        async with self.conn.transaction():
            await self._bind_owner(owner_id)
            rows = await self.conn.fetch("""
                SELECT thread_id, status, created_at, updated_at
                FROM reign.conversations
                WHERE owner_id=$1 AND archived_at IS NULL
                ORDER BY updated_at DESC, thread_id LIMIT $2
            """, owner_id, limit)
        # No plaintext conversation titles or message previews in this listing.
        return [{"thread_id": str(row["thread_id"]), "status": row["status"],
                 "created_at": row["created_at"].isoformat(),
                 "updated_at": row["updated_at"].isoformat()} for row in rows]
