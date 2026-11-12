"""Safe graph/knowledge metadata acceptance; no graph execution or vector provisioning."""
import tempfile
import unittest
from pathlib import Path

from reign.genesis_pilot import GenesisPilot, DraftMissing
from reign.genesis_catalog import (
    GenesisCatalog, CatalogConflict, CatalogMissing, validate_graph,
)


def graph(*nodes, edges):
    return {"nodes": [{"id": i, "type": kind, "ref": ref} for i, kind, ref in nodes],
            "edges": [{"from": a, "to": b} for a, b in edges]}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "genesis.sqlite3")
        self.pilot = GenesisPilot(self.path)
        self.pilot.create_draft("research-agent", "Research", ["workspace_status"])
        self.pilot.create_draft("action-agent", "Actions", [])
        self.catalog = GenesisCatalog(self.pilot)

    def tearDown(self):
        self.tmp.cleanup()

    def valid(self):
        return graph(("begin", "start", None), ("kb", "kb_search", "kb-one"),
                     ("analyst", "knowledge_agent", "research-agent"),
                     ("finish", "end", None),
                     edges=[("begin", "kb"), ("kb", "analyst"),
                            ("analyst", "finish")])

    def test_kb_catalog_is_metadata_only_with_durable_revision(self):
        kb = self.catalog.create_kb("kb-one", "Operations Manual")
        self.assertEqual(kb["queryable"], False)
        self.assertEqual(kb["ingestion"], "not_configured")
        self.assertEqual(kb["documents"], 0)
        with self.assertRaises(CatalogConflict):
            self.catalog.create_kb("kb-one", "Duplicate")
        restarted = GenesisCatalog(GenesisPilot(self.path))
        self.assertEqual(restarted.list_kbs(), [kb])

    def test_graph_save_assign_reopen_and_never_execute(self):
        self.catalog.create_kb("kb-one", "Policies")
        original = self.valid()
        saved = self.catalog.create_graph("policy-review", "Policy Review", original)
        self.assertEqual(saved["spec"], original)
        self.assertFalse(saved["executable"])
        assigned = self.catalog.assign_draft("policy-review", "research-agent")
        self.assertFalse(assigned["executable"])
        self.assertEqual(GenesisCatalog(GenesisPilot(self.path)).
                         workflows_for_agent("research-agent"), [saved])
        self.assertEqual(self.catalog.workflows_for_agent("action-agent"), [])
        with self.assertRaises(PermissionError):
            self.catalog.run("policy-review")
        with self.assertRaises(CatalogConflict):
            self.catalog.create_graph("policy-review", "Conflict", original)

    def test_unknown_or_deleted_agent_and_unknown_kb_rejected(self):
        with self.assertRaises(CatalogMissing):
            self.catalog.create_graph("bad", "Unknown KB", self.valid())
        self.catalog.create_kb("kb-one", "Policies")
        with self.assertRaises(DraftMissing):
            self.catalog.assign_draft("missing-graph", "unknown-agent")
        with self.assertRaises(CatalogMissing):
            self.catalog.assign_draft("missing-graph", "research-agent")
        self.pilot.delete_draft("research-agent", expected_revision=1)
        with self.assertRaises(DraftMissing):
            self.catalog.create_graph("now-unknown", "Deleted agent", self.valid())

    def test_graph_spec_denies_code_unknown_nodes_cycles_and_unreachable(self):
        invalids = [
            graph(("begin", "start", None), ("unsafe", "python_exec", None),
                  ("finish", "end", None), edges=[("begin", "unsafe"), ("unsafe", "finish")]),
            graph(("begin", "start", None), ("finish", "end", None),
                  edges=[("begin", "finish"), ("finish", "begin")]),
            graph(("begin", "start", None), ("finish", "end", None), edges=[]),
            graph(("begin", "start", None), ("finish", "end", None),
                  edges=[("begin", "finish"), ("begin", "finish")]),
            graph(("begin", "start", None), ("step", "decision", None),
                  ("finish", "end", None),
                  edges=[("begin", "step"), ("step", "finish"), ("step", "step")]),
            {"nodes": [{"id": "begin", "type": "start", "ref": None, "python": "exec"}],
             "edges": []},
            graph(("begin", "start", None), ("finish", "end", None),
                  edges=[("begin", "finish"), ("begin", "outside")]),
        ]
        for sample in invalids:
            with self.subTest(spec=sample), self.assertRaises(ValueError):
                validate_graph(sample)

    def test_graph_spec_rejects_invalid_identifiers_and_untrusted_payload(self):
        self.catalog.create_kb("kb-one", "Policies")
        with self.assertRaises(ValueError):
            self.catalog.create_kb("../evil", "Bad")
        with self.assertRaises(ValueError):
            self.catalog.create_graph("bad-one", "Bad", {
                **self.valid(), "shell": "rm -rf /"})
        with self.assertRaises(ValueError):
            self.catalog.create_graph("bad-two", "Bad", graph(
                ("begin", "start", None), ("kb", "kb_search", "../escape"),
                ("finish", "end", None),
                edges=[("begin", "kb"), ("kb", "finish")]))
