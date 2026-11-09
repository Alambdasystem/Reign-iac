"""Prototype async LangGraph checkpointer backed by encrypted REIGN storage.

NOT wired into production. It must pass LangGraph's conformance suite, a
complete real-graph pause/restart exercise, schema/role review and approval
before replacing Core's InMemorySaver.

Only the trusted Core server may construct this saver with an authenticated
owner. It is never exposed to an LLM as a tool.
"""
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from reign.checkpoint_store import CheckpointRepository
from reign.history_store import _owner


class EncryptedGraphSaver(BaseCheckpointSaver):
    def __init__(self, repository: CheckpointRepository, *, owner_id: str):
        # Explicitly strict even when the process environment would otherwise
        # allow arbitrary msgpack module constructors. Pickle is never allowed.
        super().__init__(serde=JsonPlusSerializer(
            allowed_msgpack_modules=None, allowed_json_modules=None,
            pickle_fallback=False))
        self.repo = repository
        self.owner_id = _owner(owner_id)

    @staticmethod
    def _ids(config: dict, *, require_checkpoint: bool = False) -> tuple[str, str | None]:
        values = config.get("configurable") or {}
        thread = str(uuid.UUID(str(values["thread_id"])))
        if values.get("checkpoint_ns"):
            raise ValueError("Non-default checkpoint namespaces require reviewed schema support")
        checkpoint_id = values.get("checkpoint_id")
        if require_checkpoint and not checkpoint_id:
            raise ValueError("Checkpoint ID is required")
        return thread, str(checkpoint_id) if checkpoint_id else None

    def _pack(self, value: Any) -> bytes:
        type_name, body = self.serde.dumps_typed(value)
        if type_name not in ("msgpack", "json", "bytes", "null"):
            raise ValueError("Unsupported checkpoint serialization type")
        return json.dumps(
            {"type": type_name, "body": base64.b64encode(body).decode("ascii")},
            separators=(",", ":")).encode("ascii")

    def _unpack(self, payload: bytes) -> Any:
        # Stored contents are always untrusted until authenticated AES-GCM
        # decryption has succeeded. Limit type tags and reject unknown fields.
        if len(payload) > 65536:
            raise ValueError("Checkpoint envelope too large")
        value = json.loads(payload.decode("ascii"))
        if type(value) is not dict or set(value) != {"type", "body"}:
            raise ValueError("Invalid checkpoint envelope")
        if value["type"] not in ("msgpack", "json", "bytes", "null"):
            raise ValueError("Unsupported checkpoint serializer")
        blob = base64.b64decode(value["body"], validate=True)
        return self.serde.loads_typed((value["type"], blob))

    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        tid, cid = self._ids(config)
        stored = await self.repo.load(owner_id=self.owner_id, thread_id=tid,
                                      checkpoint_id=cid)
        if stored is None:
            return None
        checkpoint, metadata = self._unpack(stored.payload)
        if not isinstance(checkpoint, dict) or not isinstance(metadata, dict):
            raise ValueError("Corrupt checkpoint value")
        key = {"configurable": {"thread_id": tid,
                                "checkpoint_ns": "",
                                "checkpoint_id": stored.checkpoint_id}}
        parent = ({"configurable": {"thread_id": tid, "checkpoint_ns": "",
                                   "checkpoint_id": stored.parent_checkpoint_id}}
                  if stored.parent_checkpoint_id else None)
        written = await self.repo.load_writes(owner_id=self.owner_id,
            thread_id=tid, checkpoint_id=stored.checkpoint_id)
        pending = [(task_id, channel, self._unpack(payload))
                   for task_id, _idx, channel, payload in written]
        return CheckpointTuple(key, checkpoint, metadata, parent, pending)

    async def aput(self, config: dict, checkpoint: dict, metadata: dict,
                   new_versions: dict) -> dict:
        tid, parent = self._ids(config)
        cid = str(checkpoint["id"])
        await self.repo.save(owner_id=self.owner_id, thread_id=tid,
            checkpoint_id=cid, parent_checkpoint_id=parent,
            payload=self._pack((checkpoint, metadata)))
        return {"configurable": {"thread_id": tid, "checkpoint_ns": "",
                                  "checkpoint_id": cid}}

    async def aput_writes(self, config: dict, writes: list[tuple[str, Any]],
                          task_id: str, task_path: str = "") -> None:
        tid, cid = self._ids(config, require_checkpoint=True)
        if not writes:
            return
        payload = [(WRITES_IDX_MAP.get(channel, idx), channel, self._pack(value))
                   for idx, (channel, value) in enumerate(writes)]
        await self.repo.save_writes(owner_id=self.owner_id, thread_id=tid,
            checkpoint_id=cid, task_id=task_id, task_path=task_path, writes=payload)

    async def alist(self, config: dict | None, *, filter: dict | None = None,
                    before: dict | None = None,
                    limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            raise ValueError("Owner-scoped listing requires a thread")
        tid, _ = self._ids(config)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 50):
            raise ValueError("Invalid checkpoint list limit")
        before_id = self._ids(before)[1] if before else None
        ids = await self.repo.list_ids(owner_id=self.owner_id, thread_id=tid,
                                       limit=50)
        count = 0
        for cid in ids:
            if before_id and cid >= before_id:
                continue
            result = await self.aget_tuple({"configurable": {"thread_id": tid,
                                      "checkpoint_id": cid}})
            if result is None or (filter and any(result.metadata.get(k) != v
                                                 for k, v in filter.items())):
                continue
            yield result
            count += 1
            if limit is not None and count >= limit:
                break

    async def adelete_thread(self, thread_id: str) -> None:
        tid = uuid.UUID(str(thread_id))
        async with self.repo.conn.transaction():
            await self.repo.conn.fetchval(
                "SELECT set_config('reign.actor_id',$1,true)", self.owner_id)
            await self.repo.conn.execute(
                "DELETE FROM reign.conversations WHERE thread_id=$1 AND owner_id=$2",
                tid, self.owner_id)
