"""Owner-restricted, sanitized trace span sink for a future REIGN release.

Requires reviewed RLS policies and dedicated REIGN database credentials.
Not connected to the pilot until migration and runtime identity checks pass.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from reign.telemetry import SERVICES, OPERATIONS, OUTCOMES, TOOLS, ERROR_CODES
from reign.history_store import _owner


class TraceRepository:
    def __init__(self, connection):
        self.conn = connection

    async def record(self, *, owner_id: str, span: dict, thread_id: str | None = None):
        required = {"trace_id", "span_id", "parent_span_id", "service", "operation",
                    "outcome", "tool_name", "duration_ms", "input_tokens",
                    "output_tokens", "error_code", "recorded_at"}
        if type(span) is not dict or set(span) != required:
            raise ValueError("Unrecognized trace metadata")
        if (span["service"] not in SERVICES or span["operation"] not in OPERATIONS
                or span["outcome"] not in OUTCOMES):
            raise ValueError("Invalid trace classification")
        if span["tool_name"] is not None and span["tool_name"] not in TOOLS:
            raise ValueError("Invalid trace tool")
        if span["error_code"] is not None and span["error_code"] not in ERROR_CODES:
            raise ValueError("Invalid trace error code")
        if type(span["duration_ms"]) is not int or span["duration_ms"] < 0:
            raise ValueError("Invalid trace latency")
        for count in (span["input_tokens"], span["output_tokens"]):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Invalid token usage")
        sid = span["span_id"]
        parent = span["parent_span_id"]
        for value in (sid, parent):
            if value is not None and (
                    type(value) is not str or len(value) != 16
                    or any(c not in "0123456789abcdef" for c in value)):
                raise ValueError("Invalid trace span identity")
        started = datetime.fromisoformat(span["recorded_at"])
        if started.tzinfo is None:
            raise ValueError("Trace timestamp must be timezone-aware")
        trace = UUID(span["trace_id"])
        thread = UUID(thread_id) if thread_id else None
        owner = _owner(owner_id)
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id', $1, true)", owner)
            await self.conn.execute("""
                INSERT INTO reign.trace_spans
                  (trace_id,span_id,parent_span_id,owner_id,thread_id,service,
                   operation,tool_name,outcome,duration_ms,input_tokens,
                   output_tokens,error_code,started_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """, trace, sid, parent, owner, thread, span["service"], span["operation"],
                span["tool_name"], span["outcome"], span["duration_ms"],
                span["input_tokens"], span["output_tokens"], span["error_code"], started)

    async def recent(self, owner_id: str, *, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not (1 <= limit <= 50):
            raise ValueError("Invalid limit")
        owner = _owner(owner_id)
        async with self.conn.transaction():
            await self.conn.fetchval("SELECT set_config('reign.actor_id', $1, true)", owner)
            rows = await self.conn.fetch("""
                SELECT trace_id,span_id,parent_span_id,thread_id,service,operation,
                       tool_name,outcome,duration_ms,input_tokens,output_tokens,
                       error_code,started_at
                  FROM reign.trace_spans WHERE owner_id=$1
                  ORDER BY started_at DESC LIMIT $2
            """, owner, limit)
        return [{k: str(row[k]) if k in ("trace_id", "thread_id") and row[k] else
                 row[k].isoformat() if k == "started_at" else row[k]
                 for k in row.keys()} for row in rows]
