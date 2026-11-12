"""Single-administrator Genesis draft registry on existing REIGN Core disk.

No provider API, workload credential, executable grant or agent provisioning.
This module is deliberately NOT an agent execution gateway.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from reign.genesis_registration import GenesisRequest, GenesisRegistration
from reign.sovereign_policy import Constitution
from reign.sovereign_registry import AgentRegistry, RegistryDenied

MOUNT = "/var/data/reign-vault"
FILE = "genesis-drafts.sqlite3"
TENANT = "pilot-tenant"
OWNER = "pilot-owner"
ALLOWED = frozenset({"workspace_status", "browser_status", "browser_observe"})
MAX_AGENTS = 40


class DraftMissing(Exception):
    """The requested draft is absent."""


class DraftConflict(Exception):
    """A caller supplied a stale expected revision."""


class GenesisPilot:
    def __init__(self, path: str):
        self.path = path
        self.constitution = Constitution(
            version="pilot-v1", allowed_tools=ALLOWED,
            blocked_tools=frozenset(), allowed_hosts=frozenset({"alambda-forge.onrender.com"}))
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_drafts (
              agent_id TEXT PRIMARY KEY,
              metadata TEXT NOT NULL CHECK (length(metadata) BETWEEN 1 AND 4096),
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_audit (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id TEXT NOT NULL,
              action TEXT NOT NULL,
              revision INTEGER NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")

    @classmethod
    def from_env(cls):
        if os.getenv("GENESIS_DRAFT_MODE") != "enabled":
            raise RuntimeError("Genesis pilot not enabled")
        if os.getenv("REIGN_VAULT_DIR") != MOUNT or os.path.realpath(MOUNT) != MOUNT:
            raise RuntimeError("Verified private Core disk required")
        if not os.path.ismount(MOUNT):
            raise RuntimeError("Verified Core mount missing")
        mode = os.stat(MOUNT).st_mode
        if not stat.S_ISDIR(mode) or (mode & 0o077) or not os.access(MOUNT, os.W_OK):
            raise RuntimeError("Private mount not writable")
        return cls(str(Path(MOUNT) / FILE))

    def _connect(self):
        parent = Path(self.path).parent
        if not parent.is_dir():
            raise RuntimeError("Genesis disk unavailable")
        if os.path.lexists(self.path):
            info = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise RuntimeError("Unsafe Genesis registry file")
        else:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            except FileExistsError:
                raise RuntimeError("Concurrent Genesis initialization") from None
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _request(self, agent_id: str, name: str, capabilities: list[str]):
        if type(capabilities) is not list or len(capabilities) > 12:
            raise ValueError("Invalid requested capabilities")
        if any(type(c) is not str for c in capabilities):
            raise ValueError("Invalid capability identifier")
        return GenesisRequest(agent_id=agent_id, name=name, harness="langgraph",
                              provider="render", capabilities=frozenset(capabilities))

    def preview(self, agent_id: str, name: str, capabilities: list[str]) -> dict:
        proposal = self._request(agent_id, name, capabilities)
        draft = GenesisRegistration(AgentRegistry(), self.constitution)
        result = draft.preview(proposal, tenant_id=TENANT, owner_id=OWNER, principal_id=OWNER)
        # The in-memory registry is intentionally short-lived. The persistent
        # draft database is authoritative for duplicate registration previews.
        # This check improves previews; create_draft still owns the atomic
        # uniqueness check to prevent races between preview and commit.
        with closing(self._connect()) as db:
            exists = db.execute(
                "SELECT 1 FROM agent_drafts WHERE agent_id = ? LIMIT 1",
                (proposal.agent_id,)).fetchone()
            count = db.execute("SELECT count(*) FROM agent_drafts").fetchone()[0]
        if exists:
            raise RegistryDenied("Draft already registered")
        if count >= MAX_AGENTS:
            raise RegistryDenied("Genesis draft registry is full")
        return asdict(result)

    def create_draft(self, agent_id: str, name: str, capabilities: list[str]) -> dict:
        request = self._request(agent_id, name, capabilities)
        reg = GenesisRegistration(AgentRegistry(), self.constitution)
        record = reg.register_draft(request, tenant_id=TENANT, owner_id=OWNER,
                                    principal_id=OWNER)
        data = {"agent_id": record.agent_id, "name": record.name,
                "revision": 1, "state": "draft", "provider": record.provider, "harness": record.harness,
                "constitution_version": record.constitution_version,
                "capabilities": sorted(record.capabilities), "provisioned": False}
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM agent_drafts").fetchone()[0]
            if count >= MAX_AGENTS:
                raise RegistryDenied("Genesis draft registry is full")
            try:
                db.execute("INSERT INTO agent_drafts(agent_id,metadata) VALUES (?,?)",
                           (record.agent_id, json.dumps(data, sort_keys=True)))
            except sqlite3.IntegrityError:
                raise RegistryDenied("Draft already registered") from None
            db.execute("INSERT INTO agent_audit(agent_id,action,revision) VALUES (?, 'created', 1)",
                       (record.agent_id,))
        return data

    def get_draft(self, agent_id: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT metadata FROM agent_drafts WHERE agent_id = ?",
                             (agent_id,)).fetchone()
        if row is None:
            raise DraftMissing("Draft not found")
        result = json.loads(row[0])
        result.setdefault("revision", 1)  # Older pilot records.
        return result

    @staticmethod
    def _expected_revision(value):
        if type(value) is not int or value < 1:
            raise ValueError("Expected positive integer revision")
        return value

    def update_draft(self, agent_id: str, name: str, capabilities: list[str],
                     *, expected_revision: int) -> dict:
        self._expected_revision(expected_revision)
        # Validate the proposed configuration against the existing deny-by-default
        # constitution before opening the SQLite write transaction.
        request = self._request(agent_id, name, capabilities)
        record = GenesisRegistration(AgentRegistry(), self.constitution).register_draft(
            request, tenant_id=TENANT, owner_id=OWNER, principal_id=OWNER)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT metadata FROM agent_drafts WHERE agent_id = ?",
                             (agent_id,)).fetchone()
            if row is None:
                raise DraftMissing("Draft not found")
            previous = json.loads(row[0])
            revision = previous.get("revision", 1)
            if revision != expected_revision:
                raise DraftConflict("Draft revision has changed")
            if previous.get("state") != "draft" or previous.get("provisioned") is not False:
                raise RegistryDenied("Only unprovisioned drafts may be modified")
            updated = {**previous, "name": record.name, "capabilities": sorted(record.capabilities),
                       "revision": revision + 1}
            db.execute("UPDATE agent_drafts SET metadata=? WHERE agent_id=?",
                       (json.dumps(updated, sort_keys=True), agent_id))
            db.execute("INSERT INTO agent_audit(agent_id,action,revision) VALUES (?, 'updated', ?)",
                       (agent_id, revision + 1))
        return updated

    def delete_draft(self, agent_id: str, *, expected_revision: int) -> dict:
        self._expected_revision(expected_revision)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT metadata FROM agent_drafts WHERE agent_id = ?",
                             (agent_id,)).fetchone()
            if row is None:
                raise DraftMissing("Draft not found")
            previous = json.loads(row[0])
            revision = previous.get("revision", 1)
            if revision != expected_revision:
                raise DraftConflict("Draft revision has changed")
            if previous.get("state") != "draft" or previous.get("provisioned") is not False:
                raise RegistryDenied("Only unprovisioned drafts may be deleted")
            db.execute("DELETE FROM agent_drafts WHERE agent_id=?", (agent_id,))
            # Optional newer Genesis Studio catalog, created only when enabled.
            # Delete assignments atomically so a later draft reusing this ID
            # cannot inherit the old agent's graph permissions.
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_assignments'"
            ).fetchone()
            if exists:
                db.execute("DELETE FROM graph_assignments WHERE agent_id=?", (agent_id,))
            db.execute("INSERT INTO agent_audit(agent_id,action,revision) VALUES (?, 'deleted', ?)",
                       (agent_id, revision + 1))
        return {"agent_id": agent_id, "deleted": True, "revision": revision + 1}

    def clone_draft(self, agent_id: str, new_agent_id: str, *,
                    expected_revision: int) -> dict:
        self._expected_revision(expected_revision)
        source = self.get_draft(agent_id)
        if source["revision"] != expected_revision:
            raise DraftConflict("Draft revision has changed")
        if source.get("state") != "draft" or source.get("provisioned") is not False:
            raise RegistryDenied("Only unprovisioned drafts may be cloned")
        # This only copies validated non-secret draft metadata. It never copies
        # runtime tokens, provider credentials, grants or execution state.
        return self.create_draft(new_agent_id, source["name"] + " (copy)",
                                 list(source["capabilities"]))

    def audit_events(self, agent_id: str, *, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid audit limit")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT event_id, action, revision, created_at FROM agent_audit "
                "WHERE agent_id=? ORDER BY event_id DESC LIMIT ?",
                (agent_id, limit)).fetchall()
        return [{"event_id": event, "action": action, "revision": rev, "created_at": time}
                for event, action, rev, time in rows]

    def list_drafts(self) -> list[dict]:
        with closing(self._connect()) as db, db:
            records = db.execute("SELECT metadata FROM agent_drafts ORDER BY agent_id LIMIT 40").fetchall()
        result = [json.loads(row[0]) for row in records]
        for draft in result:
            draft.setdefault("revision", 1)
        return result

    def status(self) -> dict:
        return {"mode": "draft_only", "provider_provisioning": False,
                "execution": "disabled", "policy_version": self.constitution.version,
                "allowed_capabilities": sorted(ALLOWED),
                "draft_count": len(self.list_drafts())}
