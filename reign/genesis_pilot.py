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
                "state": "draft", "provider": record.provider, "harness": record.harness,
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
        return data

    def list_drafts(self) -> list[dict]:
        with closing(self._connect()) as db, db:
            records = db.execute("SELECT metadata FROM agent_drafts ORDER BY agent_id LIMIT 40").fetchall()
        return [json.loads(row[0]) for row in records]

    def status(self) -> dict:
        return {"mode": "draft_only", "provider_provisioning": False,
                "execution": "disabled", "policy_version": self.constitution.version,
                "allowed_capabilities": sorted(ALLOWED),
                "draft_count": len(self.list_drafts())}
