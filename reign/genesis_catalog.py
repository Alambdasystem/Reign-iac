"""Versioned Genesis graph and knowledge-base *definitions* on private Core disk.

This is a non-executing control-plane catalog. Neither graphs nor knowledge bases
can invoke tools, ingest data or provision providers. A separate policy-enforcing
runtime/identity design and review is mandatory before activation.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing

IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{1,62}\Z")
NODE_TYPES = frozenset({"start", "end", "knowledge_agent", "action_agent",
                         "kb_search", "human_approval", "decision"})
MAX_NODES = 40
MAX_EDGES = 80


class CatalogConflict(Exception):
    pass


class CatalogMissing(Exception):
    pass


def _id(value):
    if type(value) is not str or IDENTIFIER.fullmatch(value) is None:
        raise ValueError("Invalid identifier")
    return value


def _name(value):
    if type(value) is not str or not 1 <= len(value.strip()) <= 120 or "\x00" in value:
        raise ValueError("Invalid name")
    return value.strip()


def validate_graph(spec):
    """Strict typed DAG. Executable semantics deliberately unsupported."""
    if type(spec) is not dict or set(spec) != {"nodes", "edges"}:
        raise ValueError("Invalid graph shape")
    nodes, edges = spec["nodes"], spec["edges"]
    if type(nodes) is not list or not 2 <= len(nodes) <= MAX_NODES:
        raise ValueError("Invalid node count")
    if type(edges) is not list or len(edges) > MAX_EDGES:
        raise ValueError("Invalid edge count")
    kinds = {}
    for node in nodes:
        if type(node) is not dict or set(node) != {"id", "type", "ref"}:
            raise ValueError("Invalid node")
        key = _id(node["id"])
        kind = node["type"]
        ref = node["ref"]
        if type(kind) is not str:
            raise ValueError("Invalid node type")
        if key in kinds or kind not in NODE_TYPES:
            raise ValueError("Duplicate node or unknown type")
        if kind in {"knowledge_agent", "action_agent", "kb_search"}:
            _id(ref)
        elif ref is not None:
            raise ValueError("Unexpected node reference")
        kinds[key] = kind
    starts = [key for key, kind in kinds.items() if kind == "start"]
    ends = [key for key, kind in kinds.items() if kind == "end"]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Exactly one start and one end required")
    outgoing = {key: set() for key in kinds}
    indegree = {key: 0 for key in kinds}
    for edge in edges:
        if type(edge) is not dict or set(edge) != {"from", "to"}:
            raise ValueError("Invalid edge")
        a, b = edge["from"], edge["to"]
        if type(a) is not str or type(b) is not str:
            raise ValueError("Invalid edge reference")
        if a not in kinds or b not in kinds or a == b or b in outgoing[a]:
            raise ValueError("Invalid, duplicate or cyclic edge")
        if kinds[a] == "end" or kinds[b] == "start":
            raise ValueError("Invalid start/end direction")
        outgoing[a].add(b)
        indegree[b] += 1
    incoming = {key: count for key, count in indegree.items()}
    ready = sorted(key for key, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for next_node in sorted(outgoing[node]):
            indegree[next_node] -= 1
            if indegree[next_node] == 0:
                ready.append(next_node)
    if visited != len(kinds):
        raise ValueError("Graph contains a cycle")
    if any((key != starts[0] and incoming[key] == 0) or
           (key != ends[0] and not outgoing[key]) for key in kinds):
        raise ValueError("Disconnected graph node")
    reachable = {starts[0]}
    frontier = [starts[0]]
    while frontier:
        for next_node in outgoing[frontier.pop()]:
            if next_node not in reachable:
                reachable.add(next_node)
                frontier.append(next_node)
    if len(reachable) != len(kinds):
        raise ValueError("Unreachable graph node")
    return {"nodes": nodes, "edges": edges}


class GenesisCatalog:
    """Use the *same* verified private Genesis SQLite file, never a fallback DB."""

    def __init__(self, genesis_pilot):
        self.pilot = genesis_pilot
        with closing(self.pilot._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_definitions(
                kb_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                revision INTEGER NOT NULL, definition TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='draft'))""")
            db.execute("""CREATE TABLE IF NOT EXISTS graph_definitions(
                graph_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                revision INTEGER NOT NULL, spec TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='draft'))""")
            db.execute("""CREATE TABLE IF NOT EXISTS graph_assignments(
                graph_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                PRIMARY KEY(graph_id,agent_id))""")

    def create_kb(self, kb_id, name):
        kb_id, name = _id(kb_id), _name(name)
        entry = {"kb_id": kb_id, "name": name, "revision": 1,
                 "status": "draft", "ingestion": "not_configured",
                 "queryable": False, "documents": 0}
        with closing(self.pilot._connect()) as db, db:
            try:
                db.execute("INSERT INTO knowledge_definitions VALUES (?,?,?,?, 'draft')",
                           (kb_id, name, 1, json.dumps(entry, sort_keys=True)))
            except sqlite3.IntegrityError:
                raise CatalogConflict("Knowledge base already exists") from None
        return entry

    def list_kbs(self):
        with closing(self.pilot._connect()) as db:
            rows = db.execute("SELECT definition FROM knowledge_definitions ORDER BY kb_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def _verify_refs(self, spec):
        for node in spec["nodes"]:
            kind, ref = node["type"], node["ref"]
            if kind in {"knowledge_agent", "action_agent"}:
                agent = self.pilot.get_draft(ref)
                if agent.get("state") != "draft" or agent.get("provisioned") is not False:
                    raise ValueError("Only safe draft agent references")
            elif kind == "kb_search":
                with closing(self.pilot._connect()) as db:
                    exists = db.execute(
                        "SELECT 1 FROM knowledge_definitions WHERE kb_id=?", (ref,)
                    ).fetchone()
                if not exists:
                    raise CatalogMissing("Unknown knowledge base")

    def create_graph(self, graph_id, name, spec):
        graph_id, name = _id(graph_id), _name(name)
        spec = validate_graph(spec)
        self._verify_refs(spec)
        entry = {"graph_id": graph_id, "name": name, "revision": 1,
                 "status": "draft", "executable": False, "spec": spec}
        with closing(self.pilot._connect()) as db, db:
            try:
                db.execute("INSERT INTO graph_definitions VALUES (?,?,?,?, 'draft')",
                           (graph_id, name, 1, json.dumps(entry, sort_keys=True)))
            except sqlite3.IntegrityError:
                raise CatalogConflict("Graph already exists") from None
        return entry

    def list_graphs(self):
        with closing(self.pilot._connect()) as db:
            rows = db.execute("SELECT spec FROM graph_definitions ORDER BY graph_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def assign_draft(self, graph_id, agent_id):
        graph_id, agent_id = _id(graph_id), _id(agent_id)
        agent = self.pilot.get_draft(agent_id)
        if agent.get("state") != "draft" or agent.get("provisioned") is not False:
            raise ValueError("Only unprovisioned drafts can be assigned")
        with closing(self.pilot._connect()) as db, db:
            graph = db.execute("SELECT 1 FROM graph_definitions WHERE graph_id=?",
                               (graph_id,)).fetchone()
            if graph is None:
                raise CatalogMissing("Unknown graph")
            db.execute("INSERT OR IGNORE INTO graph_assignments VALUES (?,?)",
                       (graph_id, agent_id))
        return {"agent_id": agent_id, "graph_id": graph_id,
                "status": "draft", "executable": False}

    def workflows_for_agent(self, agent_id):
        _id(agent_id)
        self.pilot.get_draft(agent_id)
        with closing(self.pilot._connect()) as db:
            rows = db.execute(
                "SELECT g.spec FROM graph_definitions g "
                "JOIN graph_assignments a ON a.graph_id=g.graph_id "
                "WHERE a.agent_id=? ORDER BY g.graph_id", (agent_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def run(self, *_args, **_kwargs):
        raise PermissionError("Catalog is definition-only; execution gateway not enabled")
