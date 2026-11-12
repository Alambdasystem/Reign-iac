"""Versioned, non-enforcing Genesis guardrail draft catalog.

The actual REIGN MCP/Computer gateway continues enforcing its own existing
allowlist. These policy drafts are simulations ONLY and cannot authorize a tool,
agent, destination, credential, external provider or execution.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit

from reign.genesis_pilot import ALLOWED
from reign.tool_contracts import CATALOG

_ID = re.compile(r"[a-z][a-z0-9-]{1,62}\Z")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
MAX_POLICIES = 40
MAX_HOSTS = 20


class PolicyMissing(Exception):
    pass


class PolicyConflict(Exception):
    pass


def validate_policy(name, allowed_tools, blocked_tools, allowed_hosts):
    if type(name) is not str or not 1 <= len(name.strip()) <= 120 or "\x00" in name:
        raise ValueError("Invalid policy name")
    if any(type(v) is not list for v in (allowed_tools, blocked_tools, allowed_hosts)):
        raise ValueError("Policy values must be lists")
    if len(allowed_tools) > len(ALLOWED) or len(blocked_tools) > len(CATALOG) or len(allowed_hosts) > MAX_HOSTS:
        raise ValueError("Policy list too large")
    if any(type(v) is not str for items in (allowed_tools, blocked_tools, allowed_hosts) for v in items):
        raise ValueError("Invalid policy value")
    allow, deny, hosts = set(allowed_tools), set(blocked_tools), set(allowed_hosts)
    if len(allow) != len(allowed_tools) or len(deny) != len(blocked_tools) or len(hosts) != len(allowed_hosts):
        raise ValueError("Duplicate policy entries")
    if not allow.issubset(ALLOWED) or not deny.issubset(CATALOG) or allow.intersection(deny):
        raise ValueError("Policy cannot grant actions or conflicting permissions")
    if any(not _HOST.fullmatch(host) for host in hosts):
        raise ValueError("Exact DNS hostnames required")
    return {"name": name.strip(), "allowed_tools": sorted(allow),
            "blocked_tools": sorted(deny), "allowed_hosts": sorted(hosts)}


class GenesisPolicies:
    def __init__(self, pilot):
        self.pilot = pilot
        with closing(pilot._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS policy_drafts(
              policy_id TEXT PRIMARY KEY,
              revision INTEGER NOT NULL CHECK(revision >= 1),
              payload TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status='draft'))""")
            db.execute("""CREATE TABLE IF NOT EXISTS policy_audit(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              policy_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              action TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')))""")

    def create(self, policy_id, name, allowed_tools, blocked_tools, allowed_hosts):
        if type(policy_id) is not str or not _ID.fullmatch(policy_id):
            raise ValueError("Invalid policy identifier")
        validated = validate_policy(name, allowed_tools, blocked_tools, allowed_hosts)
        entry = {"policy_id": policy_id, "revision": 1, "status": "draft",
                 "enforced": False, "assigned": False, **validated}
        with closing(self.pilot._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM policy_drafts").fetchone()[0] >= MAX_POLICIES:
                raise ValueError("Policy registry full")
            try:
                db.execute("INSERT INTO policy_drafts VALUES (?,?,?,'draft')",
                           (policy_id, 1, json.dumps(entry, sort_keys=True)))
            except sqlite3.IntegrityError:
                raise PolicyConflict("Policy already exists") from None
            db.execute("INSERT INTO policy_audit(policy_id,revision,action) "
                       "VALUES (?,1,'created')", (policy_id,))
        return entry

    def get(self, policy_id):
        if type(policy_id) is not str or not _ID.fullmatch(policy_id):
            raise ValueError("Invalid policy identifier")
        with closing(self.pilot._connect()) as db:
            row = db.execute("SELECT payload FROM policy_drafts WHERE policy_id=?",
                             (policy_id,)).fetchone()
        if row is None:
            raise PolicyMissing("Policy not found")
        return json.loads(row[0])

    def list(self):
        with closing(self.pilot._connect()) as db:
            rows = db.execute("SELECT payload FROM policy_drafts ORDER BY policy_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, policy_id, name, allowed_tools, blocked_tools, allowed_hosts, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Invalid revision")
        validated = validate_policy(name, allowed_tools, blocked_tools, allowed_hosts)
        with closing(self.pilot._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,revision FROM policy_drafts WHERE policy_id=?",
                             (policy_id,)).fetchone()
            if row is None:
                raise PolicyMissing("Policy not found")
            previous, revision = json.loads(row[0]), row[1]
            if revision != expected_revision:
                raise PolicyConflict("Policy revision changed")
            if previous["status"] != "draft" or previous["enforced"]:
                raise ValueError("Only unassigned draft policies may be edited")
            updated = {**previous, **validated, "revision": revision + 1}
            db.execute("UPDATE policy_drafts SET revision=?,payload=? WHERE policy_id=?",
                       (revision + 1, json.dumps(updated, sort_keys=True), policy_id))
            db.execute("INSERT INTO policy_audit(policy_id,revision,action) "
                       "VALUES (?,?,'updated')", (policy_id, revision + 1))
        return updated

    def simulate(self, policy_id, tool, url=None):
        """A decision preview, never a signed authorization or a tool dispatch."""
        policy = self.get(policy_id)
        if type(tool) is not str or tool not in CATALOG:
            raise ValueError("Unknown tool")
        allowed = tool in policy["allowed_tools"] and tool not in policy["blocked_tools"]
        reason = "draft_tool_allowed" if allowed else "draft_tool_denied"
        if url is not None:
            if type(url) is not str:
                raise ValueError("Invalid URL")
            try:
                u = urlsplit(url)
                host_allowed = (u.scheme == "https" and u.hostname in policy["allowed_hosts"]
                                and u.port in (None,443) and not u.username and
                                not u.password and not u.fragment)
            except ValueError:
                host_allowed = False
            if not host_allowed:
                allowed, reason = False, "draft_destination_denied"
        return {"policy_id": policy_id, "revision": policy["revision"],
                "allowed": allowed, "reason": reason, "enforced": False,
                "simulation_only": True}

    def audit(self, policy_id):
        self.get(policy_id)
        with closing(self.pilot._connect()) as db:
            rows = db.execute("SELECT revision,action,created_at FROM policy_audit "
                              "WHERE policy_id=? ORDER BY event_id DESC LIMIT 50",
                              (policy_id,)).fetchall()
        return [{"revision": r, "action": a, "created_at": t} for r,a,t in rows]
