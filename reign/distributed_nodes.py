"""Restricted distributed-node inventory, heartbeat authentication and routing preview.

Single-admin REIGN pilot only. The registered nodes are metadata, NOT live
agents, provider adapters, remote shell endpoints or dispatch authorities.
No cloud provisioning or tool execution is performed by this module.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

MOUNT = "/var/data/reign-vault"
FILENAME = "reign-nodes.sqlite3"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
KINDS = frozenset({"render", "aws", "azure", "onprem", "edge"})
CAPABILITIES = frozenset({
    "browser.observe", "browser.action", "model.inference",
    "knowledge.retrieve", "workflow.execute",
})
MAX_NODES = 40
HEARTBEAT_TTL_SECONDS = 90


class NodeDenied(PermissionError):
    """Uniform rejection without disclosing enrollment state or secrets."""


def _id(value: str) -> str:
    if type(value) is not str or not ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid node ID")
    return value


def _capabilities(values) -> tuple[str, ...]:
    if type(values) is not list or len(values) > 12 or any(
            type(x) is not str or x not in CAPABILITIES for x in values):
        raise ValueError("Unsupported node capabilities")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate node capabilities")
    return tuple(sorted(values))


class NodeRegistry:
    def __init__(self, path: str, *, clock=None):
        self.path = path
        self.clock = clock or time.time
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                token_hash BLOB NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_seen INTEGER,
                created_at INTEGER NOT NULL
            )""")

    @classmethod
    def from_env(cls):
        if os.getenv("REIGN_NODES_MODE") != "inventory":
            raise RuntimeError("Node inventory is not enabled")
        if (os.getenv("REIGN_VAULT_DIR") != MOUNT or
                os.path.realpath(MOUNT) != MOUNT or not os.path.ismount(MOUNT)):
            raise RuntimeError("Verified private Core disk required")
        st = os.stat(MOUNT)
        if not stat.S_ISDIR(st.st_mode) or (st.st_mode & 0o077) or not os.access(MOUNT, os.W_OK):
            raise RuntimeError("Private Core disk not writable")
        return cls(str(Path(MOUNT) / FILENAME))

    def _connect(self):
        if not Path(self.path).parent.is_dir():
            raise RuntimeError("Node storage unavailable")
        if os.path.lexists(self.path):
            st = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_nlink != 1:
                raise RuntimeError("Unsafe node registry file")
        else:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            except FileExistsError:
                raise RuntimeError("Concurrent registry initialization") from None
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _now(self) -> int:
        now = self.clock()
        if not isinstance(now, (int, float)) or not 0 <= now < 253402300799:
            raise RuntimeError("Invalid clock")
        return int(now)

    @staticmethod
    def _digest(token: str) -> bytes:
        if type(token) is not str or len(token) != 64 or any(
                c not in "0123456789abcdef" for c in token):
            raise NodeDenied("Node authentication denied")
        return hashlib.sha256(bytes.fromhex(token)).digest()

    def enroll(self, node_id: str, kind: str, label: str, capabilities: list[str]) -> dict:
        _id(node_id)
        if type(kind) is not str or kind not in KINDS:
            raise ValueError("Unsupported node kind")
        if type(label) is not str or not (1 <= len(label.strip()) <= 120):
            raise ValueError("Invalid node label")
        scopes = _capabilities(capabilities)
        token = secrets.token_hex(32)
        digest = self._digest(token)
        import json
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] >= MAX_NODES:
                raise NodeDenied("Node registry capacity reached")
            try:
                db.execute("INSERT INTO nodes "
                           "(node_id,kind,label,capabilities,token_hash,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (node_id, kind, label.strip(), json.dumps(scopes), digest, self._now()))
            except sqlite3.IntegrityError:
                raise NodeDenied("Node enrollment denied") from None
        # Issued once. Only the trusted admin API may return this token.
        return {"node_id": node_id, "enrollment_token": token,
                "status": "awaiting_heartbeat", "dispatch_enabled": False}

    def heartbeat(self, node_id: str, token: str) -> dict:
        node_id = _id(node_id)
        digest = self._digest(token)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT token_hash, enabled FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if row is None or not row[1] or not secrets.compare_digest(digest, row[0]):
                raise NodeDenied("Node authentication denied")
            seen = self._now()
            db.execute("UPDATE nodes SET last_seen=? WHERE node_id=?", (seen, node_id))
        return {"node_id": node_id, "accepted": True, "dispatch_enabled": False}

    def list_nodes(self) -> list[dict]:
        import json
        now = self._now()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT node_id,kind,label,capabilities,enabled,last_seen "
                "FROM nodes ORDER BY node_id LIMIT ?", (MAX_NODES,)
            ).fetchall()
        result = []
        for node_id, kind, label, values, enabled, seen in rows:
            online = bool(enabled and seen is not None and 0 <= now - seen <= HEARTBEAT_TTL_SECONDS)
            result.append({
                "node_id": node_id, "kind": kind, "label": label,
                "capabilities": json.loads(values),
                "status": "disabled" if not enabled else "online" if online else "offline",
                "last_seen": seen, "dispatch_enabled": False,
            })
        return result

    def route_preview(self, capability: str) -> dict:
        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("Unsupported route capability")
        nodes = [n for n in self.list_nodes()
                 if n["status"] == "online" and capability in n["capabilities"]]
        return {
            "capability": capability,
            "candidate_nodes": [n["node_id"] for n in nodes],
            "eligible_count": len(nodes),
            "dispatch_enabled": False,
            "reason": "routing_preview_only_no_execution",
        }

    def disable(self, node_id: str) -> dict:
        node_id = _id(node_id)
        with closing(self._connect()) as db, db:
            changed = db.execute("UPDATE nodes SET enabled=0 WHERE node_id=?", (node_id,))
            if not changed.rowcount:
                raise NodeDenied("Node not found")
        return {"node_id": node_id, "status": "disabled", "dispatch_enabled": False}
