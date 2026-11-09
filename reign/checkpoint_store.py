"""Encrypted, owner-scoped checkpoint persistence for a future REIGN LangGraph saver.

This repository stores opaque serialized checkpoint bytes. Only a separately
reviewed, authenticated checkpoint serializer may produce/consume those bytes.
It does not replay a checkpoint or automatically run browser actions. No Core
endpoint or production database access is enabled by this module.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from reign.history_crypto import HistoryCipher, SealedRecord
from reign.history_store import _owner

MAX_CHECKPOINT_ID = 160
MAX_CHECKPOINT_PAGE = 50


def _checkpoint_id(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= MAX_CHECKPOINT_ID):
        raise ValueError("Invalid checkpoint ID")
    if not all(ch.isascii() and (ch.isalnum() or ch in "-_.:") for ch in value):
        raise ValueError("Invalid checkpoint ID")
    return value


@dataclass(frozen=True)
class StoredCheckpoint:
    checkpoint_id: str
    parent_checkpoint_id: str | None
    payload: bytes
    created_at: str


class CheckpointRepository:
    """Never trust a model-supplied owner; bind an authenticated server principal.

    Callers must use a dedicated restricted connection, encrypted history key,
    and the reviewed forced-RLS migration. SQL is parameterized and the owner
    GUC is set only within a transaction, never persisted on pooled connections.
    """
    def __init__(self, conn: Any, cipher: HistoryCipher):
        self.conn = conn
        self.cipher = cipher

    async def save(self, *, owner_id: str, thread_id: str, checkpoint_id: str,
                   payload: bytes, parent_checkpoint_id: str | None = None) -> None:
        owner = _owner(owner_id)
        tid = uuid.UUID(str(thread_id))
        cid = _checkpoint_id(checkpoint_id)
        parent = _checkpoint_id(parent_checkpoint_id) if parent_checkpoint_id is not None else None
        if parent == cid:
            raise ValueError("Checkpoint cannot be its own parent")
        sealed = self.cipher.seal(payload, owner_id=owner, thread_id=str(tid),
                                  record_id=cid, purpose="checkpoint")
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id',$1,true)", owner)
            # Parent must exist in the same owner's thread before writing a
            # child. Avoid dangling parent references and cross-thread restore.
            if parent is not None:
                exists = await self.conn.fetchval("""
                    SELECT 1 FROM reign.graph_checkpoints
                    WHERE thread_id=$1 AND owner_id=$2 AND checkpoint_id=$3
                """, tid, owner, parent)
                if not exists:
                    raise ValueError("Unknown checkpoint parent")
            # A checkpoint is immutable. Never overwrite state after an
            # uncertain browser action or replay a stale checkpoint.
            await self.conn.execute("""
                INSERT INTO reign.graph_checkpoints
                  (thread_id,checkpoint_id,owner_id,parent_checkpoint_id,
                   state_ciphertext,state_nonce,key_version)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
            """, tid, cid, owner, parent, sealed.ciphertext, sealed.nonce,
                 sealed.key_version)

    async def load(self, *, owner_id: str, thread_id: str,
                   checkpoint_id: str | None = None) -> StoredCheckpoint | None:
        owner = _owner(owner_id)
        tid = uuid.UUID(str(thread_id))
        cid = _checkpoint_id(checkpoint_id) if checkpoint_id is not None else None
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id',$1,true)", owner)
            if cid is None:
                row = await self.conn.fetchrow("""
                    SELECT checkpoint_id,parent_checkpoint_id,state_ciphertext,
                           state_nonce,key_version,created_at
                    FROM reign.graph_checkpoints
                    WHERE thread_id=$1 AND owner_id=$2
                    ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1
                """, tid, owner)
            else:
                row = await self.conn.fetchrow("""
                    SELECT checkpoint_id,parent_checkpoint_id,state_ciphertext,
                           state_nonce,key_version,created_at
                    FROM reign.graph_checkpoints
                    WHERE thread_id=$1 AND owner_id=$2 AND checkpoint_id=$3
                """, tid, owner, cid)
        if row is None:
            return None
        sealed = SealedRecord(bytes(row["state_ciphertext"]),
                              bytes(row["state_nonce"]), row["key_version"])
        plaintext = self.cipher.open(sealed, owner_id=owner, thread_id=str(tid),
                                     record_id=row["checkpoint_id"], purpose="checkpoint")
        return StoredCheckpoint(row["checkpoint_id"], row["parent_checkpoint_id"],
                                plaintext, row["created_at"].isoformat())

    async def list_ids(self, *, owner_id: str, thread_id: str,
                       limit: int = MAX_CHECKPOINT_PAGE) -> list[str]:
        if type(limit) is not int or not (1 <= limit <= MAX_CHECKPOINT_PAGE):
            raise ValueError("Invalid checkpoint limit")
        owner = _owner(owner_id)
        tid = uuid.UUID(str(thread_id))
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id',$1,true)", owner)
            rows = await self.conn.fetch("""
                SELECT checkpoint_id FROM reign.graph_checkpoints
                WHERE owner_id=$1 AND thread_id=$2
                ORDER BY created_at DESC, checkpoint_id DESC LIMIT $3
            """, owner, tid, limit)
        return [row["checkpoint_id"] for row in rows]

    async def save_writes(self, *, owner_id: str, thread_id: str,
                          checkpoint_id: str, task_id: str,
                          writes: list[tuple[int, str, bytes]],
                          task_path: str = "") -> None:
        owner = _owner(owner_id)
        tid = uuid.UUID(str(thread_id))
        cid = _checkpoint_id(checkpoint_id)
        task = _checkpoint_id(task_id)
        if type(task_path) is not str or len(task_path) > 256:
            raise ValueError("Invalid task path")
        if type(writes) is not list or not (1 <= len(writes) <= 100):
            raise ValueError("Invalid checkpoint writes batch")
        prepared = []
        used = set()
        for index, channel, value in writes:
            if type(index) is not int or not (-1000 <= index <= 1000):
                raise ValueError("Invalid write index")
            if index in used:
                raise ValueError("Repeated write index")
            used.add(index)
            channel = _checkpoint_id(channel)
            sealed = self.cipher.seal(value, owner_id=owner, thread_id=str(tid),
                record_id=f"{cid}:{task}:{index}", purpose="write")
            prepared.append((tid, cid, owner, task, task_path, index, channel,
                             sealed.ciphertext, sealed.nonce, sealed.key_version))
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id',$1,true)", owner)
            for row in prepared:
                # Immutable pending writes. A replay or inconsistent retry
                # raises a unique violation rather than modifying effects.
                await self.conn.execute("""
                    INSERT INTO reign.checkpoint_writes
                      (thread_id,checkpoint_id,owner_id,task_id,task_path,
                       write_index,channel,value_ciphertext,value_nonce,key_version)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """, *row)

    async def load_writes(self, *, owner_id: str, thread_id: str,
                          checkpoint_id: str) -> list[tuple[str, int, str, bytes]]:
        owner = _owner(owner_id)
        tid = uuid.UUID(str(thread_id))
        cid = _checkpoint_id(checkpoint_id)
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id',$1,true)", owner)
            rows = await self.conn.fetch("""
                SELECT task_id,write_index,channel,value_ciphertext,
                       value_nonce,key_version
                FROM reign.checkpoint_writes
                WHERE owner_id=$1 AND thread_id=$2 AND checkpoint_id=$3
                ORDER BY task_id,write_index
            """, owner, tid, cid)
        result = []
        for row in rows:
            sealed = SealedRecord(bytes(row["value_ciphertext"]),
                                  bytes(row["value_nonce"]), row["key_version"])
            plaintext = self.cipher.open(sealed, owner_id=owner,
                thread_id=str(tid), record_id=f"{cid}:{row['task_id']}:{row['write_index']}",
                purpose="write")
            result.append((row["task_id"], row["write_index"], row["channel"], plaintext))
        return result
